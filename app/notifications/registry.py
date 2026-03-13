from typing import Protocol

from app.notifications.rules import RULES


class NotificationRegistry(Protocol):
    def find_template(self, event_id: str) -> str | None: ...


class DictRegistry:
    def find_template(self, event_id: str) -> str | None:
        rule = RULES.get(event_id)
        if not rule or not rule.get("active"):
            return None
        return rule.get("template_id")
