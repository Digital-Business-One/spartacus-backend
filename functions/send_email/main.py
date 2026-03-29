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


def _extract_value(field):
    """Extract a Python value from a Firestore protobuf field."""
    if "stringValue" in field:
        return field["stringValue"]
    if "mapValue" in field:
        return {
            k: _extract_value(v)
            for k, v in field["mapValue"].get("fields", {}).items()
        }
    if "arrayValue" in field:
        return [
            _extract_value(v)
            for v in field["arrayValue"].get("values", [])
        ]
    if "booleanValue" in field:
        return field["booleanValue"]
    if "integerValue" in field:
        return int(field["integerValue"])
    if "doubleValue" in field:
        return field["doubleValue"]
    if "nullValue" in field:
        return None
    return str(field)


@functions_framework.cloud_event
def handle_firestore(cloud_event):
    """Process Firestore document creation and send email via SendGrid."""
    # Extract document path for status update
    doc_path = cloud_event["subject"]
    if doc_path.startswith("documents/"):
        doc_path = doc_path[len("documents/"):]

    # Parse fields from Firestore protobuf format
    raw_fields = cloud_event.data.get("value", {}).get("fields", {})
    fields = {k: _extract_value(v) for k, v in raw_fields.items()}

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

    db = firestore.Client()

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
