"""Cloud Function: event orchestrator.

Triggered when a document is created in the Firestore `events`
collection. Reads the event, looks up rules, and dispatches to
channels: email, timeline, calendar, push.

RFC-10 (base) + RFC-11 (timeline, calendar, push channels).
"""

import os
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials as _creds
from firebase_admin import firestore
from firebase_functions import firestore_fn

# When running inside Firebase Emulator, firebase-admin's initialize_app()
# tries google.auth.default() which fails without ADC. Provide an anonymous
# credential so the SDK initializes without real keys — all calls go to
# localhost emulators anyway.
if os.environ.get("FUNCTIONS_EMULATOR"):
    os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")
    os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
    os.environ.setdefault("FIREBASE_STORAGE_EMULATOR_HOST", "localhost:9199")

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

# ─── Constants ────────────────────────────────────────────────────────────────

_TPL_SIGNUP = "d-d24c02e9d4134c979ddf583d011e0478"
_TPL_NOTIFICATION = "d-6572d5ac5f4346d892e44adef0b998a4"
_FROM_EMAIL = "noreply@spartacus.app.br"
_FROM_NAME = "Spartacus Artes Marciais"

# ─── EVENT_RULES ──────────────────────────────────────────────────────────────

EVENT_RULES: dict[str, dict] = {

    # ══ POSTS (post, event, championship) ═════════════════════════════════════

    "post.created": {
        "channels": ["timeline", "calendar"],
        "timeline": {
            "action": "create",
            "visibility": "public",
            "id_prefix": "post",
        },
        "calendar": {
            "action": "create",
            "only_types": ["event", "championship"],
        },
    },
    "post.updated": {
        "channels": ["timeline", "calendar"],
        "timeline": {
            "action": "update",
            "id_prefix": "post",
        },
        "calendar": {
            "action": "update",
            "only_types": ["event", "championship"],
        },
    },
    "post.deleted": {
        "channels": ["timeline", "calendar"],
        "timeline": {
            "action": "update",
            "id_prefix": "post",
            "update_fields": {"status": "deleted"},
        },
        "calendar": {
            "action": "delete",
            "only_types": ["event", "championship"],
        },
    },

    # ══ PRESENÇA ══════════════════════════════════════════════════════════════

    "checkin.registered": {
        "channels": ["timeline", "push"],
        "timeline": {
            "action": "create",
            "type": "attendance",
            "visibility": "personal_and_staff",
            "id_prefix": "presenca",
            "initial_status": "pending",
        },
        "push": {
            "target": "staff_actionable",
            "title_template": "{author_name} registrou presença",
            "body_template": "{turma_name} - {class_date}",
            "actions": [
                {"title": "Confirmar", "action": "CONFIRM"},
                {"title": "Ausência", "action": "REJECT"},
            ],
        },
    },
    "checkin.confirmed": {
        "channels": ["timeline", "push"],
        "timeline": {
            "action": "update",
            "id_prefix": "presenca",
            "update_fields": {"validationStatus": "confirmed"},
        },
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Presença validada",
            "body_template": "Sua presença em {turma_name} foi confirmada",
        },
    },
    "checkin.absent": {
        "channels": ["timeline", "push"],
        "timeline": {
            "action": "update_or_create",
            "type": "attendance",
            "visibility": "personal_and_staff",
            "id_prefix": "presenca",
            "update_fields": {"validationStatus": "absent"},
        },
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Presença não confirmada",
            "body_template": "Registro de presença em {turma_name} não foi confirmado",
        },
    },
    "checkin.review_requested": {
        "channels": ["timeline", "push"],
        "timeline": {
            "action": "update",
            "id_prefix": "presenca",
            "update_fields": {"reviewRequested": True},
        },
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Revisão de presença solicitada",
            "body_template": "Solicitação de revisão em {turma_name} registrada",
        },
    },

    # ══ DOAÇÕES ═══════════════════════════════════════════════════════════════

    "donation.registered": {
        "channels": ["timeline", "push"],
        "timeline": {
            "action": "create",
            "type": "donation",
            "visibility": "personal_and_staff",
            "id_prefix": "doacao",
            "initial_status": "pending",
        },
        "push": {
            "target": "staff_actionable",
            "title_template": "{author_name} registrou doação",
            "body_template": "{donation_amount}",
            "actions": [
                {"title": "Confirmar", "action": "CONFIRM"},
                {"title": "Ausência", "action": "REJECT"},
            ],
        },
    },
    "donation.received": {
        "channels": ["timeline", "push"],
        "timeline": {
            "action": "update",
            "id_prefix": "doacao",
            "update_fields": {"validationStatus": "confirmed"},
        },
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Doação validada",
            "body_template": "Sua doação de {donation_amount} foi confirmada",
        },
    },
    "donation.absent": {
        "channels": ["timeline", "push"],
        "timeline": {
            "action": "update",
            "id_prefix": "doacao",
            "update_fields": {"validationStatus": "absent"},
        },
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Doação não confirmada",
            "body_template": "Seu registro de doação não foi confirmado",
        },
    },
    "donation.review_requested": {
        "channels": ["timeline", "push"],
        "timeline": {
            "action": "update",
            "id_prefix": "doacao",
            "update_fields": {"reviewRequested": True},
        },
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Revisão de doação solicitada",
            "body_template": "Solicitação de revisão de doação registrada",
        },
    },

    # ══ CONTAS — existentes (email) + timeline/push novos ═════════════════════

    "signup.email_confirmation": {
        "channels": ["email", "timeline", "push"],
        "email": {"template_id": _TPL_SIGNUP, "subject": "Confirme seu e-mail — Spartacus"},
        "timeline": {
            "action": "create",
            "type": "account_created",
            "visibility": "staff_only",
            "id_prefix": "account",
        },
        "push": {
            "target": "staff",
            "title_template": "Novo cadastro",
            "body_template": "{name} criou uma conta",
        },
    },
    "signup.account_created": {
        "channels": ["email", "timeline", "push"],
        "email": {"template_id": _TPL_SIGNUP, "subject": "Bem-vindo ao Spartacus!"},
        "timeline": {
            "action": "create",
            "type": "account_created",
            "visibility": "staff_only",
            "id_prefix": "account",
        },
        "push": {
            "target": "staff",
            "title_template": "Novo cadastro",
            "body_template": "{name} criou uma conta",
        },
    },
    "signup.resend_verification": {
        "channels": ["email"],
        "email": {"template_id": _TPL_SIGNUP, "subject": "Novo link de verificação — Spartacus"},
    },
    "signup.email_verified": {
        "channels": [],
    },
    "account.approve": {
        "channels": ["email", "push"],
        "email": {"template_id": _TPL_NOTIFICATION, "subject": "Cadastro aprovado — Spartacus"},
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Cadastro aprovado!",
            "body_template": "Bem-vindo ao Spartacus",
        },
    },
    "account.reject": {
        "channels": ["email", "push"],
        "email": {"template_id": _TPL_NOTIFICATION, "subject": "Atualização sobre seu cadastro — Spartacus"},
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Atualização do cadastro",
            "body_template": "Seu cadastro no Spartacus foi atualizado",
        },
    },
    "account.approve_to_medical": {
        "channels": ["email", "push"],
        "email": {"template_id": _TPL_NOTIFICATION, "subject": "Próximo passo: anamnese — Spartacus"},
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Próximo passo: anamnese",
            "body_template": "Preencha a ficha de anamnese para concluir seu cadastro",
        },
    },
    "account.approve_medical": {
        "channels": ["email", "push"],
        "email": {"template_id": _TPL_NOTIFICATION, "subject": "Anamnese aprovada — Spartacus"},
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Anamnese aprovada",
            "body_template": "Sua ficha de anamnese foi aprovada",
        },
    },
    "account.request_revision": {
        "channels": ["email", "push"],
        "email": {"template_id": _TPL_NOTIFICATION, "subject": "Revisão cadastral solicitada — Spartacus"},
        "push": {
            "target": "owner_and_guardian",
            "title_template": "Revisão cadastral",
            "body_template": "Uma revisão foi solicitada em seu cadastro",
        },
    },
    "account.submit_medical_history": {
        "channels": ["email"],
        "email": {"template_id": _TPL_NOTIFICATION, "subject": "Anamnese enviada — Spartacus"},
    },
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_format(template: str, payload: dict) -> str:
    """Format template with payload, ignoring missing keys."""
    try:
        return template.format(**payload)
    except (KeyError, IndexError):
        return template


