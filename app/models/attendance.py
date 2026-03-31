from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class AttendanceRecord(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    id: str                          # presenca doc id or generated key
    date: str                        # "15 de Março"
    date_sort: str                   # "2026-03-15" for client sorting
    modality_name: str               # "Jiu-Jitsu"
    status: str                      # "present" | "absent" | "justified"
    status_label: str                # "Presença" | "Falta" | "Falta Justificada"
    justification: Optional[str] = None  # e.g. "Viagem a trabalho"


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
