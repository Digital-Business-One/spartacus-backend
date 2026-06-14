"""Pydantic schemas for posts (RFC-11)."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class AttachmentIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    type: Literal["image", "file", "voice"]
    url: str
    name: str
    size: int


class LinkPreviewIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    url: str
    title: str = ""
    image: str = ""
    description: str = ""


EventCategory = Literal["own", "external", "guest_class"]


class PostCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    type: Literal["post", "event", "championship"]
    title: str
    description: str
    attachments: list[AttachmentIn] = []
    link_preview: Optional[LinkPreviewIn] = None
    # event / championship only
    event_date: Optional[str] = None
    event_end_date: Optional[str] = None
    event_location: Optional[str] = None
    # event extras (backoffice calendar): own | external | guest_class
    event_category: Optional[EventCategory] = None
    modality_id: Optional[str] = None
    organizer: Optional[str] = None
    registration_link: Optional[str] = None


class PostUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    title: Optional[str] = None
    description: Optional[str] = None
    attachments: Optional[list[AttachmentIn]] = None
    link_preview: Optional[LinkPreviewIn] = None
    event_date: Optional[str] = None
    event_end_date: Optional[str] = None
    event_location: Optional[str] = None


class PostOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    id: str
    project_id: str
    type: str
    author_uid: str
    author_name: str
    title: str
    description: str
    attachments: list[dict] = []
    link_preview: Optional[dict] = None
    event_date: Optional[str] = None
    event_end_date: Optional[str] = None
    event_location: Optional[str] = None
    event_category: Optional[EventCategory] = None
    modality_id: Optional[str] = None
    organizer: Optional[str] = None
    registration_link: Optional[str] = None
    status: str
    created_at: str
    updated_at: Optional[str] = None
