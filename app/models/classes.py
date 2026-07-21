import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso_date(value: str) -> str:
    if not _ISO_DATE_RE.match(value):
        raise ValueError("attendanceStartDate inválida: use YYYY-MM-DD")
    return value


class AgeRange(BaseModel):
    min: int
    max: Optional[int] = None


class ScheduleItem(BaseModel):
    day: str        # mon, tue, wed, thu, fri, sat, sun
    start_time: str  # "16:00"
    end_time: str    # "17:00"


class ClassOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    modality_id: str
    modality_name: str
    schedule: str            # human-readable: "Ter/Qui 16:00–17:00"
    schedule_items: list[ScheduleItem] = []
    teacher: Optional[str] = None
    location: Optional[str] = None
    age_range: Optional[AgeRange] = None
    icon_url: Optional[str] = None
    active: bool = True
    student_count: int = 0
    # Attendance engine (RFC "Frequência Analítica"): enabling requires a
    # start date — records before it are outside every count/display window.
    attendance_engine_enabled: bool = Field(
        default=False, alias="attendanceEngineEnabled"
    )
    attendance_start_date: Optional[str] = Field(
        default=None, alias="attendanceStartDate"
    )


class ClassesResponse(BaseModel):
    classes: list[ClassOut]


# ── Legacy compat (read old format too) ──────────────────────────────────────


class WeeklySchedule(BaseModel):
    """Kept for backward compatibility with old seed format."""
    days: list[str]
    start_time: str
    end_time: str


# ── Create / Update ──────────────────────────────────────────────────────────


class ClassCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str               # slug, e.g. "jj-kids-vesp"
    name: str
    modality_id: str      # reference to modalities/{id}
    schedule: list[ScheduleItem]
    teacher_id: Optional[str] = None
    teacher_name: Optional[str] = None
    location: Optional[str] = None
    age_range: Optional[AgeRange] = None
    attendance_engine_enabled: bool = Field(
        default=False, alias="attendanceEngineEnabled"
    )
    attendance_start_date: Optional[str] = Field(
        default=None, alias="attendanceStartDate"
    )

    @field_validator("attendance_start_date")
    @classmethod
    def _date_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            _validate_iso_date(v)
        return v

    @model_validator(mode="after")
    def _engine_requires_start_date(self) -> "ClassCreate":
        if self.attendance_engine_enabled and not self.attendance_start_date:
            raise ValueError(
                "attendanceStartDate é obrigatória quando "
                "attendanceEngineEnabled=true"
            )
        return self


class ClassUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    modality_id: Optional[str] = None
    schedule: Optional[list[ScheduleItem]] = None
    teacher_id: Optional[str] = None
    teacher_name: Optional[str] = None
    location: Optional[str] = None
    age_range: Optional[AgeRange] = None
    # Optional[bool] with default None means "not provided in this PATCH";
    # the enabled-requires-start-date invariant is enforced against the
    # *merged* (persisted + incoming) state in ClassService.update, since a
    # partial update may legitimately omit one of the two fields.
    attendance_engine_enabled: Optional[bool] = Field(
        default=None, alias="attendanceEngineEnabled"
    )
    attendance_start_date: Optional[str] = Field(
        default=None, alias="attendanceStartDate"
    )

    @field_validator("attendance_start_date")
    @classmethod
    def _date_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            _validate_iso_date(v)
        return v