def _resolve_guardians(db, target_uid: str, project_id: str) -> list[str]:
    """Find guardian UIDs for a target user (dependent)."""
    memberships = (
        db.collection("memberships")
        .where("projectId", "==", project_id)
        .where("status", "==", "active")
        .stream()
    )
    guardian_uids = []
    for m in memberships:
        data = m.to_dict()
        if "guardian" in data.get("roles", []):
            # Check if this guardian has the target as dependent
            user_doc = db.collection("users").document(data["userId"]).get()
            if user_doc.exists:
                dependents = user_doc.to_dict().get("dependentUids", [])
                if target_uid in dependents:
                    guardian_uids.append(data["userId"])
    return guardian_uids


def _resolve_staff_uids(db, project_id: str) -> list[str]:
    """Find all staff member UIDs for a project."""
    staff_roles = {"owner", "assistant", "teacher", "instructor"}
    memberships = (
        db.collection("memberships")
        .where("projectId", "==", project_id)
        .where("status", "==", "active")
        .stream()
    )
    uids = []
    for m in memberships:
        data = m.to_dict()
        if staff_roles & set(data.get("roles", [])):
            uids.append(data["userId"])
    return uids


# ─── Channel Handlers ────────────────────────────────────────────────────────

def _handle_email(rule, event_id, payload, doc_path, db):
    config = rule["email"]
    to = payload.get("email") or payload.get("to", "")
    if not to:
        print(f"SKIP: event={event_id} channel=email no recipient")
        return
    db.collection("notifications").add({
        "event_id": event_id,
        "template_id": config["template_id"],
        "subject": config["subject"],
        "to": to,
        "from_email": _FROM_EMAIL,
        "from_name": _FROM_NAME,
        "data": payload,
        "status": "pending",
        "source_event_ref": doc_path,
    })
    print(f"OK: event={event_id} channel=email to={to}")


