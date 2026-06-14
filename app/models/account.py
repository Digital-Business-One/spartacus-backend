from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


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


class ClassDetailOut(BaseModel):
    """Expanded class info for the account detail screen."""
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    id: str
    name: str
    modality_id: str
    modality_name: str
    schedule: str
    schedule_items: list[dict] = Field(default_factory=list)
    teacher: Optional[str] = None
    location: Optional[str] = None
    active: bool = True


class GraduationEntry(BaseModel):
    """Belt + degree per modality (mirrors profile.GraduationEntry).

    `status`: "approved" (staff-set) or "pending" (student's first insert,
    awaiting staff approval in the graduations dashboard).
    `locked_by_student`: the student already did the one-time insert and can
    no longer edit this modality from the app.
    """
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    belt: str
    degree: int = 0
    prajied: Optional[int] = None
    status: str = "approved"
    locked_by_student: bool = False
    graded_by: Optional[str] = None
    graded_by_name: Optional[str] = None
    graded_at: Optional[str] = None


class CompetitionOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    weight_kg: Optional[float] = None
    target_categories: list[str] = Field(default_factory=list)
    calculated_age_category: Optional[str] = None
    calculated_weight_category: Optional[str] = None


class AccountOut(BaseModel):
    """Listing card payload. Used by GET /accounts (paginated)."""
    uid: str
    name: str
    nickname: Optional[str] = None
    email: str
    roles: list[str]
    status: str
    email_verified: bool = False
    photo_url: Optional[str] = None
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    address: Optional[AddressOut] = None
    created_at: Optional[str] = None
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    guardian_name: Optional[str] = None
    guardian_photo_url: Optional[str] = None
    class_ids: list[str] = []
    class_names: list[str] = []
    modality_names: list[str] = []
    graduation: Optional[dict] = None
    available_actions: list[AccountAction] = []
    dependents: list["AccountOut"] = []


AccountOut.model_rebuild()


class AccountDetailOut(BaseModel):
    """Detail page payload — rich. Used by GET /accounts/{uid} (RFC-12)."""
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    # Identity
    uid: str
    name: str
    nickname: Optional[str] = None
    email: Optional[str] = None
    email_verified: bool = False
    tax_id: Optional[str] = None
    photo_url: Optional[str] = None
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None

    # Context
    roles: list[str] = Field(default_factory=list)
    status: str = ""
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    guardian_name: Optional[str] = None
    guardian_phone: Optional[str] = None
    guardian_relationship: Optional[str] = None

    # Address
    address: Optional[AddressOut] = None

    # Classes (expanded)
    classes: list[ClassDetailOut] = Field(default_factory=list)

    # Graduation per modality
    graduation: Optional[dict[str, GraduationEntry]] = None

    # Competition
    competition: Optional[CompetitionOut] = None

    # Audit fields
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_updated_by: Optional[str] = None
    last_updated_by_name: Optional[str] = None
    approved_by: Optional[str] = None
    approved_by_name: Optional[str] = None
    approved_at: Optional[str] = None
    auth_provider: Optional[str] = None

    # Derived
    age_category: Optional[Literal["child", "adult"]] = None

    # Actions
    available_actions: list[AccountAction] = Field(default_factory=list)


class AccountListPage(BaseModel):
    """Paginated envelope for GET /accounts (RFC-12)."""
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    items: list[AccountOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class WarningRequest(BaseModel):
    """POST /accounts/{uid}/warning — disciplinary warning with a reason."""
    reason: str

    @model_validator(mode="after")
    def require_reason(self) -> "WarningRequest":
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("Motivo da advertência é obrigatório")
        if len(self.reason) > 500:
            raise ValueError("Motivo deve ter no máximo 500 caracteres")
        return self


class NicknameRequest(BaseModel):
    """PATCH /accounts/{uid}/nickname — empty string removes the nickname."""
    nickname: str

    @model_validator(mode="after")
    def normalize(self) -> "NicknameRequest":
        self.nickname = self.nickname.strip()
        if len(self.nickname) > 30:
            raise ValueError("Apelido deve ter no máximo 30 caracteres")
        return self


class TransitionRequest(BaseModel):
    action: str
    reason: Optional[str] = None  # used by 'expel' (suspension)


class TransitionResponse(BaseModel):
    uid: str
    previous_status: str
    new_status: str
    action: str
