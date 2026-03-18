from firebase_admin import firestore

from app.logging.decorator import log

_COLLECTION = "emails"
_FROM_EMAIL = "noreply@spartacus.app.br"
_FROM_NAME = "Spartacus Artes Marciais"


class FirestoreMailAdapter:
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
        db.collection(_COLLECTION).add(
            {
                "to": [{"email": to}],
                "from": {"email": _FROM_EMAIL, "name": _FROM_NAME},
                "subject": subject,
                "template_id": template_id,
                "personalization": [{"email": to, "data": data}],
                "tags": [event_id],
            }
        )
