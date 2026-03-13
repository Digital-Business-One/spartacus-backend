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
            "show_classes": self.show_classes,
            "classes": self.classes,
            "show_dependents": self.show_dependents,
            "dependents": self.dependents,
        }


@dataclass
class SignupGooglePayload:
    uid: str
    status: str


@dataclass
class AccountReceivedPayload:
    to: str
    name: str

    def personalization(self) -> dict:
        return {"name": self.name}


@dataclass
class ResendVerificationPayload:
    to: str
    name: str
    link: str

    def personalization(self) -> dict:
        return {"name": self.name, "link": self.link}
