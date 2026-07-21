"""Justification-types router — per-project configurable catalog.

GET is available to any authenticated project member: non-staff
(student/guardian) sees only active types, staff sees all (backoffice
CRUD context). PUT/DELETE are staff-only; DELETE deactivates, never
hard-deletes (see `JustificationTypeService`).
"""

from fastapi import APIRouter, HTTPException

from app.domain.enums import STAFF_ROLES
from app.logging.decorator import log
from app.models.justification_type import (
    JustificationType,
    JustificationTypesResponse,
    JustificationTypeUpsertRequest,
)
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.justification_type_service import JustificationTypeService

router = APIRouter(
    prefix="/projects/{project_id}/justification-types",
    tags=["justification-types"],
)


def _assert_project(project_id: str) -> None:
    ctx = auth_ctx.get()
    if ctx is None or ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Acesso negado ao projeto")


@log
@router.get("")
def get_justification_types(project_id: str) -> JustificationTypesResponse:
    _assert_project(project_id)
    ctx = auth_ctx.get()
    is_staff = any(r in STAFF_ROLES for r in ctx.roles)
    types = JustificationTypeService().list_types(
        project_id, include_inactive=is_staff,
    )
    return JustificationTypesResponse(types=types)


@log
@router.put("/{slug}")
@require_roles(*STAFF_ROLES)
def upsert_justification_type(
    project_id: str, slug: str, body: JustificationTypeUpsertRequest,
) -> JustificationType:
    _assert_project(project_id)
    try:
        return JustificationTypeService().upsert(project_id, slug, body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@log
@router.delete("/{slug}")
@require_roles(*STAFF_ROLES)
def deactivate_justification_type(project_id: str, slug: str):
    _assert_project(project_id)
    try:
        JustificationTypeService().deactivate(project_id, slug)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}
