"""Cloud Run Function: send emails via SendGrid.

Triggered by Eventarc when a document is created in the Firestore
`notifications` collection. Reads the document, sends the email
via SendGrid, and updates the document status.

Deployment:
    gcloud functions deploy send-email \
      --gen2 \
      --runtime python312 \
      --region us-east1 \
      --source ./functions/send_email \
      --entry-point handle_firestore \
      --trigger-event-filters="type=google.cloud.firestore.document.v1.created" \
      --trigger-event-filters="database=(default)" \
      --trigger-event-filters-path-pattern="document=notifications/{docId}" \
      --trigger-location=us-east1 \
      --service-account fn-send-email@PROJECT_ID.iam.gserviceaccount.com \
      --set-secrets SENDGRID_API_KEY=SENDGRID_API_KEY:latest \
      --memory 256Mi \
      --timeout 30s
"""

import os

import functions_framework
from google.cloud import firestore
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, From, To

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")


@functions_framework.cloud_event
def handle_firestore(cloud_event):
    """Process Firestore document creation and send email via SendGrid."""
    # Extract document path from the CloudEvent subject
    doc_path = cloud_event["subject"]
    if doc_path.startswith("documents/"):
        doc_path = doc_path[len("documents/"):]

    # Read the document directly from Firestore instead of parsing
    # protobuf from cloud_event.data — simpler and works regardless
    # of how Eventarc serializes the payload (JSON or protobuf bytes).
    db = firestore.Client()
    doc = db.document(doc_path).get()
    if not doc.exists:
        print(f"SKIP: document {doc_path} not found")
        return
    fields = doc.to_dict()

    template_id = fields.get("template_id", "")
    subject = fields.get("subject", "")
    to_email = fields.get("to", "")
    from_email = fields.get("from_email", "noreply@spartacus.app.br")
    from_name = fields.get("from_name", "Spartacus Artes Marciais")
    data = fields.get("data", {})
    event_id = fields.get("event_id", "")

    if not to_email or not template_id:
        print(f"SKIP: missing to={to_email} or template_id={template_id}")
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
        # Mark as sent
        db.document(doc_path).update({"status": "sent"})
    except Exception as e:
        print(f"ERROR: event={event_id} to={to_email} err={e}")
        db.document(doc_path).update({"status": "error", "error": str(e)})
        raise
