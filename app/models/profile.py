import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.models.account import AddressOut


def _validate_cpf(cpf: str) -> bool:
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for i in range(2):
        total = sum(
            int(d) * (10 + i - j) for j, d in enumerate(digits[: 9 + i])
        )
        check = 0 if total % 11 < 2 else 11 - (total % 11)
        if int(digits[9 + i]) != check:
            return False
    return True


# ── Profile ──────────────────────────────────────────────────────────────────


class GraduationEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    belt: str
    degree: int = 0
    prajied: Optional[int] = None
    # Output-only context (ignored on input; set server-side):
    status: Optional[str] = None          # "pending" | "approved"
    locked_by_student: bool = False


class CompetitionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    weight_kg: Optional[float] = None
    target_categories: list[str] = Field(default_factory=list)
    calculated_age_category: Optional[str] = None
    calculated_weight_category: Optional[str] = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uid: str
    name: str
    email: Optional[str] = None
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    tax_id: Optional[str] = None
    photo_url: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    approval_status: str = ""
    address: Optional[AddressOut] = None
    class_ids: list[str] = Field(default_factory=list)
    class_names: list[str] = Field(default_factory=list)
    graduation: Optional[dict[str, GraduationEntry]] = None
    competition: Optional[CompetitionOut] = None
    completion_percent: int = 0
    is_dependent: bool = False
    guardian_uid: Optional[str] = None


# ── Update schemas ───────────────────────────────────────────────────────────


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    name: Optional[str] = None
    birth_date: Optional[str] = None
    gender: Optional[Literal["male", "female"]] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    tax_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_min_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v.strip()) < 3:
            raise ValueError("Nome deve ter ao menos 3 caracteres")
        return v

    @field_validator("birth_date")
    @classmethod
    def birth_date_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            try:
                datetime.strptime(v, "%d/%m/%Y")
            except ValueError:
                raise ValueError("Data inválida: use DD/MM/YYYY")
        return v

    @field_validator("phone", "whatsapp")
    @classmethod
    def phone_digits(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        digits = re.sub(r"\D", "", v)
        if len(digits) not in (10, 11):
            raise ValueError("Telefone deve ter 10 ou 11 dígitos")
        return digits

    @field_validator("tax_id")
    @classmethod
    def tax_id_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _validate_cpf(v):
            raise ValueError("CPF inválido")
        return re.sub(r"\D", "", v) if v else v


class AddressUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    postal_code: str
    street: str
    number: str
    complement: Optional[str] = None
    neighborhood: str
    city: str
    state: str

    @field_validator("state")
    @classmethod
    def state_length(cls, v: str) -> str:
        if len(v) != 2:
            raise ValueError("UF deve ter 2 caracteres")
        return v.upper()


# ── Graduation ───────────────────────────────────────────────────────────────


class GraduationUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    graduation: dict[str, GraduationEntry]


# ── Competition ──────────────────────────────────────────────────────────────


class CompetitionUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    weight_kg: Optional[float] = None
    target_categories: Optional[list[str]] = None

    @field_validator("weight_kg")
    @classmethod
    def weight_positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("Peso deve ser positivo")
        return v


# ── Dependents ───────────────────────────────────────────────────────────────


class DependentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    name: str
    birth_date: str
    gender: Literal["male", "female"]

    @field_validator("name")
    @classmethod
    def name_min_length(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("Nome deve ter ao menos 3 caracteres")
        return v

    @field_validator("birth_date")
    @classmethod
    def birth_date_valid(cls, v: str) -> str:
        try:
            birth = datetime.strptime(v, "%d/%m/%Y").date()
        except ValueError:
            raise ValueError("Data inválida: use DD/MM/YYYY")
        today = date.today()
        age = (
            today.year
            - birth.year
            - ((today.month, today.day) < (birth.month, birth.day))
        )
        if age >= 18:
            raise ValueError("Dependente deve ter menos de 18 anos")
        return v


class DependentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uid: str
    name: str
    birth_date: Optional[str] = None
    gender: Optional[str] = None
    photo_url: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    approval_status: str = ""
    class_ids: list[str] = Field(default_factory=list)
    class_names: list[str] = Field(default_factory=list)
    registration_complete: bool = False


# ── Classes update ───────────────────────────────────────────────────────────


class ClassesUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    class_ids: list[str]
