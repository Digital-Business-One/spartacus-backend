from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

EventType = Literal["event", "championship"]


class EventOut(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True, alias_generator=to_camel,
    )

    id: str
    title: str
    type: EventType
    start_date: str
    end_date: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    # backoffice calendar extras
    event_category: Optional[str] = None  # own | external | guest_class
    modality_id: Optional[str] = None
    organizer: Optional[str] = None
    registration_link: Optional[str] = None


class EventsResponse(BaseModel):
    events: list[EventOut]
