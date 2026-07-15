from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class ModerationLevel(str, Enum):
    NONE = "none"
    COMMENT_BLOCKED = "comment_blocked"
    APP_BANNED = "app_banned"


class ModerationSet(BaseModel):
    """Body do POST /moderation/{uid}."""
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    level: ModerationLevel
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("level")
    @classmethod
    def _not_none(cls, v: ModerationLevel) -> ModerationLevel:
        if v == ModerationLevel.NONE:
            raise ValueError("use DELETE para liberar/desbanir")
        return v

    @field_validator("reason")
    @classmethod
    def _strip(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("reason must not be empty")
        return s


class ModeratedUserOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uid: str
    name: str
    role_label: Optional[str] = None
    photo_url: Optional[str] = None
    level: str            # ModerationLevel value ("none" = normal)
    is_staff: bool = False  # o app oculta ações sobre staff (só owner modera)


class ModeratedUsersPage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    items: list[ModeratedUserOut]
