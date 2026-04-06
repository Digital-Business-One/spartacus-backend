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


# ─── RFC-11: Timeline payloads ──────────────────────────────────────────────


@dataclass
class PostCreatedPayload:
    """Payload for post.created / post.updated / post.deleted events."""

    entity_id: str
    source_entity_ref: str
    source_entity_type: str
    type: str  # post | event | championship
    origin: str  # timeline_wizard | calendar
    title: str
    description: str
    author_uid: str
    author_name: str
    author_roles: list[str]
    attachments: list[dict] | None = None
    link_preview: dict | None = None
    event_date: str | None = None
    event_end_date: str | None = None
    event_location: str | None = None

    def personalization(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "source_entity_ref": self.source_entity_ref,
            "source_entity_type": self.source_entity_type,
            "type": self.type,
            "origin": self.origin,
            "title": self.title,
            "description": self.description,
            "author_uid": self.author_uid,
            "author_name": self.author_name,
            "author_roles": self.author_roles,
            "attachments": self.attachments,
            "link_preview": self.link_preview,
            "event_date": self.event_date,
            "event_end_date": self.event_end_date,
            "event_location": self.event_location,
        }


@dataclass
class CheckinRegisteredPayload:
    """Payload for checkin.registered event."""

    entity_id: str
    source_entity_ref: str
    source_entity_type: str
    target_uid: str
    target_name: str
    author_uid: str
    author_name: str
    turma_name: str
    modalidade_name: str
    class_date: str

    def personalization(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "source_entity_ref": self.source_entity_ref,
            "source_entity_type": self.source_entity_type,
            "target_uid": self.target_uid,
            "target_name": self.target_name,
            "author_uid": self.author_uid,
            "author_name": self.author_name,
            "turma_name": self.turma_name,
            "modalidade_name": self.modalidade_name,
            "class_date": self.class_date,
        }


@dataclass
class ValidationPayload:
    """Payload for validation events (confirmed/absent)."""

    entity_id: str
    target_uid: str
    target_name: str
    validated_by: str
    validated_at: str
    turma_name: str = ""
    donation_amount: str = ""

    def personalization(self) -> dict:
        d = {
            "entity_id": self.entity_id,
            "target_uid": self.target_uid,
            "target_name": self.target_name,
            "validated_by": self.validated_by,
            "validated_at": self.validated_at,
        }
        if self.turma_name:
            d["turma_name"] = self.turma_name
        if self.donation_amount:
            d["donation_amount"] = self.donation_amount
        return d


@dataclass
class ReviewRequestedPayload:
    """Payload for checkin.review_requested, donation.review_requested."""

    entity_id: str
    target_uid: str
    target_name: str
    review_requested_at: str
    turma_name: str = ""
    donation_amount: str = ""

    def personalization(self) -> dict:
        d = {
            "entity_id": self.entity_id,
            "target_uid": self.target_uid,
            "target_name": self.target_name,
            "review_requested_at": self.review_requested_at,
        }
        if self.turma_name:
            d["turma_name"] = self.turma_name
        if self.donation_amount:
            d["donation_amount"] = self.donation_amount
        return d


@dataclass
class DonationRegisteredPayload:
    """Payload for donation.registered event."""

    entity_id: str
    source_entity_ref: str
    source_entity_type: str
    target_uid: str
    target_name: str
    author_uid: str
    author_name: str
    donation_amount: str
    donation_date: str

    def personalization(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "source_entity_ref": self.source_entity_ref,
            "source_entity_type": self.source_entity_type,
            "target_uid": self.target_uid,
            "target_name": self.target_name,
            "author_uid": self.author_uid,
            "author_name": self.author_name,
            "donation_amount": self.donation_amount,
            "donation_date": self.donation_date,
        }
