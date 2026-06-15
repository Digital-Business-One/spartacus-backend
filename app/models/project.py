from typing import Optional

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    id: str
    name: str
    razao_social: Optional[str] = None
    cnpj: Optional[str] = None
    logo_url: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    legal_nature: Optional[str] = None
    founded_at: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    razao_social: Optional[str] = None
    cnpj: Optional[str] = None
    logo_url: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    legal_nature: Optional[str] = None
    founded_at: Optional[str] = None


class ProjectOut(BaseModel):
    id: str
    name: str
    razao_social: Optional[str] = None
    cnpj: Optional[str] = None
    logo_url: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    legal_nature: Optional[str] = None
    founded_at: Optional[str] = None
    is_root: bool = False
    created_at: Optional[str] = None


class MyProjectOut(BaseModel):
    id: str
    name: str
    logo_url: Optional[str] = None
    roles: list[str]
