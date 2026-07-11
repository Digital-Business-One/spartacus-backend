import time

import structlog

from app.events.models import DomainEvent
from app.events.port import EventPort

logger = structlog.get_logger()

_MAX_ATTEMPTS = 3
# Backoff (seconds) applied between attempts 1→2 and 2→3. Only paid on
# failure — the success path never sleeps.
_BACKOFF_SECONDS = (0.2, 0.5)


class EventPublisher:
    def __init__(self, port: EventPort):
        self._port = port

    def publish(
        self, event: DomainEvent, project_id: str, source: str
    ) -> bool:
        """Enqueue a domain event. Returns True iff it was persisted.

        Publishing is a post-commit side-effect: the primary operation
        (account created, transition applied, …) has already succeeded, so a
        failure here must NOT raise and undo it. Instead we retry transient
        failures and, on definitive failure, return False and log loudly so
        the miss is observable and recoverable — never silently swallowed as
        a phantom success.
        """
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                self._port.publish(event, project_id, source)
                return True
            except Exception as exc:  # noqa: BLE001 — side-effect must not raise
                last_exc = exc
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_SECONDS[attempt - 1])

        logger.error(
            "event_publish_failed",
            event_id=event.id,
            project_id=project_id,
            source=source,
            attempts=_MAX_ATTEMPTS,
            exc_info=last_exc,
        )
        return False
