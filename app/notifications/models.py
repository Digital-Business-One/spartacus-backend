from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class DomainEvent:
    id: str
    payload: Any
    occurred_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class SignupEmailPayload:
    """Rich payload for signup confirmation emails (email + Google)."""

    uid: str
    status: str
    to: str
    name: str
    email: str
    phone: str
    roles_label: str
    link: str
    show_classes: bool
    classes: list[dict]
    show_dependents: bool
    dependents: list[dict]

    def personalization(self) -> dict:
        return {
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "roles_label": self.roles_label,
            "link": self.link,
            "show_link": bool(self.link),
            "show_classes": self.show_classes,
            "classes": self.classes,
            "show_dependents": self.show_dependents,
            "dependents": self.dependents,
        }


@dataclass
class ResendVerificationPayload:
    """Resend email verification link."""

    to: str
    name: str
    link: str

    def personalization(self) -> dict:
        return {"name": self.name, "link": self.link, "show_link": True}


@dataclass
class AccountNotificationPayload:
    """Generic notification payload: title + message + optional CTA.

    Used for: approval, rejection, anamnese, revision.
    """

    to: str
    name: str
    title: str
    message: str
    cta_text: str = ""
    cta_url: str = ""

    def personalization(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "message": self.message,
            "cta_text": self.cta_text,
            "cta_url": self.cta_url,
        }
