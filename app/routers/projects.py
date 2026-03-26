from fastapi import APIRouter, HTTPException

from app.logging.decorator import log
from app.models.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.security.context import auth_ctx
from app.security.decorator import public, require_roles, require_root
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@log
@router.get("")
@public
def list_projects() -> list[ProjectOut]:
    """List all projects. Public — used by signup wizard to select project."""
    return ProjectService().list_all()


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


@log
@router.patch("/{project_id}")
@require_roles("owner", "assistant")
def update_project(project_id: str, data: ProjectUpdate) -> ProjectOut:
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Acesso negado ao projeto")
    project = ProjectService().update(project_id, data)
    if project is None:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    return project
