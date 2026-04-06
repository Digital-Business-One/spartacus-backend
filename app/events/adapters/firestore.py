from firebase_admin import firestore

from app.events.models import DomainEvent
from app.logging.decorator import log

_COLLECTION = "events"


class FirestoreEventStore:
    """Write domain event to Firestore `events` collection.

    Eventarc triggers the orchestrator Cloud Function on document creation.
    """

    @log(mask=["payload"])
    def publish(
        self, event: DomainEvent, project_id: str, source: str
    ) -> None:
        db = firestore.client()
        db.collection(_COLLECTION).add({
            "eventId": event.id,
            "projectId": project_id,
            "source": source,
            "payload": event.payload.personalization(),
            "occurredAt": event.occurred_at.isoformat(),
            "status": "pending",
            "processedAt": None,
            "error": None,
        })
