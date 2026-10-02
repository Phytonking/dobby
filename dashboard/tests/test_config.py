import importlib
import os
from unittest.mock import patch

import pytest

import dashboard.config as config

SERVER_URL = "http://100.64.0.10:3000"


@pytest.fixture(autouse=True)
def restore_config():
    yield
    importlib.reload(config)


def load(local_mode):
    env = {"DASHBOARD_URL": SERVER_URL}
    if local_mode is not None:
        env["LOCAL_MODE"] = local_mode
    with patch.dict(os.environ, env):
        if local_mode is None:
            os.environ.pop("LOCAL_MODE", None)
        return importlib.reload(config)


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "true\r"])
def test_local_mode_forces_localhost_over_dashboard_url(value):
    assert load(value).DASHBOARD_URL == "http://localhost:3000"


@pytest.mark.parametrize("value", [None, "", "false", "0"])
def test_without_local_mode_dashboard_url_is_used(value):
    assert load(value).DASHBOARD_URL == SERVER_URL
