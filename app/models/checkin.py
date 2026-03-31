from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ScheduleItemOut(BaseModel):
    day: str
    start_time: str
    end_time: str


class AvailableCheckinOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    aula_id: str
    turma_id: str
    turma_name: str
    modality_name: str
    date: str               # DD/MM/YYYY
    day_of_week: str        # "Quinta-feira"
    start_time: str         # "19:00"
    end_time: str           # "20:30"
    teacher: Optional[str] = None
    location: Optional[str] = None
    already_checked_in: bool = False


class CheckinRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    aula_id: str


class CheckinResponse(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    presenca_id: str
    status: str
    timestamp: str


class NextClassOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    turma_name: str
    modality_name: str
    day_of_week: str
    start_time: str


class NoCheckinAvailableOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    message: str = "Nenhuma aula disponível no momento"
    next_class: Optional[NextClassOut] = None
