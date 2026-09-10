"""Local-only, browser-based account linking. Never prints credentials."""

import argparse
import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", default="secrets/google-client.json")
    parser.add_argument("--output", default="secrets/google-token.json")
    parser.add_argument("--no-browser", action="store_true", help="Print a local authorization link")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--bind", default="localhost", help="Listener address; Docker uses 0.0.0.0 internally"
    )
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(args.client, SCOPES)
    credentials = flow.run_local_server(
        host="localhost",
        port=args.port,
        bind_addr=args.bind,
        open_browser=not args.no_browser,
        timeout_seconds=300,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Open this link in your computer's browser: {url}"
        if args.no_browser
        else "",
        success_message="Calendar linked. You can close this tab.",
    )
    if not credentials.refresh_token:
        raise SystemExit("No refresh token received. Revoke the app grant and link again.")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(credentials.to_json())
    print("Google account linked. Token saved locally; do not commit or share it.")


if __name__ == "__main__":
    main()
