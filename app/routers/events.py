from typing import Optional

from fastapi import APIRouter, Query

from app.logging.decorator import log
from app.models.event import EventsResponse
from app.security.decorator import public
from app.services.event_service import EventService

router = APIRouter(
    prefix="/projects/{project_id}/events",
    tags=["events"],
)


@log
@router.get("")
@public
def list_events(
    project_id: str,
    month: Optional[str] = Query(None, description="YYYY-MM"),
) -> EventsResponse:
    events = EventService().list_by_project(project_id, month)
    return EventsResponse(events=events)