def _handle_timeline(rule, event_data, payload, doc_path, db):
    tl = rule["timeline"]
    action = tl.get("action", "create")
    prefix = tl.get("id_prefix", "entry")
    entity_id = payload.get("entity_id", payload.get("uid", ""))
    timeline_doc_id = f"{prefix}_{entity_id}"
    doc_ref = db.collection("timeline_entries").document(timeline_doc_id)

    if action == "create":
        doc_ref.set({
            "projectId": event_data.get("projectId"),
            "type": tl.get("type") or payload.get("type"),
            "origin": payload.get("origin", "system"),
            "visibility": tl.get("visibility", "public"),
            "authorUid": payload.get("author_uid", payload.get("uid", "")),
            "authorName": payload.get("author_name", payload.get("name", "")),
            "authorRoles": payload.get("author_roles", []),
            "targetUid": payload.get("target_uid"),
            "targetName": payload.get("target_name"),
            "title": payload.get("title"),
            "description": payload.get("description"),
            "attachments": payload.get("attachments"),
            "linkPreview": payload.get("link_preview"),
            "eventDate": payload.get("event_date"),
            "eventLocation": payload.get("event_location"),
            "sourceEventRef": doc_path,
            "sourceEntityRef": payload.get("source_entity_ref"),
            "sourceEntityType": payload.get("source_entity_type"),
            "validationStatus": tl.get("initial_status"),
            "reviewRequested": False,
            "reviewRequestedAt": None,
            "reviewResolved": False,
            "reviewResolvedAt": None,
            "turmaName": payload.get("turma_name"),
            "modalidadeName": payload.get("modalidade_name"),
            "classDate": payload.get("class_date"),
            "donationAmount": payload.get("donation_amount"),
            "likesCount": 0,
            "createdAt": event_data.get("occurredAt"),
            "updatedAt": None,
        })
        print(f"OK: event={event_data.get('eventId')} channel=timeline action=create id={timeline_doc_id}")

    elif action in ("update", "update_or_create"):
        update_data = {"updatedAt": _now()}
        if "update_fields" in tl:
            update_data.update(tl["update_fields"])
        for field in ("validatedBy", "validatedAt", "title", "description",
                       "attachments", "eventDate", "eventLocation", "linkPreview",
                       "reviewRequested", "reviewRequestedAt",
                       "reviewResolved", "reviewResolvedAt"):
            if field in payload:
                update_data[field] = payload[field]

        if action == "update_or_create":
            existing = doc_ref.get()
            if not existing.exists:
                # Create instead (e.g., checkin.absent from job — no prior checkin.registered)
                _handle_timeline_create_for_absent(
                    tl, event_data, payload, doc_path, doc_ref
                )
                return

        doc_ref.update(update_data)
        print(f"OK: event={event_data.get('eventId')} channel=timeline action=update id={timeline_doc_id}")


