from typing import Optional

from pydantic import BaseModel


class AccountAction(BaseModel):
    action: str
    label: str
    target_status: str


class AccountOut(BaseModel):
    uid: str
    name: str
    email: str
    roles: list[str]
    status: str
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    created_at: Optional[str] = None
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    available_actions: list[AccountAction] = []


class TransitionRequest(BaseModel):
    action: str


class TransitionResponse(BaseModel):
    uid: str
    previous_status: str
    new_status: str
    action: str
