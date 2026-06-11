from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.events import publisher
from app.logging.decorator import log
from app.models.donation import (
    DonationConfig,
    DonationConfigUpdate,
    DonationCreate,
    DonationDashboardOut,
    DonationHistoryOut,
    DonationOut,
    DonationRegisterReceived,
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
    result, event = DonationService().create(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    publisher.publish(
        event, project_id=ctx.project_id, source="donation_service",
    )
    return result


@log
@router.get("/donations/dashboard")
@require_roles("owner", "assistant", "teacher", "instructor")
def get_donations_dashboard() -> DonationDashboardOut:
    ctx = auth_ctx.get()
    return DonationService().dashboard(ctx.project_id)


@log
@router.post("/donations/register-received", status_code=201)
@require_roles("owner", "assistant", "teacher", "instructor")
def register_received_donation(data: DonationRegisterReceived) -> DonationOut:
    """Staff registers an already-received donation for a student."""
    ctx = auth_ctx.get()
    result, event = DonationService().register_received(
        project_id=ctx.project_id,
        actor_uid=ctx.user_id,
        data=data,
    )
    publisher.publish(
        event, project_id=ctx.project_id, source="donation_service",
    )
    return result


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
@router.get("/donations/history")
def get_donation_history(
    x_acting_as: Optional[str] = Header(None),
) -> DonationHistoryOut:
    ctx = auth_ctx.get()
    return DonationService().get_history(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
    )


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
