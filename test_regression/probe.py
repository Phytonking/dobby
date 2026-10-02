"""HTTP probes executed inside the isolated test network by run.py."""

import sys
import time

import httpx


def ready():
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=3) as client:
                assert client.get("http://dashboard:8000/health").json() == {"ok": True}
                response = client.get("http://dashboard:3000/login")
                assert response.status_code == 200 and "Dobby Dashboard" in response.text
                return
        except (httpx.HTTPError, AssertionError, ValueError):
            time.sleep(1)
    raise AssertionError("Dashboard/frontend did not become ready within 60 seconds")


def database(available):
    with httpx.Client(base_url="http://dashboard:8000", timeout=8) as client:
        response = client.post(
            "/auth/local", json={"username": "persist", "password": "persistent-password-only"}
        )
        if not available:
            assert 500 <= response.status_code < 600, response.text
        else:
            assert response.status_code == 200, response.text
            assert client.get("/me").json()["calendar_email"] == "persistent@example.org"


if __name__ == "__main__":
    if sys.argv[1] == "ready":
        ready()
    else:
        database(sys.argv[1] == "database-up")
