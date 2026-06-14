"""Cloud Function: send emails via SendGrid.

Triggered when a document is created in the Firestore `notifications`
collection. Reads the document, sends the email via SendGrid, and
updates the document status.
"""

import os

import firebase_admin
from firebase_admin import credentials as _creds
from firebase_admin import firestore
from firebase_functions import firestore_fn, options

# Runtime options for `firebase deploy` (region/memory/timeout/SA + secrets).
# secrets=["NAME"] binds an existing Secret Manager secret to the function
# and surfaces it as an env var of the same name at runtime. Using a plain
# string (not SecretParam) — SecretParam is for build-time template params,
# which the firebase CLI serializes as `{{ params.NAME }}` and breaks the
# secret-version lookup.
options.set_global_options(
    region="us-east1",
    memory=options.MemoryOption.MB_256,
    timeout_sec=30,
    service_account="fn-send-email@spartacus-artes-marciais.iam.gserviceaccount.com",
    secrets=["SENDGRID_API_KEY"],
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
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import From, Mail, To


@firestore_fn.on_document_created(document="notifications/{docId}")
def send_email(
    event: firestore_fn.Event[firestore_fn.DocumentSnapshot],
) -> None:
    """Process Firestore document creation in `notifications` collection."""
    db = firestore.client()

    snapshot = event.data
    if not snapshot.exists:
        print("SKIP: document does not exist")
        return

    doc_path = f"notifications/{event.params['docId']}"
    doc_ref = db.document(doc_path)
    fields = snapshot.to_dict()
    template_id = fields.get("template_id", "")
    subject = fields.get("subject", "")
    to_email = fields.get("to", "")
    from_email = fields.get("from_email", "noreply@spartacus.app.br")
    from_name = fields.get("from_name", "Spartacus Artes Marciais")
    data = fields.get("data", {})
    event_id = fields.get("event_id", "")

    if not to_email or not template_id:
        print(f"SKIP: missing to={to_email} or template_id={template_id}")
        doc_ref.update({"status": "skipped"})
        return

    # Inject subject into template data for {{subject}} in SendGrid
    if isinstance(data, dict):
        data["subject"] = subject

    # secrets=["SENDGRID_API_KEY"] in global options binds the secret as an
    # env var at runtime in prod; docker-compose injects the same env var
    # in the emulator.
    api_key = os.environ.get("SENDGRID_API_KEY", "")

    # Dev fallback: no SendGrid key → log the email (including any CTA link)
    # instead of actually sending. When a real key is present, always send —
    # this lets developers test the full pipeline end-to-end against SendGrid.
    if not api_key:
        print("─" * 60)
        print(f"[LOCAL EMAIL] event={event_id}")
        print(f"  to:      {to_email}")
        print(f"  subject: {subject}")
        print(f"  template: {template_id}")
        if isinstance(data, dict):
            for k, v in data.items():
                print(f"  {k}: {v}")
        print("─" * 60)
        doc_ref.update({"status": "sent_local"})
        return

    message = Mail()
    message.from_email = From(from_email, from_name)
    message.to = To(to_email)
    message.template_id = template_id
    message.dynamic_template_data = data

    try:
        sg = SendGridAPIClient(api_key)
        response = sg.send(message)
        print(
            f"OK: event={event_id} "
            f"to={to_email} status={response.status_code}"
        )
        doc_ref.update({"status": "sent"})
    except Exception as e:
        print(f"ERROR: event={event_id} to={to_email} err={e}")
        doc_ref.update({"status": "error", "error": str(e)})
        raise
