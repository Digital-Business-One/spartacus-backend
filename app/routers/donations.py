from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.logging.decorator import log
from app.models.donation import (
    DonationConfig,
    DonationConfigUpdate,
    DonationCreate,
    DonationOut,
)
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.donation_service import DonationService

router = APIRouter(tags=["donations"])


@log
@router.post("/donations", status_code=201)
def create_donation(
    data: DonationCreate,
    x_acting_as: Optional[str] = Header(None),
) -> DonationOut:
    ctx = auth_ctx.get()
    return DonationService().create(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )


@log
@router.get("/donations/current")
def get_current_donation(
    x_acting_as: Optional[str] = Header(None),
) -> DonationOut:
    ctx = auth_ctx.get()
    result = DonationService().get_current(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma doação registrada este mês",
        )
    return result


@log
@router.get("/projects/{project_id}/donation-config")
def get_donation_config(project_id: str) -> DonationConfig:
    return DonationService().get_config(project_id)


@log
@router.patch("/projects/{project_id}/donation-config")
@require_roles("owner", "assistant")
def update_donation_config(
    project_id: str, data: DonationConfigUpdate,
) -> DonationConfig:
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(
            status_code=403, detail="Projeto não autorizado",
        )
    return DonationService().update_config(project_id, data)
