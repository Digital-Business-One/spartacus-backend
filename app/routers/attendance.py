from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.logging.decorator import log
from app.models.attendance import (
    AttendanceActionOut,
    AttendanceActionRequest,
    AttendanceDashboardOut,
    AttendanceHistoryOut,
)
from app.security.context import auth_ctx
from app.security.decorator import require_roles
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


# ── RFC-14: Dashboard de Frequência ────────────────────────────────────────

@log
@router.get("/dashboard/{class_id}")
@require_roles("owner", "assistant", "teacher", "instructor")
def get_attendance_dashboard(class_id: str) -> AttendanceDashboardOut:
    ctx = auth_ctx.get()
    return AttendanceService().list_today_for_class(
        project_id=ctx.project_id, class_id=class_id,
    )


@log
@router.post("/confirm")
@require_roles("owner", "assistant", "teacher", "instructor")
def confirm_attendance(data: AttendanceActionRequest) -> AttendanceActionOut:
    ctx = auth_ctx.get()
    if not data.class_id or not data.user_id:
        raise HTTPException(
            status_code=422, detail="class_id e user_id são obrigatórios",
        )
    return AttendanceService().confirm_attendance(
        project_id=ctx.project_id,
        class_id=data.class_id,
        user_id=data.user_id,
        actor_uid=ctx.user_id,
        source=data.source or "manual",
        aula_id=data.aula_id,
    )


@log
@router.post("/reject")
@require_roles("owner", "assistant", "teacher", "instructor")
def reject_attendance(data: AttendanceActionRequest) -> AttendanceActionOut:
    ctx = auth_ctx.get()
    if not data.class_id or not data.user_id:
        raise HTTPException(
            status_code=422, detail="class_id e user_id são obrigatórios",
        )
    return AttendanceService().reject_attendance(
        project_id=ctx.project_id,
        class_id=data.class_id,
        user_id=data.user_id,
        actor_uid=ctx.user_id,
        reason=data.reason,
        aula_id=data.aula_id,
    )
