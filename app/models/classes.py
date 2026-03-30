from typing import Optional

from pydantic import BaseModel


class AgeRange(BaseModel):
    min: int
    max: Optional[int] = None


class ScheduleItem(BaseModel):
    day: str        # mon, tue, wed, thu, fri, sat, sun
    start_time: str  # "16:00"
    end_time: str    # "17:00"


class ClassOut(BaseModel):
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
    id: str               # slug, e.g. "jj-kids-vesp"
    name: str
    modality_id: str      # reference to modalities/{id}
    schedule: list[ScheduleItem]
    teacher_id: Optional[str] = None
    teacher_name: Optional[str] = None
    location: Optional[str] = None
    age_range: Optional[AgeRange] = None


class ClassUpdate(BaseModel):
    name: Optional[str] = None
    modality_id: Optional[str] = None
    schedule: Optional[list[ScheduleItem]] = None
    teacher_id: Optional[str] = None
    teacher_name: Optional[str] = None
    location: Optional[str] = None
    age_range: Optional[AgeRange] = None
