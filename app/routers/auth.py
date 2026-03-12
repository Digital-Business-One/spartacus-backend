from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from app.logging.decorator import log
from app.models.auth import SignupRequest, SignupResponse
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
