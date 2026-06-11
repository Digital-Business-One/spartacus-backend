from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel

from app.models.account import GraduationEntry

DonationItem = Literal[
    "food_1kg", "cookies", "coffee", "juice", "other",
]

ITEM_LABELS: dict[str, str] = {
    "food_1kg": "1 KG de alimento não perecível",
    "cookies": "1 pacote de bolacha",
    "coffee": "1 pacote de café",
    "juice": "1 pacote de suco",
    "other": "Outra forma de apoio",
}


class DonationCreate(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    item: DonationItem
    item_description: Optional[str] = None
    month: Optional[str] = None  # "2026-03" — defaults to current month

    @model_validator(mode="after")
    def desc_required_for_other(self) -> "DonationCreate":
        if self.item == "other" and not self.item_description:
            raise ValueError(
                "Descrição obrigatória para 'Outra forma de apoio'"
            )
        return self


class DonationOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    id: str
    item: str
    item_label: str
    item_description: Optional[str] = None
    month: str              # "2026-03"
    status: str             # pledged | received
    created_at: str


class DonationHistoryItem(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    # status: pledged | received | pending
    # status_label: Aguardando | Validado | Pendente
    id: str
    month: str                                    # "2026-03"
    month_label: str                              # "MARÇO / 2026"
    item: Optional[str] = None
    item_label: str
    item_description: Optional[str] = None
    status: str
    status_label: str
    created_at: str
    received_by: Optional[str] = None
    received_by_name: Optional[str] = None
    received_at: Optional[str] = None


class DonationHistoryOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    donations: list[DonationHistoryItem]



class DonationStudentCard(BaseModel):
    """Single student visible in the donations dashboard columns."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    user_id: str
    name: str
    initials: str
    age: Optional[int] = None
    photo_url: Optional[str] = None
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    guardian_name: Optional[str] = None
    graduation: Optional[dict[str, GraduationEntry]] = None
    # Donation state for the month
    # values: "none" | "pledged" | "received"
    # (an "absent"/rejected donation appears as "none" in the UI)
    status: str = "none"
    donation_id: Optional[str] = None
    item: Optional[str] = None
    item_label: Optional[str] = None
    item_description: Optional[str] = None
    registered_at: Optional[str] = None
    validated_at: Optional[str] = None


class DonationDashboardOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    month: str          # "2026-06"
    month_label: str    # "Junho / 2026"
    students: list[DonationStudentCard]


class DonationRegisterReceived(BaseModel):
    """Staff registers a donation on behalf of a student, already received."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    user_id: str
    item: DonationItem
    item_description: Optional[str] = None

    @model_validator(mode="after")
    def desc_required_for_other(self) -> "DonationRegisterReceived":
        if self.item == "other" and not self.item_description:
            raise ValueError(
                "Descrição obrigatória para 'Outra forma de apoio'"
            )
        return self


class DonationConfigItem(BaseModel):
    code: str
    label: str
    active: bool = True


class DonationConfig(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    items: list[DonationConfigItem]
    thank_you_message: str = ""


class DonationConfigUpdate(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    items: Optional[list[DonationConfigItem]] = None
    thank_you_message: Optional[str] = None
