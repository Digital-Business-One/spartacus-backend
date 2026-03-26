from typing import Optional

from pydantic import BaseModel


class AgeRange(BaseModel):
    min: int
    max: Optional[int] = None


class ClassOut(BaseModel):
    id: str
    name: str
    modality: str
    schedule: str  # human-readable, e.g. "Seg/Qua/Sex 08:00–09:00"
    teacher: Optional[str] = None
    age_range: Optional[AgeRange] = None
    icon_url: Optional[str] = None


class ClassesResponse(BaseModel):
    classes: list[ClassOut]


class WeeklySchedule(BaseModel):
    days: list[str]
    start_time: str
    end_time: str


class ClassCreate(BaseModel):
    id: str  # slug, e.g. "muay-thai-kids"
    name: str
    modality: str
    weekly_schedule: WeeklySchedule
    teacher_id: Optional[str] = None
    teacher_name: Optional[str] = None
    age_range: Optional[AgeRange] = None


class ClassUpdate(BaseModel):
    name: Optional[str] = None
    modality: Optional[str] = None
    weekly_schedule: Optional[WeeklySchedule] = None
    teacher_id: Optional[str] = None
    teacher_name: Optional[str] = None
    age_range: Optional[AgeRange] = None
