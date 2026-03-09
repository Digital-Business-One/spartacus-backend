import os

from fastapi import APIRouter, HTTPException

from app.logging.decorator import log
from app.services.email_service import EmailService

router = APIRouter(prefix="/internal", tags=["internal"])


@log
@router.post("/email/test")
def test_email(to: str):
    if os.getenv("APP_ENV") != "development":
        raise HTTPException(
            status_code=403,
            detail="Disponível apenas em ambiente de desenvolvimento",
        )
    EmailService().send(
        to=to,
        subject="[Spartacus] Teste de e-mail transacional",
        html=(
            "<h1>Teste</h1>"
            "<p>E-mail de teste do backend Spartacus via MailerSend.</p>"
        ),
    )
    return {"status": "sent", "to": to}
