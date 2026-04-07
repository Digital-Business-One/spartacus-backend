from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.events import publisher
from app.logging.decorator import log
from app.models.medical_history import (
    MedicalHistoryOut,
    MedicalHistoryRequest,
    PendingAnamneseList,
)
from app.security.context import ADMIN_ROLES, auth_ctx
from app.services.medical_history_service import MedicalHistoryService

router = APIRouter(prefix="/medical-history", tags=["medical-history"])


@log
@router.get("/pending")
def list_pending_anamneses() -> PendingAnamneseList:
    """List medical histories pending for the current user + dependents.

    Returns self if status is waiting_medical_history, plus any
    dependents (where current user is guardian) in the same status.
    Used by the app to drive a multi-target anamnese flow.
    """
    ctx = auth_ctx.get()
    if ctx is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    pending = MedicalHistoryService().list_pending(
        ctx.project_id, ctx.user_id
    )
    return PendingAnamneseList(pending=pending)


@log
@router.post("", status_code=201)
def submit_medical_history(
    body: MedicalHistoryRequest,
    x_acting_as: Optional[str] = Header(None),
) -> MedicalHistoryOut:
    """Submit medical history form (user action).

    No @require_roles — students in waiting_medical_history have no
    custom claims yet. Auth is validated via Firebase JWT (ctx.user_id)
    and the state machine rejects invalid transitions.

    If X-Acting-As header is set, submits on behalf of a dependent
    (guardian filling for minor). Service validates the relationship.
    """
    ctx = auth_ctx.get()
    if ctx is None:
        raise HTTPException(status_code=401, detail="Não autenticado")

    target_uid = x_acting_as or ctx.user_id
    try:
        result, event = MedicalHistoryService().submit(
            ctx.project_id, target_uid, body, ctx.user_id
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if event:
        publisher.publish(
            event,
            project_id=ctx.project_id,
            source="medical_history_service",
        )
    return result


@log
@router.get("/{user_id}")
def get_medical_history(user_id: str) -> MedicalHistoryOut:
    """Get medical history for a user.

    Team members (owner/assistant) can read any user's form.
    Users can read their own form.
    """
    ctx = auth_ctx.get()
    if ctx is None:
        raise HTTPException(status_code=401, detail="Não autenticado")

    is_self = ctx.user_id == user_id
    is_admin = any(r in ADMIN_ROLES for r in ctx.roles)
    if not is_self and not is_admin:
        raise HTTPException(status_code=403, detail="Permissão insuficiente")

    result = MedicalHistoryService().get(ctx.project_id, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Anamnese não encontrada")
    return result
