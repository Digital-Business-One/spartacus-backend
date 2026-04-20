from typing import Optional

from pydantic import BaseModel

VALID_STATUSES: frozenset[str] = frozenset({"pending", "active", "suspended"})
VALID_ROLES: frozenset[str] = frozenset(
    {"owner", "assistant", "teacher", "instructor", "guardian", "student",
     "supporter", "sponsor", "social", "master"}
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


class EligibleUserOut(BaseModel):
    uid: str
    name: str
    email: str
    photo_url: Optional[str] = None
    birth_date: Optional[str] = None
    roles: list[str] = []


class AssignRoleRequest(BaseModel):
    role: str
    user_ids: list[str]


class AssignRoleResponse(BaseModel):
    assigned: int
    skipped: int
