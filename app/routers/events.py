from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.events import publisher
from app.logging.decorator import log
from app.models.event import EventsResponse
from app.models.post import PostCreate
from app.security.context import auth_ctx
from app.security.decorator import public
from app.services.event_service import EventService, can_create_event
from app.services.post_service import PostService

router = APIRouter(
    prefix="/projects/{project_id}/events",
    tags=["events"],
)


def _author_name(uid: str) -> str:
    from firebase_admin import firestore

    db = firestore.client()
    doc = db.collection("users").document(uid).get()
    return doc.to_dict().get("name", "") if doc.exists else ""


@log
@router.get("")
@public
def list_events(
    project_id: str,
    month: Optional[str] = Query(None, description="YYYY-MM"),
) -> EventsResponse:
    events = EventService().list_by_project(project_id, month)
    return EventsResponse(events=events)


@log
@router.post("", status_code=201)
def create_event(project_id: str, data: PostCreate):
    """Create a calendar event from the backoffice.

    Reuses the posts pipeline (timeline + calendar + push). Permission:
    must hold the `social` role AND a staff role.
    """
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Projeto não autorizado")
    if not can_create_event(ctx.roles):
        raise HTTPException(
            status_code=403,
            detail="Permissão insuficiente (requer staff + social)",
        )
    # Calendar events are always event-typed posts with a category.
    if data.type == "post":
        data.type = "event"
    result, event = PostService().create(
        project_id=ctx.project_id,
        data=data,
        author_uid=ctx.user_id,
        author_name=_author_name(ctx.user_id),
        author_roles=ctx.roles,
    )
    publisher.publish(event, project_id=ctx.project_id, source="event_service")
    return result
