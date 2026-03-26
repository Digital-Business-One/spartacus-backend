from fastapi import APIRouter, HTTPException

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
@router.get("/{project_id}/classes")
@public
def list_classes(project_id: str) -> ClassesResponse:
    classes = ClassService().list_by_project(project_id)
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
@router.delete("/{project_id}/classes/{class_id}", status_code=204)
@require_roles("owner", "assistant")
def deactivate_class(project_id: str, class_id: str):
    _assert_project(project_id)
    if not ClassService().deactivate(project_id, class_id):
        raise HTTPException(status_code=404, detail="Turma não encontrada")
