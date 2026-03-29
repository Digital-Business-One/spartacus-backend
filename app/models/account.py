from typing import Optional

from pydantic import BaseModel


class AccountAction(BaseModel):
    action: str
    label: str
    target_status: str


class AddressOut(BaseModel):
    postal_code: Optional[str] = None
    street: Optional[str] = None
    number: Optional[str] = None
    complement: Optional[str] = None
    neighborhood: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None


class AccountOut(BaseModel):
    uid: str
    name: str
    email: str
    roles: list[str]
    status: str
    email_verified: bool = False
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    address: Optional[AddressOut] = None
    created_at: Optional[str] = None
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    class_ids: list[str] = []
    class_names: list[str] = []
    available_actions: list[AccountAction] = []


class TransitionRequest(BaseModel):
    action: str


class TransitionResponse(BaseModel):
    uid: str
    previous_status: str
    new_status: str
    action: str
