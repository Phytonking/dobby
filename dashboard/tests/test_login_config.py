import pytest

from dashboard.login_config import dashboard_url, dashboard_urls

SERVER_URLS = "http://100.64.0.10:3000,http://192.168.1.20:3000"


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "true\r"])
def test_local_mode_pins_dashboard_origin_to_localhost(monkeypatch, value):
    monkeypatch.setenv("DASHBOARD_URL", SERVER_URLS)
    monkeypatch.setenv("LOCAL_MODE", value)
    assert dashboard_urls() == ["http://localhost:3000"]
    assert dashboard_url() == "http://localhost:3000"


@pytest.mark.parametrize("value", [None, "", "false", "0"])
def test_without_local_mode_dashboard_url_list_is_used(monkeypatch, value):
    monkeypatch.setenv("DASHBOARD_URL", SERVER_URLS)
    if value is None:
        monkeypatch.delenv("LOCAL_MODE", raising=False)
    else:
        monkeypatch.setenv("LOCAL_MODE", value)
    assert dashboard_urls() == SERVER_URLS.split(",")
    assert dashboard_url() == "http://100.64.0.10:3000"
