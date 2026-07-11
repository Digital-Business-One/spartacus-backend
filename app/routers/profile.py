import time
from typing import Optional

from fastapi import APIRouter, Header, UploadFile
from firebase_admin import firestore

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
from app.services.storage_service import StorageService

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
    changed = ProfileService().update_graduation(
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    # Report the real outcome so the client never claims a phantom success.
    return {"status": "updated" if changed else "unchanged", "changed": changed}


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


@log
@router.post("/photo")
async def upload_photo(
    file: UploadFile,
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    ctx = auth_ctx.get()
    target_uid = x_acting_as or ctx.user_id
    if x_acting_as:
        ProfileService()._assert_guardian_of(
            ctx.user_id, target_uid,
        )

    photo_url = await StorageService().upload_avatar(
        target_uid, file,
    )
    sep = "&" if "?" in photo_url else "?"
    photo_url = f"{photo_url}{sep}v={int(time.time())}"

    db = firestore.client()
    db.collection("users").document(target_uid).update(
        {"photoUrl": photo_url},
    )
    return {"status": "uploaded", "photo_url": photo_url}


@log
@router.delete("/photo")
def delete_photo(
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    ctx = auth_ctx.get()
    target_uid = x_acting_as or ctx.user_id
    if x_acting_as:
        ProfileService()._assert_guardian_of(
            ctx.user_id, target_uid,
        )

    StorageService().delete_avatar(target_uid)

    db = firestore.client()
    db.collection("users").document(target_uid).update(
        {"photoUrl": None},
    )
    return {"status": "deleted"}
