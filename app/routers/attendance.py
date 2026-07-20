from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, UploadFile
from firebase_admin import firestore

from app.events import publisher
from app.logging.decorator import log
from app.models.attendance import (
    AttendanceActionOut,
    AttendanceActionRequest,
    AttendanceAnalyticsOut,
    AttendanceDashboardOut,
    AttendanceHistoryOut,
    JustifyAttendanceOut,
    JustifyAttendanceRequest,
    RejectJustificationRequest,
)
from app.security.context import auth_ctx
from app.security.decorator import require_roles
from app.services.attendance_service import AttendanceService
from app.services.justification_service import JustificationService
from app.services.justification_type_service import JustificationTypeService
from app.services.storage_service import upload_media_attachment

router = APIRouter(prefix="/attendance", tags=["attendance"])

# Sub-projeto B (Justificativa de Faltas) — days after the aula's date
# (attendance doc's `timestamp`) during which a justification may be
# submitted. See design spec "Decisões" -> "Prazo (a confirmar): 7 dias".
_JUSTIFY_PRAZO_DAYS = 7

# Task B4 — justification attachment upload constraints.
_JUSTIFICATION_UPLOAD_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
_JUSTIFICATION_ALLOWED_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "application/pdf",
}


@log
@router.get("/history")
def get_attendance_history(
    month: Optional[str] = Query(None, description="Filter YYYY-MM"),
    modality: Optional[str] = Query(None, description="Modality slug"),
    belt: Optional[str] = Query(None, description="Belt slug"),
    degree: Optional[int] = Query(None, description="Graduation degree"),
    x_acting_as: Optional[str] = Header(None),
) -> AttendanceHistoryOut:
    ctx = auth_ctx.get()
    filters = {
        k: v
        for k, v in {
            "month": month,
            "modality": modality,
            "belt": belt,
            "degree": degree,
        }.items()
        if v is not None
    }
    return AttendanceService().get_history(
        project_id=ctx.project_id,
        uid=ctx.user_id,
        acting_as=x_acting_as,
        filters=filters or None,
    )


# ── RFC-14: Dashboard de Frequência ────────────────────────────────────────

@log
@router.get("/dashboard/{class_id}")
@require_roles("owner", "assistant", "teacher", "instructor")
def get_attendance_dashboard(class_id: str) -> AttendanceDashboardOut:
    ctx = auth_ctx.get()
    return AttendanceService().list_today_for_class(
        project_id=ctx.project_id, class_id=class_id,
    )


@log
@router.get("/analytics/{class_id}")
@require_roles("owner", "assistant", "teacher", "instructor")
def get_attendance_analytics(
    class_id: str,
    month: Optional[str] = Query(None, description="Filter YYYY-MM"),
) -> AttendanceAnalyticsOut:
    ctx = auth_ctx.get()
    return AttendanceService().get_analytics(
        project_id=ctx.project_id, class_id=class_id, month=month,
    )


@log
@router.post("/confirm")
@require_roles("owner", "assistant", "teacher", "instructor")
def confirm_attendance(data: AttendanceActionRequest) -> AttendanceActionOut:
    ctx = auth_ctx.get()
    if not data.class_id or not data.user_id:
        raise HTTPException(
            status_code=422, detail="class_id e user_id são obrigatórios",
        )
    result, event = AttendanceService().confirm_attendance(
        project_id=ctx.project_id,
        class_id=data.class_id,
        user_id=data.user_id,
        actor_uid=ctx.user_id,
        source=data.source or "manual",
        aula_id=data.aula_id,
        force=data.force,
    )
    if event:
        publisher.publish(event, project_id=ctx.project_id, source="attendance_service")
    return result


# ── Sub-projeto B: Justificativa de Faltas (Task B3) ───────────────────────

def _assert_guardian_of(db, guardian_uid: str, target_uid: str) -> None:
    """Mirrors `AttendanceService._assert_guardian_of` (own copy — B3 must
    not modify B1/B2 service files, see task brief): 404 when the acting-as
    target user doc doesn't exist, 403 only on a genuine guardian mismatch."""
    doc = db.collection("users").document(target_uid).get()
    if not doc.exists:
        raise HTTPException(
            status_code=404, detail="Dependente não encontrado",
        )
    if doc.to_dict().get("guardianUid") != guardian_uid:
        raise HTTPException(
            status_code=403, detail="Você não é responsável deste dependente",
        )


