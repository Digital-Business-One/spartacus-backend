"""Posts router — CRUD for timeline posts (RFC-11)."""

from fastapi import APIRouter, HTTPException

from app.events import publisher
from app.logging.decorator import log
from app.models.post import PostCreate, PostOut, PostUpdate
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.post_service import PostService

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
