from typing import Optional, Union

from fastapi import APIRouter, Header

from app.events import publisher
from app.logging.decorator import log
from app.models.checkin import (
    AvailableCheckinOut,
    CheckinRequest,
    CheckinResponse,
    NoCheckinAvailableOut,
)
from app.security.context import auth_ctx
from app.services.checkin_service import CheckinService

router = APIRouter(prefix="/checkin", tags=["checkin"])


@log
@router.get("/available")
def get_available(
    x_acting_as: Optional[str] = Header(None),
) -> Union[AvailableCheckinOut, NoCheckinAvailableOut]:
    ctx = auth_ctx.get()
    return CheckinService().get_available(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
    )


@log
@router.post("", status_code=201)
def do_checkin(
    data: CheckinRequest,
    x_acting_as: Optional[str] = Header(None),
) -> CheckinResponse:
    ctx = auth_ctx.get()
    result, event = CheckinService().checkin(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        aula_id=data.aula_id,
    )
    publisher.publish(
        event, project_id=ctx.project_id, source="checkin_service",
    )
    return result
