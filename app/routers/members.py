from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.logging.decorator import log
from app.models.membership import (
    AssignRoleRequest,
    AssignRoleResponse,
    EligibleUserOut,
    MembershipCreate,
    MembershipOut,
    MembershipUpdate,
)
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.membership_service import MembershipService

router = APIRouter(prefix="/projects/{project_id}/members", tags=["members"])


def _assert_project(project_id: str) -> None:
    """Garante que X-Project-Id bate com o projeto da rota."""
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Acesso negado ao projeto")


@log
@router.post("", status_code=201)
@require_roles("owner", "assistant")
def add_member(project_id: str, data: MembershipCreate) -> MembershipOut:
    _assert_project(project_id)
    try:
        return MembershipService().add(project_id, data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@log
@router.get("")
@require_roles("owner", "assistant")
def list_members(project_id: str) -> list[MembershipOut]:
    _assert_project(project_id)
    return MembershipService().list_active(project_id)


@log
@router.patch("/{user_id}")
@require_roles("owner", "assistant")
def update_member(
    project_id: str, user_id: str, data: MembershipUpdate
) -> MembershipOut:
    _assert_project(project_id)
    try:
        return MembershipService().update(project_id, user_id, data)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ─── IAM endpoints (RFC-13) ──────────────────────────────────────────────────


@log
@router.get("/eligible")
@require_roles("owner", "assistant")
def list_eligible(
    project_id: str,
    role: str = Query(..., description="Role to check eligibility for"),
    search: Optional[str] = Query(None),
) -> list[EligibleUserOut]:
    """Return approved accounts that do NOT have the given role."""
    _assert_project(project_id)
    return MembershipService().list_eligible(project_id, role, search)


@log
@router.post("/assign-role")
@require_roles("owner", "assistant")
def assign_role(
    project_id: str, data: AssignRoleRequest
) -> AssignRoleResponse:
    """Add a role to multiple users at once."""
    _assert_project(project_id)
    assigned, skipped = MembershipService().assign_role(
        project_id, data.role, data.user_ids,
    )
    return AssignRoleResponse(assigned=assigned, skipped=skipped)
