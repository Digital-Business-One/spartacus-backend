from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
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


# ── RFC-14: Dashboard de Frequência (kanban) ───────────────────────────────

class StudentAttendanceCard(BaseModel):
    """Single person visible in the frequência dashboard columns."""

    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    user_id: str
    name: str
    initials: str
    age: Optional[int] = None
    age_category: Optional[str] = None       # "Kids", "Juvenil", "Adulto"...
    photo_url: Optional[str] = None
    roles: list[str] = []
    is_dependent: bool = False
    guardian_uid: Optional[str] = None
    guardian_name: Optional[str] = None
    # Attendance state for today's aula
    # values: "absent" | "registered" | "confirmed"
    status: str = "absent"
    attendance_id: Optional[str] = None
    source: Optional[str] = None             # "qr" | "manual"
    registered_at: Optional[str] = None
    confirmed_at: Optional[str] = None


class ClassBriefOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    id: str
    name: str
    modality_id: str
    modality_name: str
    teacher_name: Optional[str] = None
    start_time: Optional[str] = None         # "19:00"
    end_time: Optional[str] = None           # "20:30"
    total_slots: int = 0
    enrolled_count: int = 0


class AttendanceDashboardOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    class_info: ClassBriefOut = Field(..., alias="class")
    aula_id: Optional[str] = None            # null if no session scheduled today
    date: str                                # "YYYY-MM-DD"
    students: list[StudentAttendanceCard]


class AttendanceActionRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    class_id: str
    user_id: str
    aula_id: Optional[str] = None            # server resolves today's aula if omitted
    source: Optional[str] = "manual"         # "qr" | "manual"
    reason: Optional[str] = None             # for rejections


class AttendanceActionOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    status: str
    attendance_id: Optional[str] = None
