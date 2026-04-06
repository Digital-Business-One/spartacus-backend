"""Cloud Function: send push notifications via Firebase Cloud Messaging.

Triggered when a document is created in the Firestore `push_queue`
collection. Reads the document, resolves the user's FCM token,
sends the push notification, and updates the document status.

RFC-11: Timeline Assíncrona via Arquitetura de Eventos.
"""

from firebase_admin import firestore, initialize_app, messaging
from firebase_functions import firestore_fn

initialize_app()


def _get_fcm_token(db, to_uid: str) -> str | None:
    """Resolve FCM token from user's push_tokens sub-collection."""
    tokens_ref = db.collection("users").document(to_uid).collection("push_tokens")
    docs = tokens_ref.order_by("updatedAt", direction=firestore.Query.DESCENDING).limit(1).stream()
    for doc in docs:
        return doc.to_dict().get("token")
    return None


@firestore_fn.on_document_created(document="push_queue/{docId}")
def send_push(event: firestore_fn.Event[firestore_fn.DocumentSnapshot]) -> None:
    """Process Firestore document creation and send push via FCM."""
    snapshot = event.data
    if not snapshot:
        print("SKIP: no data in event")
        return

    fields = snapshot.to_dict()
    to_uid = fields.get("to_uid", "")
    title = fields.get("title", "")
    body = fields.get("body", "")
    data = fields.get("data", {})
    actions = fields.get("actions", [])
    source_event_ref = fields.get("source_event_ref", "")

    if not to_uid or not title:
        print(f"SKIP: missing to_uid={to_uid} or title={title}")
        return

    doc_ref = snapshot.reference
    db = firestore.client()

    token = _get_fcm_token(db, to_uid)
    if not token:
        print(f"NO_TOKEN: to_uid={to_uid} source={source_event_ref}")
        doc_ref.update({"status": "no_token"})
        return

    # Build FCM message
    notification = messaging.Notification(title=title, body=body)

    # Attach data payload (includes entity references for deep linking)
    data_payload = {k: str(v) for k, v in data.items() if v is not None}

    # Attach actions as data for client-side handling
    if actions:
        data_payload["actions"] = str(actions)

    android_config = messaging.AndroidConfig(
        priority="high",
        notification=messaging.AndroidNotification(
            click_action="OPEN_TIMELINE",
        ),
    )

    message = messaging.Message(
        notification=notification,
        data=data_payload,
        token=token,
        android=android_config,
    )

    try:
        response = messaging.send(message)
        print(f"OK: to_uid={to_uid} response={response} source={source_event_ref}")
        doc_ref.update({"status": "sent", "fcm_response": response})
    except messaging.UnregisteredError:
        print(f"UNREGISTERED: to_uid={to_uid} — removing stale token")
        doc_ref.update({"status": "unregistered"})
    except Exception as e:
        print(f"ERROR: to_uid={to_uid} err={e}")
        doc_ref.update({"status": "error", "error": str(e)})
        raise
