from typing import Protocol

from app.notifications.rules import RULES


class NotificationRegistry(Protocol):
    def find_rule(
        self, event_id: str
    ) -> tuple[str, str] | None: ...


class DictRegistry:
    def find_rule(
        self, event_id: str
    ) -> tuple[str, str] | None:
        """Return (template_id, subject) or None."""
        rule = RULES.get(event_id)
        if not rule or not rule.get("active"):
            return None
        return rule["template_id"], rule["subject"]
