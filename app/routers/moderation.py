"""Moderation router — set/lift moderation level + staff listing (RFC-XX)."""

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.events import publisher
from app.logging.decorator import log
from app.models.moderation import ModeratedUsersPage, ModerationSet
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.moderation_service import ModerationService

router = APIRouter(prefix="/moderation", tags=["moderation"])

_STAFF = ("owner", "assistant", "teacher", "instructor")


@log
@router.get("/users")
@require_roles(*_STAFF)
def list_users(
    q: str = Query(""), status_: str = Query("", alias="status")
) -> ModeratedUsersPage:
    ctx = auth_ctx.get()
    return ModerationService().list_users(ctx, q=q, status=status_)


@log
@router.post("/{uid}", status_code=status.HTTP_201_CREATED)
@require_roles(*_STAFF)
def set_moderation(uid: str, body: ModerationSet) -> Response:
    ctx = auth_ctx.get()
    try:
        event = ModerationService().apply(
            ctx.project_id, uid, ctx, body.level.value, body.reason
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    publisher.publish(event, project_id=ctx.project_id, source="moderation_service")
    return Response(status_code=status.HTTP_201_CREATED)


@log
@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT)
@require_roles(*_STAFF)
def lift_moderation(uid: str) -> Response:
    ctx = auth_ctx.get()
    try:
        event = ModerationService().lift(ctx.project_id, uid, ctx)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    publisher.publish(event, project_id=ctx.project_id, source="moderation_service")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
