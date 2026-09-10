"""Fail CI on common accidentally tracked secret files and credential patterns.

This is a baseline guard, not an exhaustive secret detector. Enable GitHub push protection too.
Only filenames are printed, never matched content.
"""

from pathlib import Path
import re
import subprocess
import sys

PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r'"refresh_token"\s*:\s*"[^"\s]{12,}"'),
    re.compile(r"(?m)^DISCORD_TOKEN\s*=\s*\S{20,}"),
    re.compile(r"\b(?:mfa\.[\w-]{60,}|[\w-]{24,}\.[\w-]{6}\.[\w-]{27,})\b"),
]


def main():
    result = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)
    failures = []
    for name in result.stdout.decode().split("\0"):
        if not name:
            continue
        path = Path(name)
        lower = name.lower()
        forbidden = (
            lower.startswith("secrets/")
            or (path.name.startswith(".env") and path.name != ".env.example")
            or path.suffix in (".pem", ".key")
            or (path.suffix == ".json" and any(x in lower for x in ("credentials", "token", "gha-creds")))
        )
        if forbidden or any(
            pattern.search(path.read_text(encoding="utf-8", errors="replace")) for pattern in PATTERNS
        ):
            failures.append(name)
    if failures:
        print("Possible secrets in tracked files (contents withheld):\n" + "\n".join(failures))
        sys.exit(1)
    print("Tracked-file secret guard passed.")


if __name__ == "__main__":
    main()