def _parse_record_dt(value) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@log
@router.post("/{doc_id}/justify")
def justify_attendance(
    doc_id: str,
    data: JustifyAttendanceRequest,
    x_acting_as: Optional[str] = Header(None),
) -> JustifyAttendanceOut:
    """Aluno/responsável justifica uma falta (`absent` -> pendente).

    Validation order (see design spec "Endpoints" and task brief): record
    exists + belongs to the acting user (403/404) -> record is `absent`
    (409) -> within the 7-day prazo (422) -> type exists and is active
    (422) -> attachment present when the type requires one (422). The
    actual transition + write is delegated to `JustificationService.justify`
    (B1), which re-validates the origin status at write-time (same
    discipline as the rest of the validation flow) — this router validates
    early only for a clean, specific error.
    """
    ctx = auth_ctx.get()
    db = firestore.client()

    target_uid = ctx.user_id
    if x_acting_as:
        _assert_guardian_of(db, ctx.user_id, x_acting_as)
        target_uid = x_acting_as

    doc = db.collection("attendance").document(doc_id).get()
    if not doc.exists:
        raise HTTPException(
            status_code=404, detail="Registro de frequência não encontrado",
        )
    record = doc.to_dict()
    if (
        record.get("projectId") != ctx.project_id
        or record.get("userId") != target_uid
    ):
        raise HTTPException(status_code=403, detail="Acesso negado ao registro")

    if record.get("status") != "absent":
        raise HTTPException(
            status_code=409,
            detail=(
                "Registro não está 'absent' — status atual: "
                f"{record.get('status')}"
            ),
        )

    record_dt = _parse_record_dt(record.get("timestamp"))
    if record_dt is not None:
        deadline = record_dt + timedelta(days=_JUSTIFY_PRAZO_DAYS)
        if datetime.now(timezone.utc) > deadline:
            raise HTTPException(
                status_code=422, detail="Prazo de justificativa encerrado",
            )

    types = JustificationTypeService().list_types(
        ctx.project_id, include_inactive=True,
    )
    jtype = next((t for t in types if t.slug == data.type_id), None)
    if jtype is None or not jtype.active:
        raise HTTPException(
            status_code=422,
            detail="Tipo de justificativa inválido ou inativo",
        )

    if jtype.requires_attachment and data.attachment is None:
        raise HTTPException(
            status_code=422,
            detail="Anexo obrigatório para este tipo de justificativa",
        )

    justification = JustificationService().justify(
        doc_id=doc_id,
        project_id=ctx.project_id,
        actor_uid=ctx.user_id,
        type_id=data.type_id,
        type_name=jtype.name,
        text=data.text,
        attachment=data.attachment,
    )
    return JustifyAttendanceOut(
        status="absent_justification_pending",
        justification=justification,
    )


@log
@router.post("/justification-upload")
async def upload_justification_attachment(file: UploadFile) -> dict:
    """Upload de anexo (imagem/PDF) para uma justificativa de falta.

    Gate: qualquer membro autenticado do projeto — **sem** o gate `social`
    exigido por `/posts/upload`, já que aluno/responsável precisa poder
    anexar o atestado. Reusa `storage_service.upload_media_attachment`
    (mesma mecânica de `/posts/upload`), com prefixo `justifications/{uid}/`
    e status 415/413 para tipo inválido / arquivo grande demais.
    """
    ctx = auth_ctx.get()
    return await upload_media_attachment(
        uid=ctx.user_id,
        file=file,
        prefix="justifications",
        allowed_types=_JUSTIFICATION_ALLOWED_TYPES,
        max_size=_JUSTIFICATION_UPLOAD_MAX_SIZE,
        invalid_type_status=415,
        oversize_status=413,
    )


@log
@router.patch("/{doc_id}/justification/approve")
@require_roles("owner", "assistant", "teacher", "instructor")
def approve_justification(doc_id: str) -> JustifyAttendanceOut:
    """Staff aprova uma justificativa pendente (`absent_justification_pending`
    -> `absent_justified`). Delegates entirely to `JustificationService.approve`
    (B1), which re-validates the origin status at write-time (409 if the
    record isn't pending) and stamps `reviewedBy`/`reviewedByName`/`reviewedAt`.
    """
    ctx = auth_ctx.get()
    justification = JustificationService().approve(
        doc_id=doc_id,
        project_id=ctx.project_id,
        actor_uid=ctx.user_id,
    )
    return JustifyAttendanceOut(
        status="absent_justified", justification=justification,
    )


@log
@router.patch("/{doc_id}/justification/reject")
@require_roles("owner", "assistant", "teacher", "instructor")
def reject_justification(
    doc_id: str, data: RejectJustificationRequest,
) -> JustifyAttendanceOut:
    """Staff recusa uma justificativa pendente (`absent_justification_pending`
    -> `absent`, motivo obrigatório). Delegates to `JustificationService.reject`
    (B1), which enforces the non-empty `reason` (422) and the origin-status
    check (409 if the record isn't pending).
    """
    ctx = auth_ctx.get()
    justification = JustificationService().reject(
        doc_id=doc_id,
        project_id=ctx.project_id,
        actor_uid=ctx.user_id,
        reason=data.reason,
    )
    return JustifyAttendanceOut(status="absent", justification=justification)


@log
@router.post("/reject")
@require_roles("owner", "assistant", "teacher", "instructor")
def reject_attendance(data: AttendanceActionRequest) -> AttendanceActionOut:
    ctx = auth_ctx.get()
    if not data.class_id or not data.user_id:
        raise HTTPException(
            status_code=422, detail="class_id e user_id são obrigatórios",
        )
    result, event = AttendanceService().reject_attendance(
        project_id=ctx.project_id,
        class_id=data.class_id,
        user_id=data.user_id,
        actor_uid=ctx.user_id,
        reason=data.reason,
        aula_id=data.aula_id,
        force=data.force,
    )
    if event:
        publisher.publish(event, project_id=ctx.project_id, source="attendance_service")
    return result
