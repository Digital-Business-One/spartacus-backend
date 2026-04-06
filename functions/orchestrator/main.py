"""Cloud Function: event orchestrator.

Triggered when a document is created in the Firestore `events`
collection. Reads the event, looks up rules, and dispatches to the
appropriate channel (e.g. writes to `notifications` for email).
"""

from datetime import datetime, timezone

from firebase_admin import firestore, initialize_app
from firebase_functions import firestore_fn

initialize_app()

# SendGrid Dynamic Template IDs
_TPL_SIGNUP = "d-d24c02e9d4134c979ddf583d011e0478"
_TPL_NOTIFICATION = "d-6572d5ac5f4346d892e44adef0b998a4"

_FROM_EMAIL = "noreply@spartacus.app.br"
_FROM_NAME = "Spartacus Artes Marciais"

EVENT_RULES: dict[str, dict] = {
    # ── Signup ────────────────────────────────────────────────────────
    "signup.email_confirmation": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_SIGNUP,
            "subject": "Confirme seu e-mail — Spartacus",
        },
    },
    "signup.account_created": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_SIGNUP,
            "subject": "Bem-vindo ao Spartacus!",
        },
    },
    "signup.resend_verification": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_SIGNUP,
            "subject": "Novo link de verificação — Spartacus",
        },
    },
    # ── Account lifecycle ─────────────────────────────────────────────
    "account.approve": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_NOTIFICATION,
            "subject": "Cadastro aprovado — Spartacus",
        },
    },
    "account.reject": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_NOTIFICATION,
            "subject": "Atualização sobre seu cadastro — Spartacus",
        },
    },
    "account.approve_to_medical": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_NOTIFICATION,
            "subject": "Próximo passo: anamnese — Spartacus",
        },
    },
    "account.approve_medical": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_NOTIFICATION,
            "subject": "Anamnese aprovada — Spartacus",
        },
    },
    "account.request_revision": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_NOTIFICATION,
            "subject": "Revisão cadastral solicitada — Spartacus",
        },
    },
    "account.submit_medical_history": {
        "channels": ["email"],
        "email": {
            "template_id": _TPL_NOTIFICATION,
            "subject": "Anamnese enviada — Spartacus",
        },
    },
    # ── Events without action (logged but no dispatch) ────────────────
    "signup.email_verified": {
        "channels": [],
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@firestore_fn.on_document_created(document="events/{docId}")
def event_orchestrator(
    event: firestore_fn.Event[firestore_fn.DocumentSnapshot],
) -> None:
    """Process Firestore document creation in `events` collection."""
    snapshot = event.data
    if not snapshot:
        print("SKIP: no data in event")
        return

    event_data = snapshot.to_dict()
    event_id = event_data.get("eventId", "")
    rule = EVENT_RULES.get(event_id)

    doc_ref = snapshot.reference

    if not rule or not rule.get("channels"):
        doc_ref.update({"status": "processed", "processedAt": _now()})
        print(f"OK: event={event_id} no channels — marked processed")
        return

    payload = event_data.get("payload", {})
    db = firestore.client()

    try:
        for channel in rule["channels"]:
            if channel == "email":
                channel_config = rule["email"]
                to = payload.get("email") or payload.get("to", "")
                if not to:
                    print(f"SKIP: event={event_id} channel=email no recipient")
                    continue
                db.collection("notifications").add({
                    "event_id": event_id,
                    "template_id": channel_config["template_id"],
                    "subject": channel_config["subject"],
                    "to": to,
                    "from_email": _FROM_EMAIL,
                    "from_name": _FROM_NAME,
                    "data": payload,
                    "status": "pending",
                    "source_event_ref": f"events/{snapshot.id}",
                })
                print(f"OK: event={event_id} channel=email to={to}")
            # elif channel == "push": (future)

        doc_ref.update({"status": "processed", "processedAt": _now()})
    except Exception as e:
        print(f"ERROR: event={event_id} err={e}")
        doc_ref.update({
            "status": "failed",
            "processedAt": _now(),
            "error": str(e),
        })
        raise
