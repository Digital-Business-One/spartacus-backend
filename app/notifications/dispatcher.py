import structlog

from app.notifications.models import DomainEvent
from app.notifications.registry import DictRegistry

logger = structlog.get_logger()


class NotificationDispatcher:
    def __init__(self, port, registry=None):
        self._port = port
        self._registry = registry or DictRegistry()

    def dispatch(self, event: DomainEvent) -> None:
        template_id = self._registry.find_template(event.id)
        if not template_id:
            logger.info(
                "notification_skipped",
                event_id=event.id,
                reason="no_template",
            )
            return
        self._port.send(
            event_id=event.id,
            template_id=template_id,
            to=event.payload.to,
            data=event.payload.personalization(),
        )
