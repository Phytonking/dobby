"""Entrypoint: `python -m bot.main` starts Dobby; `--check` only validates configuration."""

import logging
import sys

from .config import Config
from .models import ConfigError
from .diagnostics import log_startup_failure

log = logging.getLogger("scheduler")


def main(argv=None):
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    log.setLevel(logging.INFO)
    check_only = "--check" in (sys.argv[1:] if argv is None else argv)
    try:
        config = Config.load()
    except ConfigError as exc:
        log.error("startup_failed: %s", exc)
        raise SystemExit(2) from None
    except Exception as exc:
        log_startup_failure(log, exc, "configuration")
        raise SystemExit(1) from None
    if check_only:
        log.info(
            "config_ok guild=%s timezone=%s model=%s mention_channels=%d context_limit=%d",
            config.guild,
            config.timezone,
            config.model,
            len(config.mention_channels),
            config.context_limit,
        )
        return
    stage = "imports"
    try:
        from .integrations.discord.client import Bot

        stage = "initialization"
        bot = Bot(config)
        stage = "discord_run"
        bot.run(config.token, log_handler=None)
    except Exception as exc:
        log_startup_failure(log, exc, stage)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
