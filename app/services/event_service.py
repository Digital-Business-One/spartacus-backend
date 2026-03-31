from typing import Optional

from firebase_admin import firestore

from app.logging.decorator import log
from app.models.event import EventOut


class EventService:
    _COLLECTION = "events"

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
            start = data.get("startDate", "")

            # Filter by month if provided (YYYY-MM)
            if month and start:
                event_month = start[:7]  # "2026-04-15..." → "2026-04"
                if event_month != month:
                    continue

            results.append(
                EventOut(
                    id=doc.id,
                    title=data.get("title", ""),
                    type=data.get("type", "event"),
                    start_date=start,
                    end_date=data.get("endDate"),
                    location=data.get("location"),
                    description=data.get("description"),
                )
            )

        return results
