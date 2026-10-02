"""Startup diagnostics that omit exception messages, source lines and locals."""

import traceback


def log_startup_failure(logger, error, stage):
    logger.error("startup_failed stage=%s type=%s", stage, type(error).__name__)
    # SDK exception messages may contain URLs, tokens, or request payloads.
    # Frame locations still identify where a failure originated without those values.
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        logger.error("startup_exception type=%s", type(error).__name__)
        for frame, lineno in traceback.walk_tb(error.__traceback__):
            logger.error(
                "startup_frame file=%s line=%d function=%s",
                frame.f_code.co_filename,
                lineno,
                frame.f_code.co_name,
            )
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
