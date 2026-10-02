"""Run with python -m dashboard.local_admin USERNAME [--email EXISTING_GOOGLE_EMAIL]."""
import argparse
import asyncio
from getpass import getpass
import re
import sys

from sqlalchemy import delete, select

from .database import SessionLocal, engine
from .models import Session, User
from .passwords import hash_password


def prompt_password(*, confirm: bool = True) -> str:
    """Keep input hidden; never trim or otherwise change the chosen password."""
    for _ in range(3):
        password = getpass("Password (12-256 characters; input hidden): ")
        if not 12 <= len(password) <= 256:
            print("Password must contain 12 to 256 characters. Try again.", file=sys.stderr)
            continue
        if not confirm or password == getpass("Confirm password (input hidden): "):
            return password
        print(
            "Passwords do not match. Nothing was saved. Enter both again, waiting for each prompt.\n"
            "If confirmation keeps failing, rerun with --no-confirm to enter the password once.",
            file=sys.stderr,
        )
    raise ValueError("Password entry failed after three attempts; nothing was saved")


async def save_admin(username: str, password_hash: str, email: str | None = None):
    async with SessionLocal() as db, db.begin():
        user = (await db.execute(select(User).where(User.local_username == username))).scalar_one_or_none()
        if email:
            linked = (await db.execute(select(User).where(User.uw_email == email))).scalar_one_or_none()
            if linked is None:
                raise ValueError("No existing user with that Google email")
            if user is not None and user.id != linked.id:
                raise ValueError("Username already belongs to a different user")
            if linked.local_username not in (None, username):
                raise ValueError("That user already has a different local username")
            user = linked
        if user is None:
            user = User(display_name=username, local_username=username, role="admin")
            db.add(user)
        else:
            await db.execute(delete(Session).where(Session.user_id == user.id))
        user.local_username = username
        user.password_hash = password_hash
        user.role = "admin"


def main():
    parser = argparse.ArgumentParser(description="Create/reset a local admin; passwords are prompted securely")
    parser.add_argument("username")
    parser.add_argument("--email", help="Attach credentials to an existing Google user")
    parser.add_argument("--no-confirm", action="store_true", help="Enter a hidden password once without confirmation")
    args = parser.parse_args()
    username = args.username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,64}", username):
        parser.error("Username must be 3-64 letters, digits, dots, underscores or hyphens")
    try:
        password = prompt_password(confirm=not args.no_confirm)
    except (EOFError, KeyboardInterrupt):
        parser.error("Password entry cancelled; use an interactive terminal (docker compose exec without -T)")
    except ValueError as error:
        parser.error(str(error))

    async def run():
        try:
            await save_admin(username, hash_password(password), args.email)
        finally:
            await engine.dispose()

    try:
        asyncio.run(run())
    except ValueError as error:
        parser.error(str(error))
    print(f"Local admin '{username}' is ready. Existing sessions for this user were revoked.")


if __name__ == "__main__":
    main()
