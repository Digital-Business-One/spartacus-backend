from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.events import publisher
from app.logging.decorator import log
from app.models.auth import (
    CheckEmailResponse,
    EmailVerifiedResponse,
    MeResponse,
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
@router.get("/check-email")
@public
def check_email(email: str) -> CheckEmailResponse:
    available = AuthService().check_email(email)
    return CheckEmailResponse(available=available)


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

    event = AuthService().signup(x_project_id, data, google_uid)
    publisher.publish(event, project_id=x_project_id, source="auth_service")
    return SignupResponse(
        uid=event.payload.uid,
        status=event.payload.status,
        email=data.email if data.auth_method == "email" else None,
    )


@log
@router.get("/me")
def me() -> MeResponse:
    ctx = auth_ctx.get()
    user_data = AuthService().get_user_status(ctx.user_id)
    return MeResponse(
        uid=ctx.user_id,
        email=ctx.user_email,
        approval_status=user_data["approvalStatus"],
        birth_date=user_data.get("birthDate"),
    )


@log
@router.post("/email-verified")
def email_verified(
    x_project_id: str = Header(...),
) -> EmailVerifiedResponse:
    ctx = auth_ctx.get()
    event = AuthService().confirm_email_verified(ctx.user_id, ctx.user_email)
    if event:
        publisher.publish(event, project_id=x_project_id, source="auth_service")
    return EmailVerifiedResponse(status="pending_approval")


@log
@router.post("/resend-verification")
@public
def resend_verification(
    data: ResendVerificationRequest,
    x_project_id: str = Header(...),
) -> EmailVerifiedResponse:
    event = AuthService().resend_verification(data)
    if event:
        publisher.publish(
            event, project_id=x_project_id, source="auth_service"
        )
    return EmailVerifiedResponse(status="ok")
