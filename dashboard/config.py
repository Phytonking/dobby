import os

# strip() because .env is often CRLF; "true\r" must still count.
LOCAL_MODE = os.environ.get("LOCAL_MODE", "").strip().lower() in ("1", "true", "yes")

DASHBOARD_URL = (
    "http://localhost:3000" if LOCAL_MODE else os.environ.get("DASHBOARD_URL", "http://localhost:3000")
)
