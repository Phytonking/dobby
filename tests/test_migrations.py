"""Exercise the migration entry point without needing a running Postgres server."""

import io
import runpy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic import command, context
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scheme", ["postgresql+asyncpg", "postgresql"])
@pytest.mark.parametrize("connection_fails", [False, True])
def test_online_uses_installed_async_driver(monkeypatch, scheme, connection_fails):
    monkeypatch.setenv("DATABASE_URL", f"{scheme}://test:pass%25word@localhost/test")
    config = Config()
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: False)
    configure = MagicMock()
    migrate = MagicMock()
    monkeypatch.setattr(context, "configure", configure)
    monkeypatch.setattr(context, "begin_transaction", MagicMock())
    monkeypatch.setattr(context, "run_migrations", migrate)
    connection = MagicMock()
    async_connection = AsyncMock()
    async_connection.run_sync.side_effect = lambda fn: fn(connection)
    manager = AsyncMock()
    manager.__aenter__.return_value = async_connection
    if connection_fails:
        manager.__aenter__.side_effect = RuntimeError("connection unavailable")

    def connect(engine):
        assert engine.dialect.driver == "asyncpg"
        assert engine.url.password == "pass%word"
        return manager

    monkeypatch.setattr(AsyncEngine, "connect", connect)
    dispose = AsyncMock()
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    if connection_fails:
        with pytest.raises(RuntimeError, match="connection unavailable"):
            runpy.run_path(str(ROOT / "migrations/env.py"))
        migrate.assert_not_called()
    else:
        runpy.run_path(str(ROOT / "migrations/env.py"))
        configure.assert_called_once_with(connection=connection, target_metadata=None)
        migrate.assert_called_once_with()
    dispose.assert_awaited_once()


def test_offline_generates_all_migrations(monkeypatch):
    # Alembic's fileConfig disables existing loggers, leaking into later tests.
    monkeypatch.setattr("logging.config.fileConfig", lambda *args, **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:pass%25word@localhost/test")
    output = io.StringIO()
    config = Config(str(ROOT / "migrations/alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert "CREATE TABLE users" in sql
    assert "ADD COLUMN local_username" in sql
    assert "ADD COLUMN password_hash" in sql
    assert "uq_users_local_username" in sql
    assert "CREATE TABLE calendar_invites" in sql
    assert "uq_calendar_invite_target" in sql
    assert "ADD COLUMN calendar_email_updated_at" in sql
