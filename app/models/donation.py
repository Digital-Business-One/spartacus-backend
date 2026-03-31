from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel

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
