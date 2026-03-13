from fastapi import APIRouter

from app.logging.decorator import log
from app.models.classes import ClassesResponse
from app.security.decorator import public
from app.services.class_service import ClassService

router = APIRouter(prefix="/projects", tags=["classes"])


@log
@router.get("/{project_id}/classes")
@public
def list_classes(project_id: str) -> ClassesResponse:
    classes = ClassService().list_by_project(project_id)
    return ClassesResponse(classes=classes)
