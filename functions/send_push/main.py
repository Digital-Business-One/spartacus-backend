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
import os
import urllib.request
from urllib.error import HTTPError, URLError

import firebase_admin
from firebase_admin import credentials as _creds
from firebase_admin import firestore
from firebase_functions import firestore_fn, options

# Runtime options for `firebase deploy` (region/memory/timeout/SA).
options.set_global_options(
    region="us-east1",
    memory=options.MemoryOption.MB_256,
    timeout_sec=30,
    service_account="fn-send-email@spartacus-artes-marciais.iam.gserviceaccount.com",
)

if os.environ.get("FUNCTIONS_EMULATOR"):
    os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")
    os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")

    from google.auth.credentials import AnonymousCredentials

    class _EmulatorCred(_creds.Base):
        def get_credential(self):
            return AnonymousCredentials()

    firebase_admin.initialize_app(
        credential=_EmulatorCred(),
        options={"projectId": os.environ.get(
            "GCLOUD_PROJECT", "spartacus-artes-marciais"
        )},
    )
else:
    firebase_admin.initialize_app()

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


@firestore_fn.on_document_created(document="push_queue/{docId}")
def send_push(
    event: firestore_fn.Event[firestore_fn.DocumentSnapshot],
) -> None:
    """Process Firestore document creation in `push_queue` collection."""
    db = firestore.client()

    snapshot = event.data
    if not snapshot.exists:
        print("SKIP: document does not exist")
        return

    doc_path = f"push_queue/{event.params['docId']}"
    doc_ref = db.document(doc_path)
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
