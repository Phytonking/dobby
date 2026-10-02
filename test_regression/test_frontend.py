import sys
from pathlib import Path

import pytest
from playwright.async_api import async_playwright, expect

pytestmark = pytest.mark.asyncio
URL = "http://dashboard:3000"


async def finish(browser, page, name):
    if sys.exc_info()[0] is not None:
        artifacts = Path("/artifacts")
        artifacts.mkdir(exist_ok=True)
        await page.screenshot(path=str(artifacts / (name + ".png")), full_page=True)
    await browser.close()


async def test_10_frontend_dashboard_user_workflow(db):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await page.goto(URL + "/login")
            await page.get_by_label("Username").fill("admin")
            await page.get_by_label("Password", exact=True).fill("regression-password-only")
            await page.get_by_role("button", name="Sign in", exact=True).click()
            await expect(page).to_have_url(URL + "/dashboard")
            await page.get_by_label("Email", exact=True).fill("browser@example.org")
            with_response = page.expect_response(
                lambda r: r.url.endswith("/me") and r.request.method == "PATCH"
            )
            async with with_response as response:
                await page.get_by_role("button", name="Save", exact=True).click()
            assert (await response.value).status == 200
            await page.reload()
            await expect(page.get_by_label("Email", exact=True)).to_have_value("browser@example.org")
            await page.goto(URL + "/dashboard/admin/users")
            await expect(page.get_by_role("cell", name="member@example.org", exact=True)).to_be_visible()
            await page.goto(URL + "/dashboard/integrations")
            await expect(page.get_by_text("Instagram", exact=True)).to_be_visible()
            assert not errors
        finally:
            await finish(browser, page, sys._getframe().f_code.co_name)


async def test_19_frontend_escapes_user_content(db):
    from sqlalchemy import update
    from dashboard.models import User

    payload = '<img src=x onerror="window.injected=true">'
    async with db[0]() as session:
        await session.execute(update(User).where(User.id == db[1].id).values(display_name=payload))
        await session.commit()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        try:
            await page.context.add_cookies([{"name": "dobby_session", "value": db[3]["admin"], "url": URL}])
            await page.goto(URL + "/dashboard")
            await expect(page.get_by_role("heading", name="Welcome back, " + payload + ".")).to_be_visible()
            assert await page.evaluate("window.injected === undefined")
            assert await page.locator("img[onerror]").count() == 0
        finally:
            await finish(browser, page, sys._getframe().f_code.co_name)


async def test_29_frontend_api_unavailable_message():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        try:
            await page.route("**/auth/methods", lambda route: route.abort())
            await page.goto(URL + "/login")
            await expect(
                page.get_by_role("alert").filter(has_text="Cannot reach the dashboard API")
            ).to_be_visible(timeout=15000)
            await expect(page.get_by_role("button", name="Sign in", exact=True)).to_have_count(0)
            await page.unroute("**/auth/methods")
            await page.reload()
            await expect(page.get_by_label("Username")).to_be_visible()
        finally:
            await finish(browser, page, sys._getframe().f_code.co_name)


async def test_30_frontend_protected_routes_and_member_navigation(db):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        try:
            for path in ("/dashboard", "/dashboard/admin/users", "/dashboard/integrations"):
                await page.goto(URL + path)
                await expect(page).to_have_url(URL + "/login")
            await page.context.add_cookies([{"name": "dobby_session", "value": db[3]["student"], "url": URL}])
            await page.goto(URL + "/dashboard")
            await expect(page.get_by_label("Email", exact=True)).to_be_visible()
            await expect(page.get_by_role("link", name="Users", exact=True)).to_have_count(0)
            await expect(page.get_by_role("link", name="Service accounts", exact=True)).to_have_count(0)
        finally:
            await finish(browser, page, sys._getframe().f_code.co_name)
