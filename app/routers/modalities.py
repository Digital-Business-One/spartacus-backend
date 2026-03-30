from fastapi import APIRouter, HTTPException

from app.logging.decorator import log
from app.models.modality import (
    ModalitiesResponse,
    ModalityCreate,
    ModalityOut,
    ModalityUpdate,
)
from app.security.context import auth_ctx
from app.security.decorator import public, require_roles
from app.services.modality_service import ModalityService

router = APIRouter(
    prefix="/projects/{project_id}/modalities",
    tags=["modalities"],
)


@log
@router.get("")
@public
def list_modalities(project_id: str) -> ModalitiesResponse:
    modalities = ModalityService().list_by_project(project_id)
    return ModalitiesResponse(modalities=modalities)


@log
@router.post("", status_code=201)
@require_roles("owner", "assistant")
def create_modality(
    project_id: str, data: ModalityCreate
) -> ModalityOut:
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Projeto não autorizado")
    return ModalityService().create(project_id, data)


@log
@router.patch("/{modality_id}")
@require_roles("owner", "assistant")
def update_modality(
    project_id: str, modality_id: str, data: ModalityUpdate
) -> ModalityOut:
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Projeto não autorizado")
    result = ModalityService().update(project_id, modality_id, data)
    if result is None:
        raise HTTPException(status_code=404, detail="Modalidade não encontrada")
    return result


@log
@router.delete("/{modality_id}", status_code=204)
@require_roles("owner", "assistant")
def deactivate_modality(project_id: str, modality_id: str):
    ctx = auth_ctx.get()
    if ctx.project_id != project_id:
        raise HTTPException(status_code=403, detail="Projeto não autorizado")
    if not ModalityService().deactivate(project_id, modality_id):
        raise HTTPException(status_code=404, detail="Modalidade não encontrada")
