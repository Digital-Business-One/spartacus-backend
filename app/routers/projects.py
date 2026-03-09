from fastapi import APIRouter, HTTPException

from app.logging.decorator import log
from app.models.project import ProjectCreate, ProjectOut
from app.security.context import auth_ctx
from app.security.decorator import require_root
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@log
@router.post("", status_code=201)
@require_root
def create_project(data: ProjectCreate) -> ProjectOut:
    return ProjectService().create(data)


@log
@router.get("/{project_id}")
def get_project(project_id: str) -> ProjectOut:
    ctx = auth_ctx.get()
    if not ctx.roles:
        raise HTTPException(status_code=403, detail="Acesso negado ao projeto")
    project = ProjectService().get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    return project
