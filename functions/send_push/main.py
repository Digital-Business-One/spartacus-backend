"""Cloud Function: send push notifications via Expo Push Service.

Triggered when a document is created in the Firestore `push_queue`
collection. Reads the document, resolves the user's Expo push token,
sends the push notification, and updates the document status.

Uses the Expo Push API (https://exp.host/--/api/v2/push/send) which
accepts ExponentPushToken[...] tokens emitted by expo-notifications
on the client side. Free and works out-of-the-box for both Android
and iOS, no FCM/APNS service account needed.

RFC-11: Timeline Assíncrona via Arquitetura de Eventos.
"""

import json
import urllib.request
from urllib.error import HTTPError, URLError

import functions_framework
from cloudevents.http import CloudEvent
from firebase_admin import firestore, initialize_app

initialize_app()

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def _get_user_tokens(db, to_uid: str) -> list[str]:
    """Resolve all Expo push tokens registered for a user."""
    tokens_ref = (
        db.collection("users").document(to_uid).collection("push_tokens")
    )
    tokens = []
    for doc in tokens_ref.stream():
        data = doc.to_dict()
        token = data.get("token", "")
        if token and token.startswith("ExponentPushToken"):
            tokens.append(token)
    return tokens


def _send_to_expo(messages: list[dict]) -> dict:
    """POST a batch of messages to the Expo Push API."""
    body = json.dumps(messages).encode("utf-8")
    req = urllib.request.Request(
        EXPO_PUSH_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


@functions_framework.cloud_event
def send_push(cloud_event: CloudEvent) -> None:
    """Process Firestore document creation in `push_queue` collection."""
    db = firestore.client()

    subject = cloud_event.get("subject", "")
    doc_path = subject.removeprefix("documents/")
    if not doc_path or not doc_path.startswith("push_queue/"):
        print(f"SKIP: unexpected subject={subject}")
        return

    doc_ref = db.document(doc_path)
    snapshot = doc_ref.get()
    if not snapshot.exists:
        print(f"SKIP: document not found at {doc_path}")
        return

    fields = snapshot.to_dict()
    to_uid = fields.get("to_uid", "")
    title = fields.get("title", "")
    body = fields.get("body", "")
    data = fields.get("data", {})
    source_event_ref = fields.get("source_event_ref", "")

    if not to_uid or not title:
        print(f"SKIP: missing to_uid={to_uid} or title={title}")
        doc_ref.update({"status": "skipped"})
        return

    tokens = _get_user_tokens(db, to_uid)
    if not tokens:
        print(f"NO_TOKEN: to_uid={to_uid} source={source_event_ref}")
        doc_ref.update({"status": "no_token"})
        return

    # Build one message per token
    messages = []
    for token in tokens:
        messages.append({
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "priority": "high",
            "data": {
                "event_id": fields.get("event_id", ""),
                "entity_type": data.get("entity_type", ""),
                "entity_id": data.get("entity_id", ""),
                "source_event_ref": source_event_ref,
            },
        })

    try:
        response = _send_to_expo(messages)
        print(
            f"OK: to_uid={to_uid} tokens={len(tokens)} response={response}"
        )
        doc_ref.update({
            "status": "sent",
            "expo_response": str(response),
        })
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        print(f"HTTP_ERROR: to_uid={to_uid} status={e.code} body={err_body}")
        doc_ref.update({
            "status": "error",
            "error": f"HTTP {e.code}: {err_body}",
        })
        raise
    except URLError as e:
        print(f"URL_ERROR: to_uid={to_uid} reason={e.reason}")
        doc_ref.update({"status": "error", "error": str(e.reason)})
        raise
    except Exception as e:
        print(f"ERROR: to_uid={to_uid} err={e}")
        doc_ref.update({"status": "error", "error": str(e)})
        raise
