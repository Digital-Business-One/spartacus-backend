"""Cloud Run Function: send emails via SendGrid.

Triggered by PubSub messages on the `email-notifications` topic.
Reads the message payload and sends email using SendGrid Dynamic Templates.

Deployment:
    gcloud functions deploy send-email \
      --gen2 \
      --runtime python312 \
      --region us-east1 \
      --source ./functions/send_email \
      --entry-point handle_pubsub \
      --trigger-topic email-notifications \
      --set-secrets SENDGRID_API_KEY=SENDGRID_API_KEY:latest \
      --memory 256Mi \
      --timeout 30s
"""

import base64
import json
import os

import functions_framework
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail,
    From,
    To,
    DynamicTemplateData,
)

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY", "")


@functions_framework.cloud_event
def handle_pubsub(cloud_event):
    """Process PubSub message and send email via SendGrid."""
    raw = base64.b64decode(cloud_event.data["message"]["data"])
    payload = json.loads(raw)

    template_id = payload.get("template_id", "")
    subject = payload.get("subject", "")
    to_email = payload.get("to", "")
    from_email = payload.get("from_email", "noreply@spartacus.app.br")
    from_name = payload.get("from_name", "Spartacus Artes Marciais")
    data = payload.get("data", {})

    if not to_email or not template_id:
        print(f"SKIP: missing to={to_email} or template_id={template_id}")
        return

    # Inject subject into template data (for {{subject}} in SendGrid)
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
            f"OK: event={payload.get('event_id')} "
            f"to={to_email} status={response.status_code}"
        )
    except Exception as e:
        print(f"ERROR: event={payload.get('event_id')} to={to_email} err={e}")
        raise
