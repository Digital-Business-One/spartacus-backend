from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.events import publisher
from app.logging.decorator import log
from app.models.account import AccountOut, TransitionRequest, TransitionResponse
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.account_service import AccountService

router = APIRouter(prefix="/accounts", tags=["accounts"])


@log
@router.get("")
@require_roles("owner", "assistant")
def list_accounts(
    status: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
) -> list[AccountOut]:
    ctx = auth_ctx.get()
    return AccountService().list_accounts(
        ctx.project_id, status, role, search
    )


@log
@router.get("/{uid}")
@require_roles("owner", "assistant")
def get_account(uid: str) -> AccountOut:
    ctx = auth_ctx.get()
    account = AccountService().get_account(ctx.project_id, uid)
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
