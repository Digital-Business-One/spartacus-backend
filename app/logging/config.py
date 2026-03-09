import logging
import os

import structlog


def _rename_level_to_severity(
    logger: object, method: str, event_dict: dict
) -> dict:
    """Rename structlog 'level' to 'severity' for Cloud Logging compatibility."""
    level = event_dict.pop("level", method).upper()
    event_dict["severity"] = level
    return event_dict


def configure_logging() -> None:
    log_level_str = os.getenv("LOG_LEVEL", "info").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            _rename_level_to_severity,
            structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
