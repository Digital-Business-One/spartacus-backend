"""Posts router — CRUD for timeline posts (RFC-11)."""

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, UploadFile
from firebase_admin import storage

from app.events import publisher
from app.logging.decorator import log
from app.models.post import PostCreate, PostOut, PostUpdate
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.post_service import PostService

_UPLOAD_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
_ALLOWED_MEDIA = {
    "image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp",
    "video/mp4", "video/quicktime", "video/webm",
    "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm", "audio/ogg",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

router = APIRouter(prefix="/posts", tags=["posts"])


def _get_author_name(uid: str, project_id: str) -> str:
    """Resolve author name from users collection."""
    from firebase_admin import firestore

    db = firestore.client()
    user_doc = db.collection("users").document(uid).get()
    if user_doc.exists:
        return user_doc.to_dict().get("name", "")
    return ""


@log
@router.post("", status_code=201)
@require_roles("social")
def create_post(data: PostCreate) -> PostOut:
    ctx = auth_ctx.get()
    author_name = _get_author_name(ctx.user_id, ctx.project_id)

    result, event = PostService().create(
        project_id=ctx.project_id,
        data=data,
        author_uid=ctx.user_id,
        author_name=author_name,
        author_roles=ctx.roles,
    )
    publisher.publish(event, project_id=ctx.project_id, source="post_service")
    return result


@log
@router.patch("/{post_id}")
def update_post(post_id: str, data: PostUpdate) -> PostOut:
    ctx = auth_ctx.get()
    result, event = PostService().update(
        project_id=ctx.project_id,
        post_id=post_id,
        data=data,
        actor_uid=ctx.user_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Post não encontrado")
    if event:
        publisher.publish(event, project_id=ctx.project_id, source="post_service")
    return result


@log
@router.post("/upload")
@require_roles("social")
async def upload_attachment(file: UploadFile) -> dict:
    """Upload a media file for a post attachment. Returns URL + metadata."""
    ctx = auth_ctx.get()

    content_type = (file.content_type or "").lower()
    if content_type == "image/jpg":
        content_type = "image/jpeg"
    if content_type not in _ALLOWED_MEDIA:
        raise HTTPException(
            status_code=400,
            detail=f"Tipo de arquivo não permitido: {content_type}",
        )

    contents = await file.read()
    if len(contents) > _UPLOAD_MAX_SIZE:
        raise HTTPException(status_code=400, detail="Arquivo muito grande (max 10 MB)")

    ext = (file.filename or "file").rsplit(".", 1)[-1] if file.filename else "bin"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    blob_name = f"posts/{ctx.user_id}/{ts}_{uuid.uuid4().hex[:8]}.{ext}"

    bucket_name = os.getenv(
        "FIREBASE_STORAGE_BUCKET",
        "spartacus-artes-marciais.firebasestorage.app",
    )
    bucket = storage.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(contents, content_type=content_type)
    blob.make_public()

    # Classify for the AttachmentIn.type field
    if content_type.startswith("image"):
        att_type = "image"
    elif content_type.startswith("audio"):
        att_type = "voice"
    else:
        att_type = "file"

    return {
        "type": att_type,
        "url": blob.public_url,
        "name": file.filename or f"attachment.{ext}",
        "size": len(contents),
    }


@log
@router.delete("/{post_id}", status_code=204)
def delete_post(post_id: str):
    ctx = auth_ctx.get()
    event = PostService().delete(
        project_id=ctx.project_id,
        post_id=post_id,
        actor_uid=ctx.user_id,
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Post não encontrado")
    publisher.publish(event, project_id=ctx.project_id, source="post_service")
