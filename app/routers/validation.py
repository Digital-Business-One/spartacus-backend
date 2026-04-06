"""Validation router — confirm/absent/review (RFC-11)."""

from fastapi import APIRouter, HTTPException

from app.events import publisher
from app.logging.decorator import log
from app.models.timeline import ValidateRequest
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.validation_service import ValidationService

router = APIRouter(tags=["validation"])


# ─── Presença ─────────────────────────────────────────────────────────────────

@log
@router.patch("/presencas/{doc_id}/validate")
@require_roles("owner", "assistant", "teacher", "instructor")
def validate_presenca(doc_id: str, body: ValidateRequest):
    ctx = auth_ctx.get()
    try:
        event = ValidationService().validate(
            collection="presencas",
            doc_id=doc_id,
            project_id=ctx.project_id,
            status=body.status,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=422, detail=str(e))

    publisher.publish(event, project_id=ctx.project_id, source="validation_service")
    return {"status": "ok"}


@log
@router.post("/presencas/{doc_id}/request-review")
def request_review_presenca(doc_id: str):
    ctx = auth_ctx.get()
    try:
        event = ValidationService().request_review(
            collection="presencas",
            doc_id=doc_id,
            project_id=ctx.project_id,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    publisher.publish(event, project_id=ctx.project_id, source="validation_service")
    return {"status": "ok"}


@log
@router.patch("/presencas/{doc_id}/resolve-review")
@require_roles("owner", "assistant", "teacher", "instructor")
def resolve_review_presenca(doc_id: str):
    ctx = auth_ctx.get()
    try:
        ValidationService().resolve_review(
            collection="presencas",
            doc_id=doc_id,
            project_id=ctx.project_id,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"status": "ok"}


# ─── Doações ──────────────────────────────────────────────────────────────────

@log
@router.patch("/doacoes/{doc_id}/validate")
@require_roles("owner", "assistant", "teacher", "instructor")
def validate_doacao(doc_id: str, body: ValidateRequest):
    ctx = auth_ctx.get()
    try:
        event = ValidationService().validate(
            collection="doacoes",
            doc_id=doc_id,
            project_id=ctx.project_id,
            status=body.status,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=422, detail=str(e))

    publisher.publish(event, project_id=ctx.project_id, source="validation_service")
    return {"status": "ok"}


@log
@router.post("/doacoes/{doc_id}/request-review")
def request_review_doacao(doc_id: str):
    ctx = auth_ctx.get()
    try:
        event = ValidationService().request_review(
            collection="doacoes",
            doc_id=doc_id,
            project_id=ctx.project_id,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    publisher.publish(event, project_id=ctx.project_id, source="validation_service")
    return {"status": "ok"}


@log
@router.patch("/doacoes/{doc_id}/resolve-review")
@require_roles("owner", "assistant", "teacher", "instructor")
def resolve_review_doacao(doc_id: str):
    ctx = auth_ctx.get()
    try:
        ValidationService().resolve_review(
            collection="doacoes",
            doc_id=doc_id,
            project_id=ctx.project_id,
            actor_uid=ctx.user_id,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"status": "ok"}
