"""Support (Apoio) router — donations + services. Generalizes donations."""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from app.events import publisher
from app.logging.decorator import log
from app.models.support import (
    SupportConfig,
    SupportConfigUpdate,
    SupportCreate,
    SupportDashboardOut,
    SupportHistoryOut,
    SupportOut,
    SupportRegisterReceived,
)
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.support_service import SupportService

router = APIRouter(tags=["support"])

_STAFF = ("owner", "assistant", "teacher", "instructor")


@log
@router.post("/support", status_code=201)
def create_support(
    data: SupportCreate,
    x_acting_as: Optional[str] = Header(None),
) -> SupportOut:
    ctx = auth_ctx.get()
    result, event = SupportService().create(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        data=data,
    )
    publisher.publish(event, project_id=ctx.project_id, source="support_service")
    return result


@log
@router.get("/support/history")
def get_support_history(
    x_acting_as: Optional[str] = Header(None),
    year: Optional[int] = Query(None),
) -> SupportHistoryOut:
    ctx = auth_ctx.get()
    return SupportService().get_history(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        year=year,
    )


@log
@router.get("/support/dashboard")
@require_roles(*_STAFF)
def get_support_dashboard(
    month: Optional[str] = Query(None),
    type: Optional[str] = Query(None, description="donation | service"),
) -> SupportDashboardOut:
    ctx = auth_ctx.get()
    return SupportService().dashboard(ctx.project_id, month, type)


@log
@router.post("/support/register-received", status_code=201)
@require_roles(*_STAFF)
def register_received_support(data: SupportRegisterReceived) -> SupportOut:
    ctx = auth_ctx.get()
    result, event = SupportService().register_received(
        project_id=ctx.project_id,
        actor_uid=ctx.user_id,
        data=data,
    )
    publisher.publish(event, project_id=ctx.project_id, source="support_service")
    return result


@log
@router.get("/projects/{project_id}/support-config")
def get_support_config(project_id: str) -> SupportConfig:
    return SupportService().get_config(project_id)


@log
@router.patch("/projects/{project_id}/support-config")
@require_roles("owner", "assistant")
def update_support_config(
    project_id: str, data: SupportConfigUpdate,
) -> SupportConfig:
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Projeto não autorizado")
    return SupportService().update_config(project_id, data)
