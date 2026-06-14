import re
from typing import Optional

from firebase_admin import firestore

from app.logging.decorator import log
from app.models.event import EventOut

# Legacy rows written before the frontend switched to ISO-8601 have dates
# as "DD/MM/YYYY[ HH:mm]". Detect and convert them so the filter and the
# frontend (which parses with `new Date(...)`) both work transparently.
_BR_DATE = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{2}):(\d{2}))?$",
)


def _to_iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    m = _BR_DATE.match(value)
    if not m:
        return value  # already ISO or unknown format
    d, mo, y, hh, mm = m.groups()
    time = f"T{hh}:{mm}:00" if hh and mm else "T00:00:00"
    return f"{y}-{mo}-{d}{time}"


_STAFF_ROLES = {"owner", "assistant", "teacher", "instructor"}


def can_create_event(roles: list[str]) -> bool:
    """Event creation requires BOTH the `social` role and a staff role."""
    rset = set(roles or [])
    return "social" in rset and bool(_STAFF_ROLES & rset)


class EventService:
    # Written by the orchestrator Cloud Function when a post of type
    # event/championship is created. NOT the "events" collection (that one
    # is the domain-event pubsub stream for the backend itself).
    _COLLECTION = "eventos_calendario"

    @log
    def list_by_project(
        self,
        project_id: str,
        month: Optional[str] = None,
    ) -> list[EventOut]:
        db = firestore.client()
        query = db.collection(self._COLLECTION).where(
            "projectId", "==", project_id,
        )

        docs = list(query.stream())

        results: list[EventOut] = []
        for doc in docs:
            data = doc.to_dict()
            start_iso = _to_iso(data.get("startDate", ""))

            # Filter by month if provided (YYYY-MM)
            if month and start_iso:
                if start_iso[:7] != month:
                    continue

            results.append(
                EventOut(
                    id=doc.id,
                    title=data.get("title", ""),
                    type=data.get("type", "event"),
                    start_date=start_iso or "",
                    end_date=_to_iso(data.get("endDate")),
                    location=data.get("location"),
                    description=data.get("description"),
                    event_category=data.get("eventCategory"),
                    modality_id=data.get("modalityId"),
                    organizer=data.get("organizer"),
                    registration_link=data.get("registrationLink"),
                )
            )

        return results
