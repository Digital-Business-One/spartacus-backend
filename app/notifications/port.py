from typing import Protocol


class NotificationPort(Protocol):
    def send(
        self, event_id: str, template_id: str, to: str, data: dict
    ) -> None: ...
