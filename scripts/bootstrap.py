"""Create the local files Compose expects, so Docker never invents them as directories.

Docker Desktop silently creates a *directory* when a bind/secret source is missing,
which then mounts over the runtime paths. Run this once before the first `up`.
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
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if path.is_dir() else 0))


def main():
    problems = []
    env, secrets = ROOT / ".env", ROOT / "secrets"
    token = secrets / "google-token.json"

    for path in (env, token):
        if path.is_dir():
            problems.append(f"{path} is a directory; remove it (Docker created it) and rerun.")
    if problems:
        print("\n".join(problems))
        return 1

    if env.exists():
        print(f"Kept existing {env.name}")
    else:
        shutil.copyfile(ROOT / ".env.example", env)
        print(f"Created {env.name} from .env.example - fill in your tokens and IDs")
    secure(env)

    secrets.mkdir(exist_ok=True)
    secure(secrets)
    print(f"Ready {secrets.name}{os.sep}")

    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    secure(data)
    print(f"Ready {data.name}{os.sep} (contact memory)")

    if os.name != "nt":
        print("Set DOBBY_UID/DOBBY_GID in .env to:", f"{os.getuid()}/{os.getgid()}")

    if token.exists():
        secure(token)
        print(f"Found {token.name}")
    else:
        print(f"Missing {token.name} - link Google before starting the bot:")
        print("  docker compose -f compose.auth.yaml run --rm --service-ports link-google")
    return 0


if __name__ == "__main__":
    sys.exit(main())
