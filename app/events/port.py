from typing import Protocol

from app.events.models import DomainEvent


class EventPort(Protocol):
    def publish(
        self, event: DomainEvent, project_id: str, source: str
    ) -> None: ...