def _handle_timeline_create_for_absent(tl, event_data, payload, doc_path, doc_ref):
    """Create a timeline entry for absent when no prior registered entry exists."""
    doc_ref.set({
        "projectId": event_data.get("projectId"),
        "type": tl.get("type", "attendance"),
        "origin": "system",
        "visibility": tl.get("visibility", "personal_and_staff"),
        "authorUid": "system",
        "authorName": "Sistema",
        "authorRoles": [],
        "targetUid": payload.get("target_uid"),
        "targetName": payload.get("target_name"),
        "title": None,
        "description": None,
        "attachments": None,
        "linkPreview": None,
        "eventDate": None,
        "eventLocation": None,
        "sourceEventRef": doc_path,
        "sourceEntityRef": payload.get("source_entity_ref"),
        "sourceEntityType": payload.get("source_entity_type"),
        "validationStatus": "absent",
        "reviewRequested": False,
        "reviewRequestedAt": None,
        "reviewResolved": False,
        "reviewResolvedAt": None,
        "turmaName": payload.get("turma_name"),
        "modalidadeName": payload.get("modalidade_name"),
        "classDate": payload.get("class_date"),
        "donationAmount": payload.get("donation_amount"),
        "likesCount": 0,
        "createdAt": event_data.get("occurredAt"),
        "updatedAt": None,
    })
    print(f"OK: event={event_data.get('eventId')} channel=timeline action=create_for_absent")


