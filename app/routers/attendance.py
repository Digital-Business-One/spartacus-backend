from typing import Optional

from fastapi import APIRouter, Header

from app.logging.decorator import log
from app.models.attendance import AttendanceHistoryOut
from app.security.context import auth_ctx
from app.services.attendance_service import AttendanceService

router = APIRouter(prefix="/attendance", tags=["attendance"])


@log
@router.get("/history")
def get_attendance_history(
    x_acting_as: Optional[str] = Header(None),
) -> AttendanceHistoryOut:
    ctx = auth_ctx.get()
    return AttendanceService().get_history(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
    )
