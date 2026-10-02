"""Create the local `.env` Compose expects, so Docker never invents it as a directory.

Docker Desktop silently creates a *directory* when a bind source is missing, which then shadows
the real file. Run this once before the first `up`. Service credentials live in Composio, not in
local files, so `.env` is the only thing to create.
"""

import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def secure(path):
    """Owner-only permissions where the platform supports them."""
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main():
    env = ROOT / ".env"
    if env.is_dir():
        print(f"{env} is a directory; remove it (Docker created it) and rerun.")
        return 1

    if env.exists():
        print(f"Kept existing {env.name}")
    else:
        shutil.copyfile(ROOT / ".env.example", env)
        print(f"Created {env.name} from .env.example - fill in your tokens, keys and IDs")
    secure(env)

    for stray in (ROOT / "secrets", ROOT / "data"):
        if stray.exists():
            print(f"Note: {stray.name}/ is no longer used; it can be deleted.")

    if os.name != "nt":
        print("Set DOBBY_UID/DOBBY_GID in .env to:", f"{os.getuid()}/{os.getgid()}")
    print(
        "Next: README.md → Set up Dobby, then `docker compose run --rm --no-deps bot python -m bot.main --check`"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
