from typing import Optional

from pydantic import BaseModel


class ModalityOut(BaseModel):
    id: str
    name: str
    slug: str
    icon_url: Optional[str] = None


class ModalitiesResponse(BaseModel):
    modalities: list[ModalityOut]


class ModalityCreate(BaseModel):
    slug: str
    name: str


class ModalityUpdate(BaseModel):
    name: Optional[str] = None
