"""JustificationService — status transitions for absence justifications.

Sub-projeto B (Justificativa de Faltas). Transition matrix:

    absent ──(justify)──► absent_justification_pending ──(approve)──► absent_justified
      ▲                                │
      └────────(reject, com motivo)────┘

Each transition validates the record's current status before writing (same
discipline as `ValidationService`/`AttendanceService.confirm_attendance`)
and raises `HTTPException(409, ...)` on an invalid origin — matching the
existing service style of raising HTTPException directly rather than a
plain exception mapped later at the router layer.

Reenvio (resubmit after a rejection): `justify` on an `absent` record that
already carries a prior `justification` archives that entry into
`justificationHistory[]` before writing the new one.

Scope note: this module is the transition core only (model + service).
HTTP endpoints, role gates, prazo (7 dias) and attachment-required
validation land in later tasks (B3–B5), which call these functions.

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
(sections "Decisões" → "Fluxo de status", "Modelo de dados").
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from firebase_admin import firestore

from app.domain.enums import ValidationStatus
from app.logging.decorator import log
from app.models.attendance import Justification, JustificationAttachment

_ATTENDANCE = "attendance"
_USERS = "users"


class JustificationService:

    @staticmethod
    def _load_attendance(db, doc_id: str, project_id: str) -> tuple:
        """Fetch + ownership-check an attendance doc. Returns (ref, data)."""
        doc_ref = db.collection(_ATTENDANCE).document(doc_id)
        doc = doc_ref.get()
        if not doc.exists:
            raise HTTPException(
                status_code=404, detail="Registro de frequência não encontrado",
            )
        data = doc.to_dict()
        if data.get("projectId") != project_id:
            raise HTTPException(
                status_code=403, detail="Acesso negado ao registro",
            )
        return doc_ref, data

    @staticmethod
    def _actor_name(db, actor_uid: str) -> str:
        actor_doc = db.collection(_USERS).document(actor_uid).get()
        return actor_doc.to_dict().get("name", "") if actor_doc.exists else ""

    @log
    def justify(
        self,
        doc_id: str,
        project_id: str,
        actor_uid: str,
        type_id: str,
        type_name: str,
        text: str,
        attachment: JustificationAttachment | dict | None = None,
    ) -> dict:
        """`absent` → `absent_justification_pending`.

        Also covers reenvio: when the record already carries a prior
        `justification` (i.e. it was previously rejected back to `absent`),
        that entry is archived into `justificationHistory[]` first.
        """
        db = firestore.client()
        doc_ref, data = self._load_attendance(db, doc_id, project_id)

        if data.get("status") != ValidationStatus.ABSENT:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Registro não está 'absent' — status atual: "
                    f"{data.get('status')}"
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        justification = Justification(
            type_id=type_id,
            type_name=type_name,
            text=text,
            attachment=attachment,
            submitted_at=now,
            submitted_by=actor_uid,
        )
        justification_payload = justification.model_dump(by_alias=True)

        update: dict = {
            "status": ValidationStatus.ABSENT_JUSTIFICATION_PENDING.value,
            "justification": justification_payload,
        }

        prior = data.get("justification")
        if prior:
            history = list(data.get("justificationHistory") or [])
            history.append(prior)
            update["justificationHistory"] = history

        doc_ref.update(update)
        return justification_payload

    @log
    def approve(
        self,
        doc_id: str,
        project_id: str,
        actor_uid: str,
    ) -> dict:
        """`absent_justification_pending` → `absent_justified`."""
        db = firestore.client()
        doc_ref, data = self._load_attendance(db, doc_id, project_id)

        if data.get("status") != ValidationStatus.ABSENT_JUSTIFICATION_PENDING:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Registro não está com justificativa pendente — status "
                    f"atual: {data.get('status')}"
                ),
            )
        if not data.get("justification"):
            raise HTTPException(
                status_code=409,
                detail="Registro pendente sem justificativa associada",
            )

        now = datetime.now(timezone.utc).isoformat()
        justification = Justification.model_validate(data["justification"])
        justification.reviewed_at = now
        justification.reviewed_by = actor_uid
        justification.reviewed_by_name = self._actor_name(db, actor_uid)
        justification_payload = justification.model_dump(by_alias=True)

        doc_ref.update({
            "status": ValidationStatus.ABSENT_JUSTIFIED.value,
            "justification": justification_payload,
        })
        return justification_payload

    @log
    def reject(
        self,
        doc_id: str,
        project_id: str,
        actor_uid: str,
        reason: str,
    ) -> dict:
        """`absent_justification_pending` → `absent`. Requires `reason`."""
        if not reason or not reason.strip():
            raise HTTPException(
                status_code=422, detail="Motivo da recusa é obrigatório",
            )

        db = firestore.client()
        doc_ref, data = self._load_attendance(db, doc_id, project_id)

        if data.get("status") != ValidationStatus.ABSENT_JUSTIFICATION_PENDING:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Registro não está com justificativa pendente — status "
                    f"atual: {data.get('status')}"
                ),
            )
        if not data.get("justification"):
            raise HTTPException(
                status_code=409,
                detail="Registro pendente sem justificativa associada",
            )

        now = datetime.now(timezone.utc).isoformat()
        justification = Justification.model_validate(data["justification"])
        justification.reviewed_at = now
        justification.reviewed_by = actor_uid
        justification.reviewed_by_name = self._actor_name(db, actor_uid)
        justification.reject_reason = reason
        justification_payload = justification.model_dump(by_alias=True)

        # Record returns to plain `absent`, keeping the justification (with
        # rejectReason) on the record for audit. It only moves into
        # `justificationHistory[]` on a subsequent reenvio (see `justify`).
        doc_ref.update({
            "status": ValidationStatus.ABSENT.value,
            "justification": justification_payload,
        })
        return justification_payload
