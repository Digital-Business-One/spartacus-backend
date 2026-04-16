from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from firebase_admin import firestore

from app.logging.decorator import log
from app.models.classes import ClassCreate, ClassesResponse, ClassOut, ClassUpdate
from app.security.context import auth_ctx
from app.security.decorator import public, require_roles
from app.services.class_service import ClassService

router = APIRouter(prefix="/projects", tags=["classes"])


def _assert_project(project_id: str) -> None:
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Acesso negado ao projeto")


@log
@router.get("/{project_id}/my-classes")
def get_my_classes(
    project_id: str,
    x_acting_as: Optional[str] = Header(None),
) -> dict:
    ctx = auth_ctx.get()
    target_uid = x_acting_as or ctx.user_id
    db = firestore.client()
    user_doc = db.collection("users").document(target_uid).get()
    class_ids = user_doc.to_dict().get("classIds", []) if user_doc.exists else []
    return {
        "enrollments": [{"class_id": cid} for cid in class_ids],
    }


@log
@router.get("/{project_id}/classes")
@public
def list_classes(
    project_id: str,
    include_inactive: bool = Query(False, alias="includeInactive"),
) -> ClassesResponse:
    classes = ClassService().list_by_project(
        project_id, include_inactive=include_inactive
    )
    return ClassesResponse(classes=classes)


@log
@router.post("/{project_id}/classes", status_code=201)
@require_roles("owner", "assistant")
def create_class(project_id: str, data: ClassCreate) -> ClassOut:
    _assert_project(project_id)
    return ClassService().create(project_id, data)


@log
@router.patch("/{project_id}/classes/{class_id}")
@require_roles("owner", "assistant")
def update_class(project_id: str, class_id: str, data: ClassUpdate) -> ClassOut:
    _assert_project(project_id)
    result = ClassService().update(project_id, class_id, data)
    if result is None:
        raise HTTPException(status_code=404, detail="Turma não encontrada")
    return result


@log
@router.post("/{project_id}/classes/{class_id}/reactivate")
@require_roles("owner", "assistant")
def reactivate_class(project_id: str, class_id: str) -> dict:
    """Mark an inactive class as active again."""
    _assert_project(project_id)
    if not ClassService().reactivate(project_id, class_id):
        raise HTTPException(status_code=404, detail="Turma não encontrada")
    return {"status": "reactivated"}


@log
@router.delete("/{project_id}/classes/{class_id}")
@require_roles("owner", "assistant")
def delete_class(project_id: str, class_id: str) -> dict:
    """
    Delete a class if it has no enrolled students; otherwise mark as inactive.
    Returns {"status": "deleted"} or {"status": "deactivated"}.
    """
    _assert_project(project_id)
    result = ClassService().delete_or_deactivate(project_id, class_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Turma não encontrada")
    return {"status": result}
