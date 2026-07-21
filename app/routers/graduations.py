"""Graduations router — matrix, staff dashboard, approve & promote."""

from fastapi import APIRouter, HTTPException

from app.events import publisher
from app.logging.decorator import log
from app.models.graduation_system import (
    GraduationActionRequest,
    GraduationDashboardOut,
    GraduationSystemsResponse,
    RosterOut,
)
from app.security.context import auth_ctx
from app.security.decorator import public, require_roles
from app.services.graduation_service import GraduationService

router = APIRouter(tags=["graduations"])

_STAFF = ("owner", "assistant", "teacher", "instructor")


@log
@router.get("/projects/{project_id}/graduation-systems")
@public
def get_graduation_systems(project_id: str) -> GraduationSystemsResponse:
    return GraduationSystemsResponse(
        systems=GraduationService().get_systems(project_id),
    )


@log
@router.get("/graduations/dashboard")
@require_roles(*_STAFF)
def get_graduations_dashboard(
    modality: str | None = None,
    view: str | None = None,
) -> GraduationDashboardOut | RosterOut:
    ctx = auth_ctx.get()
    if view == "roster":
        return GraduationService().build_roster(ctx.project_id)
    if not modality:
        raise HTTPException(status_code=422, detail="modality é obrigatório")
    return GraduationService().dashboard(ctx.project_id, modality)


@log
@router.post("/graduations/{uid}/approve")
@require_roles(*_STAFF)
def approve_graduation(uid: str, body: GraduationActionRequest):
    ctx = auth_ctx.get()
    try:
        event = GraduationService().approve(
            project_id=ctx.project_id,
            uid=uid,
            modality=body.modality,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    publisher.publish(event, project_id=ctx.project_id, source="graduation_service")
    return {"status": "ok"}


@log
@router.post("/graduations/{uid}/reject")
@require_roles(*_STAFF)
def reject_graduation(uid: str, body: GraduationActionRequest):
    """Reprova a graduação informada pelo aluno (libera para reedição)."""
    ctx = auth_ctx.get()
    try:
        event = GraduationService().reject(
            project_id=ctx.project_id,
            uid=uid,
            modality=body.modality,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    publisher.publish(event, project_id=ctx.project_id, source="graduation_service")
    return {"status": "ok"}


@log
@router.post("/graduations/{uid}/undo")
@require_roles(*_STAFF)
def undo_graduation(uid: str, body: GraduationActionRequest):
    """Desfaz a última graduação registrada (correção). Sem push."""
    ctx = auth_ctx.get()
    try:
        GraduationService().undo(
            project_id=ctx.project_id,
            uid=uid,
            modality=body.modality,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"status": "ok"}


@log
@router.post("/graduations/{uid}/promote")
@require_roles(*_STAFF)
def promote_graduation(uid: str, body: GraduationActionRequest):
    ctx = auth_ctx.get()
    if body.kind not in ("degree", "belt"):
        raise HTTPException(status_code=422, detail="kind inválido")
    try:
        event = GraduationService().promote(
            project_id=ctx.project_id,
            uid=uid,
            modality=body.modality,
            actor_uid=ctx.user_id,
            kind=body.kind,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    publisher.publish(event, project_id=ctx.project_id, source="graduation_service")
    return {"status": "ok"}
