import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

VALID_SIGNUP_ROLES: frozenset[str] = frozenset(
    {"student", "teacher", "instructor", "guardian", "supporter", "sponsor"}
)


def _parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        raise ValueError("Data inválida: use DD/MM/YYYY")


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


class DependentIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    id: str
    name: str
    birth_date: str
    gender: Literal["male", "female"]
    tax_id: Optional[str] = Field(default=None)
    class_ids: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_min_length(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("Nome deve ter ao menos 3 caracteres")
        return v

    @field_validator("birth_date")
    @classmethod
    def birth_date_format(cls, v: str) -> str:
        _parse_date(v)
        return v

    @field_validator("tax_id")
    @classmethod
    def tax_id_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _validate_cpf(v):
            raise ValueError("CPF inválido")
        return re.sub(r"\D", "", v) if v else v


class SignupRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    auth_method: Literal["email", "google"]
    email: str
    password: Optional[str] = Field(default=None)
    name: str
    birth_date: str
    gender: Literal["male", "female"]
    tax_id: Optional[str] = Field(default=None)
    phone: str
    whatsapp: str
    postal_code: str
    street: str
    number: str
    complement: Optional[str] = Field(default=None)
    neighborhood: str
    city: str
    state: str
    roles: list[str]
    dependents: list[DependentIn] = Field(default_factory=list)
    class_ids: list[str] = Field(default_factory=list)

    @field_validator("email")
    @classmethod
    def email_format(cls, v: str) -> str:
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Email inválido")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) < 8:
            raise ValueError("Senha deve ter ao menos 8 caracteres")
        return v

    @field_validator("name")
    @classmethod
    def name_min_length(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("Nome deve ter ao menos 3 caracteres")
        return v

    @field_validator("birth_date")
    @classmethod
    def birth_date_valid(cls, v: str) -> str:
        _parse_date(v)
        return v

    @field_validator("tax_id")
    @classmethod
    def tax_id_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _validate_cpf(v):
            raise ValueError("CPF inválido")
        return re.sub(r"\D", "", v) if v else v

    @field_validator("phone", "whatsapp")
    @classmethod
    def phone_digits(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if len(digits) not in (10, 11):
            raise ValueError("Telefone deve ter 10 ou 11 dígitos")
        return digits

    @field_validator("roles")
    @classmethod
    def roles_valid(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Ao menos uma role é obrigatória")
        invalid = set(v) - VALID_SIGNUP_ROLES
        if invalid:
            raise ValueError(f"Roles inválidas: {invalid}")
        return v

    @model_validator(mode="after")
    def check_business_rules(self) -> "SignupRequest":
        if self.auth_method == "email" and not self.password:
            raise ValueError("Senha obrigatória para authMethod=email")
        if "guardian" in self.roles and not self.dependents:
            raise ValueError("Guardian deve informar ao menos um dependente")
        birth = _parse_date(self.birth_date)
        today = date.today()
        age = (
            today.year
            - birth.year
            - ((today.month, today.day) < (birth.month, birth.day))
        )
        if age < 16:
            raise ValueError("Usuário deve ter ao menos 16 anos")
        return self


class SignupResponse(BaseModel):
    uid: str
    status: str
    email: Optional[str] = None


class ResendVerificationRequest(BaseModel):
    email: str


class EmailVerifiedResponse(BaseModel):
    status: str


class CheckEmailResponse(BaseModel):
    available: bool


class MeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    uid: str
    email: str
    approval_status: str
    birth_date: Optional[str] = None
