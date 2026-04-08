"""Cloud Function: send emails via SendGrid.

Triggered when a document is created in the Firestore `notifications`
collection. Reads the document, sends the email via SendGrid, and
updates the document status.

Uses functions_framework.cloud_event (Gen2 standard) instead of the
firebase_functions decorator, which the runtime no longer recognizes
properly with the current version of functions-framework.
"""

import os

import functions_framework
from cloudevents.http import CloudEvent
from firebase_admin import firestore, initialize_app
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import From, Mail, To

initialize_app()

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")


@functions_framework.cloud_event
def send_email(cloud_event: CloudEvent) -> None:
    """Process Firestore document creation in `notifications` collection."""
    db = firestore.client()

    # Extract document path from CloudEvent subject
    # Format: "documents/notifications/{docId}"
    subject_attr = cloud_event.get("subject", "")
    doc_path = subject_attr.removeprefix("documents/")
    if not doc_path or not doc_path.startswith("notifications/"):
        print(f"SKIP: unexpected subject={subject_attr}")
        return

    doc_ref = db.document(doc_path)
    snapshot = doc_ref.get()
    if not snapshot.exists:
        print(f"SKIP: document not found at {doc_path}")
        return

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

    message = Mail()
    message.from_email = From(from_email, from_name)
    message.to = To(to_email)
    message.template_id = template_id
    message.dynamic_template_data = data

    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
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
