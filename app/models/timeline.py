"""Pydantic schemas for timeline feed (RFC-11)."""

from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class TimelineEntryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    id: str
    project_id: str
    type: str
    origin: str
    visibility: str

    author_uid: str
    author_name: str
    author_roles: list[str] = []
    author_photo_url: Optional[str] = None

    target_uid: Optional[str] = None
    target_name: Optional[str] = None
    target_photo_url: Optional[str] = None

    title: Optional[str] = None
    description: Optional[str] = None
    attachments: Optional[list[dict]] = None
    link_preview: Optional[dict] = None

    event_date: Optional[str] = None
    event_location: Optional[str] = None

    validation_status: Optional[str] = None
    validated_by: Optional[str] = None
    validated_at: Optional[str] = None

    review_requested: bool = False
    review_resolved: bool = False

    likes_count: int = 0
    comments_count: int = 0
    user_liked: bool = False  # resolved per-request
    is_pinned: bool = False  # resolved per-request (project-level pin)

    turma_name: Optional[str] = None
    modalidade_name: Optional[str] = None
    class_date: Optional[str] = None
    donation_amount: Optional[str] = None

    roles_label: Optional[str] = None
    classes: Optional[list[str]] = None
    guardian_name: Optional[str] = None

    created_at: str
    updated_at: Optional[str] = None


class TimelineFeedResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    entries: list[TimelineEntryOut]
    next_cursor: Optional[str] = None


class ValidateRequest(BaseModel):
    status: str  # "confirmed" | "absent"


class PinResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    pinned: bool


class LinkPreviewResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    url: str
    title: str = ""
    image: str = ""
    description: str = ""


class LikeUser(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uid: str
    name: str
    role: str = ""
    photo_url: Optional[str] = None


class LikesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    users: list[LikeUser]
