from typing import Optional

from fastapi import APIRouter, Header

from app.logging.decorator import log
from app.models.profile import (
    AddressUpdate,
    ClassesUpdate,
    CompetitionUpdate,
    DependentCreate,
    DependentOut,
    GraduationUpdate,
    ProfileOut,
    ProfileUpdate,
)
from app.security.context import auth_ctx
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/users/me", tags=["profile"])


@log
@router.get("/profile")
def get_profile(
    x_acting_as: Optional[str] = Header(None),
) -> ProfileOut:
    ctx = auth_ctx.get()
    return ProfileService().get_profile(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
    )


@log
@router.patch("/profile")
def update_profile(
    data: ProfileUpdate,
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    ctx = auth_ctx.get()
    ProfileService().update_profile(
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    return {"status": "updated"}


@log
@router.patch("/address")
def update_address(
    data: AddressUpdate,
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    ctx = auth_ctx.get()
    ProfileService().update_address(
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    return {"status": "updated"}


@log
@router.get("/dependents")
def list_dependents() -> list[DependentOut]:
    ctx = auth_ctx.get()
    return ProfileService().list_dependents(
        project_id=ctx.project_id,
        uid=ctx.user_id,
    )


@log
@router.post("/dependents", status_code=201)
def create_dependent(data: DependentCreate) -> DependentOut:
    ctx = auth_ctx.get()
    return ProfileService().create_dependent(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        data=data,
    )


@log
@router.patch("/classes")
def update_classes(
    data: ClassesUpdate,
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    ctx = auth_ctx.get()
    ProfileService().update_classes(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    return {"status": "updated"}


@log
@router.patch("/graduation")
def update_graduation(
    data: GraduationUpdate,
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    ctx = auth_ctx.get()
    ProfileService().update_graduation(
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    return {"status": "updated"}


@log
@router.patch("/competition")
def update_competition(
    data: CompetitionUpdate,
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    ctx = auth_ctx.get()
    ProfileService().update_competition(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    return {"status": "updated"}
