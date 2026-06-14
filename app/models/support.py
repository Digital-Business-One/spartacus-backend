"""Support (Apoio) — donations and services configured per project.

Generalizes the former "donations" feature: a support record is either a
`donation` or a `service`, both configured in the project. No monthly lock —
a member may register as many as they like; staff approve/reject each one.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel

SupportType = Literal["donation", "service"]

# Defaults used to seed the config when a project has none yet (and to
# migrate the legacy donationConfig).
DEFAULT_DONATION_ITEMS: dict[str, str] = {
    "food_1kg": "1 KG de alimento não perecível",
    "cookies": "1 pacote de bolacha",
    "coffee": "1 pacote de café",
    "juice": "1 pacote de suco",
    "other": "Outra forma de apoio",
}
DEFAULT_SERVICE_ITEMS: dict[str, str] = {
    "class": "Ministrar uma aula",
    "cleaning": "Ajudar na limpeza",
    "event_help": "Ajudar em um evento",
    "other": "Outra forma de apoio",
}
DEFAULT_THANK_YOU = (
    "Muito obrigado pelo seu apoio! Sua contribuição mantém o "
    "Projeto Spartacus vivo. Oss!"
)


class SupportCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    support_type: SupportType = "donation"
    item: str                                # code from the project config
    item_description: Optional[str] = None
    month: Optional[str] = None              # "2026-03" — optional metadata

    @model_validator(mode="after")
    def desc_required_for_other(self) -> "SupportCreate":
        if self.item == "other" and not (self.item_description or "").strip():
            raise ValueError("Descrição obrigatória para 'Outra forma de apoio'")
        return self


class SupportOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    id: str
    support_type: SupportType
    item: str
    item_label: str
    item_description: Optional[str] = None
    month: Optional[str] = None
    status: str                              # pledged | received | absent
    created_at: str


class SupportHistoryItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    id: str
    support_type: SupportType
    month: Optional[str] = None
    month_label: Optional[str] = None
    item: Optional[str] = None
    item_label: str
    item_description: Optional[str] = None
    status: str
    status_label: str
    created_at: str
    received_by: Optional[str] = None
    received_by_name: Optional[str] = None
    received_at: Optional[str] = None


class SupportHistoryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    items: list[SupportHistoryItem]


class SupportRecordCard(BaseModel):
    """A single support record in the staff dashboard (per-record kanban)."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    id: str
    user_id: str
    name: str
    nickname: Optional[str] = None
    initials: str
    age: Optional[int] = None
    photo_url: Optional[str] = None
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    guardian_name: Optional[str] = None
    support_type: SupportType
    item: Optional[str] = None
    item_label: Optional[str] = None
    item_description: Optional[str] = None
    # values: "pledged" | "received" | "absent"
    status: str = "pledged"
    created_at: Optional[str] = None
    validated_at: Optional[str] = None


class SupportDashboardOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    month: str
    month_label: str
    items: list[SupportRecordCard]


class SupportRegisterReceived(BaseModel):
    """Staff registers a support already received, on behalf of a member."""

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    user_id: str
    support_type: SupportType = "donation"
    item: str
    item_description: Optional[str] = None

    @model_validator(mode="after")
    def desc_required_for_other(self) -> "SupportRegisterReceived":
        if self.item == "other" and not (self.item_description or "").strip():
            raise ValueError("Descrição obrigatória para 'Outra forma de apoio'")
        return self


class SupportConfigItem(BaseModel):
    code: str
    label: str
    active: bool = True


class SupportConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    donations: list[SupportConfigItem]
    services: list[SupportConfigItem]
    thank_you_message: str = ""


class SupportConfigUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    donations: Optional[list[SupportConfigItem]] = None
    services: Optional[list[SupportConfigItem]] = None
    thank_you_message: Optional[str] = None
