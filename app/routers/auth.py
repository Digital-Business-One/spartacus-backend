from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.logging.decorator import log
from app.models.auth import (
    EmailVerifiedResponse,
    ResendVerificationRequest,
    SignupRequest,
    SignupResponse,
)
from app.security.context import auth_ctx
from app.security.decorator import public
from app.security.firebase import verify_id_token
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@log
@router.post("/signup", status_code=201)
@public
def signup(
    data: SignupRequest,
    x_project_id: str = Header(...),
    authorization: Optional[str] = Header(None),
) -> SignupResponse:
    google_uid = None
    if data.auth_method == "google":
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Token Firebase obrigatório para Google Sign-In",
            )
        token = authorization.removeprefix("Bearer ")
        try:
            claims = verify_id_token(token)
            google_uid = claims["uid"]
        except Exception:
            raise HTTPException(status_code=401, detail="Token inválido")

    return AuthService().signup(x_project_id, data, google_uid)


@log
@router.post("/email-verified")
def email_verified(
    x_project_id: str = Header(...),
) -> EmailVerifiedResponse:
    ctx = auth_ctx.get()
    AuthService().confirm_email_verified(ctx.user_id, ctx.user_email)
    return EmailVerifiedResponse(status="pending_approval")


@log
@router.post("/resend-verification")
@public
def resend_verification(data: ResendVerificationRequest) -> EmailVerifiedResponse:
    AuthService().resend_verification(data)
    return EmailVerifiedResponse(status="ok")
