import os

from fastapi import APIRouter, HTTPException
from firebase_admin import firestore

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
    db = firestore.client()
    db.collection("mail").add(
        {
            "to": [{"email": to}],
            "from": {
                "email": "noreply@spartacus.app.br",
                "name": "Spartacus Artes Marciais",
            },
            "subject": "[Spartacus] Teste de e-mail transacional",
            "html": (
                "<h1>Teste</h1>"
                "<p>E-mail de teste do backend Spartacus.</p>"
            ),
            "tags": ["internal.test"],
        }
    )
    return {"status": "queued", "to": to}
