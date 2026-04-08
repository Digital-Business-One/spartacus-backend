from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class AttendanceRecord(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    # status: registered | confirmed | absent | absent_justified
    # status_label: Aguardando | Validado | Não confirmado | Falta Justificada
    id: str
    date: str                                 # "15 de Março"
    date_sort: str                            # "2026-03-15" for sorting
    time: Optional[str] = None                # "14:30" (HH:MM)
    class_id: str
    class_name: str
    modality_name: str
    teacher_name: Optional[str] = None
    status: str
    status_label: str
    validated_by: Optional[str] = None
    validated_by_name: Optional[str] = None
    validated_at: Optional[str] = None
    justification: Optional[str] = None


class MonthSummary(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    month: str           # "2026-03"
    month_label: str     # "Março / 2026"
    attended: int        # 10
    total: int           # 12
    percent: int         # 83
    records: list[AttendanceRecord]


class AttendanceHistoryOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    streak_days: int     # consecutive training days
    overall_percent: int # weighted average across all months
    months: list[MonthSummary]
