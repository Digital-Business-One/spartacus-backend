"""Timeline router — feed, reactions, link preview (RFC-11)."""

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from app.logging.decorator import log
from app.models.timeline import LinkPreviewResponse, TimelineFeedResponse
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.link_preview_service import LinkPreviewService
from app.services.timeline_service import TimelineService

router = APIRouter(prefix="/timeline", tags=["timeline"])


@log
@router.get("")
def get_feed(
    type: Optional[str] = Query(None, description="Filter by entry type"),
    cursor: Optional[str] = Query(None, description="Pagination cursor (createdAt)"),
    limit: int = Query(5, ge=1, le=50),
    x_acting_as: Optional[str] = Header(None),
) -> TimelineFeedResponse:
    ctx = auth_ctx.get()
    entries, next_cursor = TimelineService().get_feed(
        project_id=ctx.project_id,
        user=ctx,
        cursor=cursor,
        type_filter=type,
        limit=limit,
        acting_as=x_acting_as,
    )
    return TimelineFeedResponse(entries=entries, next_cursor=next_cursor)


@log
@router.post("/{entry_id}/reactions", status_code=204)
def like_entry(entry_id: str):
    ctx = auth_ctx.get()
    try:
        TimelineService().like(entry_id, ctx.user_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@log
@router.delete("/{entry_id}/reactions", status_code=204)
def unlike_entry(entry_id: str):
    ctx = auth_ctx.get()
    TimelineService().unlike(entry_id, ctx.user_id)


@log
@router.get("/link-preview")
@require_roles("social")
def get_link_preview(url: str = Query(...)) -> LinkPreviewResponse:
    result = LinkPreviewService().fetch(url)
    return LinkPreviewResponse(**result)
