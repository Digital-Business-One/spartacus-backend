from firebase_admin import firestore

from app.logging.decorator import log

_COLLECTION = "notifications"
_FROM_EMAIL = "noreply@spartacus.app.br"
_FROM_NAME = "Spartacus Artes Marciais"


class NotificationAdapter:
    """Write notification to Firestore.

    Eventarc triggers the Cloud Function on document creation —
    no need to publish to PubSub directly.
    """

    @log(mask=["to"])
    def send(
        self,
        event_id: str,
        template_id: str,
        subject: str,
        to: str,
        data: dict,
    ) -> None:
        db = firestore.client()
        db.collection(_COLLECTION).add({
            "event_id": event_id,
            "template_id": template_id,
            "subject": subject,
            "to": to,
            "from_email": _FROM_EMAIL,
            "from_name": _FROM_NAME,
            "data": data,
            "status": "pending",
        })
