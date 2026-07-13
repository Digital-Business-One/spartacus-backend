from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CommentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    text: str = Field(min_length=1, max_length=2000)
    parent_id: Optional[str] = None
    mentions: list[str] = Field(default_factory=list)


class CommentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    id: str
    author_uid: str
    author_name: str
    author_photo_url: Optional[str] = None
    text: str
    parent_id: Optional[str] = None
    mentions: list[str] = Field(default_factory=list)
    created_at: str
    deleted: bool = False
    deleted_by: Optional[str] = None


class CommentsPage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    items: list[CommentOut]
    next_cursor: Optional[str] = None


class MentionableOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uid: str
    display: str          # apelido→nome
    subtitle: Optional[str] = None  # nome, quando display é apelido
    photo_url: Optional[str] = None
    initials: str
