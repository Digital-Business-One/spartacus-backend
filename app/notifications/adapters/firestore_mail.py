import json
import os

from firebase_admin import firestore
from google.cloud import pubsub_v1

from app.logging.decorator import log

_COLLECTION = "notifications"
_FROM_EMAIL = "noreply@spartacus.app.br"
_FROM_NAME = "Spartacus Artes Marciais"
_TOPIC = "email-notifications"


class NotificationAdapter:
    """Write audit to Firestore + publish to PubSub for async email delivery."""

    def __init__(self):
        self._publisher = None

    def _get_publisher(self):
        if self._publisher is None:
            self._publisher = pubsub_v1.PublisherClient()
        return self._publisher

    def _topic_path(self) -> str:
        project = os.environ.get(
            "GOOGLE_CLOUD_PROJECT", "spartacus-artes-marciais"
        )
        return f"projects/{project}/topics/{_TOPIC}"

    @log(mask=["to"])
    def send(
        self,
        event_id: str,
        template_id: str,
        subject: str,
        to: str,
        data: dict,
    ) -> None:
        payload = {
            "event_id": event_id,
            "template_id": template_id,
            "subject": subject,
            "to": to,
            "from_email": _FROM_EMAIL,
            "from_name": _FROM_NAME,
            "data": data,
        }

        # 1. Audit trail in Firestore
        db = firestore.client()
        db.collection(_COLLECTION).add(payload)

        # 2. Publish to PubSub (skip in dev/emulator)
        if os.environ.get("FIRESTORE_EMULATOR_HOST"):
            return

        publisher = self._get_publisher()
        publisher.publish(
            self._topic_path(),
            json.dumps(payload).encode("utf-8"),
        )
