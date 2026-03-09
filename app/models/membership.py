from typing import Optional

from pydantic import BaseModel

VALID_STATUSES: frozenset[str] = frozenset({"pending", "active", "suspended"})
VALID_ROLES: frozenset[str] = frozenset(
    {"owner", "assistant", "teacher", "instructor", "guardian", "student",
     "supporter", "sponsor"}
)


class MembershipCreate(BaseModel):
    user_id: str
    roles: list[str]
    status: str = "active"


class MembershipUpdate(BaseModel):
    roles: Optional[list[str]] = None
    status: Optional[str] = None


class MembershipOut(BaseModel):
    project_id: str
    user_id: str
    roles: list[str]
    status: str
    joined_at: str
