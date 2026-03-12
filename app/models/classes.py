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
