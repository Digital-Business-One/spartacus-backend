from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.events import publisher
from app.logging.decorator import log
from app.models.account import (
    AccountDetailOut,
    AccountListPage,
    TransitionRequest,
    TransitionResponse,
)
from app.models.account_history import AccountHistoryPage
from app.models.attendance import AttendanceHistoryOut
from app.models.donation import DonationHistoryOut
from app.models.medical_history import MedicalHistoryOut
from app.models.profile import DependentOut
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.account_history_service import AccountHistoryService
from app.services.account_service import AccountService
from app.services.attendance_service import AttendanceService
from app.services.donation_service import DonationService
from app.services.medical_history_service import MedicalHistoryService
from app.services.profile_service import ProfileService

router = APIRouter(prefix="/accounts", tags=["accounts"])


# ─── Listagem (paginada) + detalhe ────────────────────────────────────────────


@log
@router.get("")
@require_roles("owner", "assistant")
def list_accounts(
    status: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query(
        None,
        description="name | name_desc | age | age_desc",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50, alias="pageSize"),
) -> AccountListPage:
    ctx = auth_ctx.get()
    return AccountService().list_accounts_paginated(
        project_id=ctx.project_id,
        status_filter=status,
        role_filter=role,
        search=search,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@log
@router.get("/{uid}")
@require_roles("owner", "assistant")
def get_account(uid: str) -> AccountDetailOut:
    ctx = auth_ctx.get()
    account = AccountService().get_account_detail(ctx.project_id, uid)
    if account is None:
        raise HTTPException(
            status_code=404, detail="Conta não encontrada"
        )
    return account


@log
@router.post("/{uid}/transitions")
@require_roles("owner", "assistant")
def execute_transition(
    uid: str, body: TransitionRequest
) -> TransitionResponse:
    ctx = auth_ctx.get()
    try:
        result, event = AccountService().execute_transition(
            ctx.project_id, uid, body.action, ctx.user_id
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if event:
        publisher.publish(
            event, project_id=ctx.project_id, source="account_service"
        )
    return result


# ─── Endpoints admin RFC-12 (sem X-Acting-As) ─────────────────────────────────


@log
@router.get("/{uid}/medical-history")
@require_roles("owner", "assistant")
def get_account_medical_history(uid: str) -> MedicalHistoryOut:
    ctx = auth_ctx.get()
    try:
        return MedicalHistoryService().get_admin(
            project_id=ctx.project_id, user_id=uid,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@log
@router.get("/{uid}/attendance/history")
@require_roles("owner", "assistant")
def get_account_attendance_history(
    uid: str,
    year: Optional[int] = Query(None),
    months: Optional[str] = Query(
        None, description="CSV de meses (1-12), ex: '3,4,5'",
    ),
    statuses: Optional[str] = Query(
        None,
        description="CSV de statuses, ex: 'registered,confirmed,absent'",
    ),
) -> AttendanceHistoryOut:
    ctx = auth_ctx.get()
    months_list = _parse_int_csv(months)
    statuses_list = [s.strip() for s in statuses.split(",")] if statuses else None
    return AttendanceService().get_history_admin(
        project_id=ctx.project_id,
        target_uid=uid,
        year=year,
        months=months_list,
        statuses=statuses_list,
    )


@log
@router.get("/{uid}/donations/history")
@require_roles("owner", "assistant")
def get_account_donations_history(
    uid: str,
    year: Optional[int] = Query(None),
) -> DonationHistoryOut:
    ctx = auth_ctx.get()
    return DonationService().get_history_admin(
        project_id=ctx.project_id, target_uid=uid, year=year,
    )


@log
@router.get("/{uid}/dependents")
@require_roles("owner", "assistant")
def get_account_dependents(uid: str) -> list[DependentOut]:
    ctx = auth_ctx.get()
    return ProfileService().get_dependents_admin(
        project_id=ctx.project_id, guardian_uid=uid,
    )


@log
@router.get("/{uid}/history")
@require_roles("owner", "assistant")
def get_account_history(
    uid: str,
    year: Optional[int] = Query(None),
    months: Optional[str] = Query(None),
    types: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50, alias="pageSize"),
) -> AccountHistoryPage:
    ctx = auth_ctx.get()
    months_list = _parse_int_csv(months)
    types_list = [t.strip() for t in types.split(",")] if types else None
    return AccountHistoryService().query(
        uid=uid,
        project_id=ctx.project_id,
        year=year,
        months=months_list,
        types=types_list,
        page=page,
        page_size=page_size,
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _parse_int_csv(value: Optional[str]) -> Optional[list[int]]:
    if not value:
        return None
    try:
        return [int(v.strip()) for v in value.split(",") if v.strip()]
    except ValueError:
        return None