def _handle_calendar(rule, event_data, payload, doc_path, db):
    cal = rule["calendar"]
    only_types = cal.get("only_types")
    post_type = payload.get("type", "")

    if only_types and post_type not in only_types:
        return

    action = cal.get("action", "create")
    entity_id = payload.get("entity_id", "")
    cal_doc_id = f"post_{entity_id}"
    doc_ref = db.collection("eventos_calendario").document(cal_doc_id)

    if action == "create":
        doc_ref.set({
            "projectId": event_data.get("projectId"),
            "type": post_type,
            "title": payload.get("title"),
            "description": payload.get("description"),
            "startDate": payload.get("event_date"),
            "endDate": payload.get("event_end_date"),
            "location": payload.get("event_location"),
            "origin": payload.get("origin", "timeline_wizard"),
            "sourcePostRef": f"posts/{entity_id}",
            "sourceEventRef": doc_path,
            "createdBy": payload.get("author_uid"),
            "status": "scheduled",
            "createdAt": event_data.get("occurredAt"),
        })
        print(f"OK: event={event_data.get('eventId')} channel=calendar action=create id={cal_doc_id}")

    elif action == "update":
        update_data = {"updatedAt": _now()}
        for field in ("title", "description", "startDate", "endDate", "location"):
            mapped = {"startDate": "event_date", "endDate": "event_end_date", "location": "event_location"}
            payload_key = mapped.get(field, field)
            if payload_key in payload and payload[payload_key] is not None:
                update_data[field] = payload[payload_key]
        doc_ref.update(update_data)
        print(f"OK: event={event_data.get('eventId')} channel=calendar action=update id={cal_doc_id}")

    elif action == "delete":
        doc_ref.update({"status": "cancelled", "updatedAt": _now()})
        print(f"OK: event={event_data.get('eventId')} channel=calendar action=delete id={cal_doc_id}")


def _handle_push(rule, event_data, payload, doc_path, db):
    push = rule["push"]
    target = push.get("target", "")
    project_id = event_data.get("projectId", "")

    recipients = []
    if target == "owner_and_guardian":
        target_uid = payload.get("target_uid") or payload.get("uid", "")
        if target_uid:
            recipients.append(target_uid)
            recipients.extend(_resolve_guardians(db, target_uid, project_id))
    elif target in ("staff", "staff_actionable"):
        recipients = _resolve_staff_uids(db, project_id)

    title = _safe_format(push.get("title_template", ""), payload)
    body = _safe_format(push.get("body_template", ""), payload)

    for uid in recipients:
        if not uid:
            continue
        push_doc = {
            "to_uid": uid,
            "title": title,
            "body": body,
            "data": {
                "event_id": event_data.get("eventId", ""),
                "entity_type": payload.get("source_entity_type", ""),
                "entity_id": payload.get("entity_id", payload.get("uid", "")),
            },
            "status": "pending",
            "source_event_ref": doc_path,
        }
        if target == "staff_actionable":
            push_doc["actions"] = push.get("actions", [])
        db.collection("push_queue").add(push_doc)

    print(f"OK: event={event_data.get('eventId')} channel=push target={target} recipients={len(recipients)}")


# ─── Entry Point ──────────────────────────────────────────────────────────────

@firestore_fn.on_document_created(document="events/{eventId}")
def event_orchestrator(
    event: firestore_fn.Event[firestore_fn.DocumentSnapshot],
) -> None:
    """Process Firestore document creation in `events` collection."""
    db = firestore.client()

    snapshot = event.data
    if not snapshot.exists:
        print("SKIP: document does not exist")
        return

    doc_path = f"events/{event.params['eventId']}"
    doc_ref = db.document(doc_path)
    event_data = snapshot.to_dict()
    event_id = event_data.get("eventId", "")
    rule = EVENT_RULES.get(event_id)

    if not rule or not rule.get("channels"):
        doc_ref.update({"status": "processed", "processedAt": _now()})
        print(f"OK: event={event_id} no channels — marked processed")
        return

    payload = event_data.get("payload", {})

    try:
        for channel in rule["channels"]:
            if channel == "email":
                _handle_email(rule, event_id, payload, doc_path, db)
            elif channel == "timeline":
                _handle_timeline(rule, event_data, payload, doc_path, db)
            elif channel == "calendar":
                _handle_calendar(rule, event_data, payload, doc_path, db)
            elif channel == "push":
                _handle_push(rule, event_data, payload, doc_path, db)

        doc_ref.update({"status": "processed", "processedAt": _now()})
    except Exception as e:
        print(f"ERROR: event={event_id} err={e}")
        doc_ref.update({"status": "failed", "processedAt": _now(), "error": str(e)})
        raise
