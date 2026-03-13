import os

from fastapi import APIRouter, HTTPException
from mailersend import EmailBuilder, MailerSendClient

from app.logging.decorator import log

router = APIRouter(prefix="/internal", tags=["internal"])


@log
@router.post("/email/test")
def test_email(to: str):
    if os.getenv("APP_ENV") != "development":
        raise HTTPException(
            status_code=403,
            detail="Disponível apenas em ambiente de desenvolvimento",
        )
    email_request = (
        EmailBuilder()
        .from_email("noreply@horadofluxo.com.br", "Spartacus")
        .to(to)
        .subject("[Spartacus] Teste de e-mail transacional")
        .html(
            "<h1>Teste</h1>"
            "<p>E-mail de teste do backend Spartacus via MailerSend.</p>"
        )
        .build()
    )
    MailerSendClient().emails.send(email_request)
    return {"status": "sent", "to": to}
