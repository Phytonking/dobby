"""One command for CI/local verification. Requires only Python and Docker Compose.

Creates an isolated project, never reads .env or publishes host ports, and always
removes only that project's containers and test volume. No Docker socket in tests.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "test_regression" / "artifacts"
PROJECT = "dobby-test-" + uuid.uuid4().hex[:10]
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    "test_regression/ci.env",
    "-p",
    PROJECT,
    "-f",
    "compose.regression.yaml",
]
# Explicit values override inherited shell credentials as well as the env file.
ENV = os.environ.copy()
for line in (ROOT / "test_regression" / "ci.env").read_text().splitlines():
    if line and not line.startswith("#"):
        key, value = line.split("=", 1)
        ENV[key] = value
for key in (
    "INSTAGRAM_USER_ID",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "DISCORD_CLIENT_ID",
    "DISCORD_CLIENT_SECRET",
    "BOOTSTRAP_ADMIN_EMAIL",
    "LOCAL_NETWORKS",
    "NOTION_PARENT_PAGE_ID",
):
    ENV[key] = ""


def compose(*args, capture=False, check=True, timeout=600):
    result = subprocess.run(
        COMPOSE + list(args), cwd=ROOT, env=ENV, text=True, capture_output=capture, timeout=timeout
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Compose {args[0]} failed ({result.returncode}): {result.stderr if capture else ''}"
        )
    return result


def probe(mode):
    compose("run", "--rm", "--no-deps", "regression", "python", "test_regression/probe.py", mode, timeout=90)


def postgres_ready():
    for _ in range(30):
        result = compose("exec", "-T", "postgres", "pg_isready", "-U", "dobby", capture=True, check=False)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError("Postgres did not recover")


def test_01_fresh_stack():
    compose("up", "-d", "bot", "dashboard", "frontend")
    probe("ready")
    for _ in range(30):
        logs = compose("logs", "--no-color", "bot", capture=True).stdout
        if "regression_bot_ready" in logs:
            break
        time.sleep(1)
    else:
        raise AssertionError("Bot never reached readiness: " + logs)
    for service in ("bot", "dashboard", "frontend"):
        container = compose("ps", "-q", service, capture=True).stdout.strip()
        state = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
        assert state["State"]["Running"] and state["RestartCount"] == 0, service


def test_02_bot_container_restrictions():
    # bot_smoke executes a representative provider/cache operation under these limits.
    container = compose("ps", "-q", "bot", capture=True).stdout.strip()
    state = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    assert state["HostConfig"]["ReadonlyRootfs"]
    assert state["Config"]["User"] == "10001:10001"
    assert "ALL" in state["HostConfig"]["CapDrop"]
    assert state["HostConfig"]["Memory"] == 512 * 1024 * 1024
    compose(
        "exec",
        "-T",
        "bot",
        "python",
        "-c",
        "from pathlib import Path; assert Path('/tmp/.composio/regression-write').read_text() == 'ok'",
    )


def test_08_restart_persistence():
    compose(
        "exec",
        "-T",
        "dashboard",
        "python",
        "-c",
        "import asyncio; from dashboard.local_admin import save_admin; from dashboard.passwords import hash_password; asyncio.run(save_admin('persist', hash_password('persistent-password-only')))",
    )
    compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "dobby",
        "-d",
        "dobby",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "UPDATE users SET calendar_email='persistent@example.org' WHERE local_username='persist'",
    )
    compose("restart", "postgres")
    postgres_ready()
    compose("up", "-d", "--force-recreate", "--no-deps", "bot", "dashboard", "frontend")
    probe("ready")
    probe("database-up")


def test_09_database_outage_recovery():
    compose("stop", "postgres")
    try:
        probe("database-down")
    finally:
        compose("start", "postgres")
        postgres_ready()
    probe("database-up")


def main():
    ARTIFACTS.mkdir(exist_ok=True)
    for name in ("lifecycle.xml", "regression.xml", "compose.log"):
        (ARTIFACTS / name).unlink(missing_ok=True)
    suite = ET.Element("testsuite", name="Docker lifecycle")
    failed = False
    try:
        compose("build", "bot", "dashboard", "frontend", "migrate", timeout=1200)
        compose("build", "regression", timeout=1200)
        compose("run", "--rm", "--no-deps", "regression", "ruff", "check", "test_regression")
        compose("run", "--rm", "--no-deps", "regression", "ruff", "format", "--check", "test_regression")
        for test in (
            test_01_fresh_stack,
            test_02_bot_container_restrictions,
            test_08_restart_persistence,
            test_09_database_outage_recovery,
        ):
            print(f"\n{test.__name__}", flush=True)
            case = ET.SubElement(suite, "testcase", name=test.__name__, classname="Docker")
            start = time.monotonic()
            try:
                test()
            except Exception as exc:
                ET.SubElement(case, "failure", message=str(exc)).text = str(exc)
                raise
            finally:
                case.set("time", str(time.monotonic() - start))
        compose(
            "run",
            "--rm",
            "--no-deps",
            "-v",
            f"{ARTIFACTS.as_posix()}:/artifacts",
            "regression",
            "python",
            "-m",
            "pytest",
            "test_regression",
            "-v",
            "--tb=short",
            "-p",
            "no:cacheprovider",
            "--junitxml=/artifacts/regression.xml",
            timeout=600,
        )
    except Exception as exc:
        failed = True
        print(str(exc), file=sys.stderr)
        if not suite.findall("testcase/failure") and not (ARTIFACTS / "regression.xml").exists():
            case = ET.SubElement(suite, "testcase", name="suite_setup", classname="Docker")
            ET.SubElement(case, "failure", message=str(exc))
    finally:
        suite.set("tests", str(len(suite.findall("testcase"))))
        suite.set("failures", str(len(suite.findall("testcase/failure"))))
        ET.ElementTree(suite).write(ARTIFACTS / "lifecycle.xml", encoding="unicode")
        logs = compose("logs", "--no-color", capture=True, check=False).stdout
        (ARTIFACTS / "compose.log").write_text(logs, encoding="utf-8")
        compose("down", "--volumes", "--remove-orphans", check=False)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
