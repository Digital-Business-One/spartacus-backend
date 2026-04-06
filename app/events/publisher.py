import structlog

from app.events.models import DomainEvent
from app.events.port import EventPort

logger = structlog.get_logger()


class EventPublisher:
    def __init__(self, port: EventPort):
        self._port = port

    def publish(
        self, event: DomainEvent, project_id: str, source: str
    ) -> None:
        try:
            self._port.publish(event, project_id, source)
        except Exception:
            logger.error(
                "event_publish_failed",
                event_id=event.id,
                project_id=project_id,
                source=source,
                exc_info=True,
            )
