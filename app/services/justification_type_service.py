"""JustificationTypeService — CRUD for the `justification_types` catalog.

Per-project configurable catalog of absence-justification types (Sub-projeto
B, Justificativa de Faltas). Mirrors `GraduationService`'s matrix
convention: one doc per type, id "{projectId}_{slug}". Non-staff
(student/guardian) sees only active types; staff (backoffice) sees all,
including inactive, for CRUD. DELETE deactivates — never hard-deletes,
since past justifications keep a denormalized `typeName` snapshot on the
`attendance` doc (out of scope here; see `app.services.justification_service`).

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
(section "Tipos de justificativa").
"""

from firebase_admin import firestore

from app.logging.decorator import log
from app.models.justification_type import (
    JustificationType,
    JustificationTypeUpsertRequest,
)

_COLLECTION = "justification_types"


class JustificationTypeService:

    @staticmethod
    def _doc_id(project_id: str, slug: str) -> str:
        return f"{project_id}_{slug}"

    @staticmethod
    def _doc_to_type(data: dict) -> JustificationType:
        return JustificationType(
            project_id=data.get("projectId", ""),
            slug=data.get("slug", ""),
            name=data.get("name", ""),
            allows_attachment=data.get("allowsAttachment", False),
            requires_attachment=data.get("requiresAttachment", False),
            active=data.get("active", True),
            order=data.get("order", 0),
        )

    @log
    def list_types(
        self, project_id: str, include_inactive: bool,
    ) -> list[JustificationType]:
        db = firestore.client()
        docs = (
            db.collection(_COLLECTION)
            .where("projectId", "==", project_id)
            .stream()
        )
        types = [self._doc_to_type(d.to_dict()) for d in docs]
        if not include_inactive:
            types = [t for t in types if t.active]
        types.sort(key=lambda t: t.order)
        return types

    @log
    def upsert(
        self, project_id: str, slug: str, body: JustificationTypeUpsertRequest,
    ) -> JustificationType:
        if body.requires_attachment and not body.allows_attachment:
            raise ValueError(
                "requiresAttachment=true exige allowsAttachment=true",
            )
        data = {
            "projectId": project_id,
            "slug": slug,
            "name": body.name,
            "allowsAttachment": body.allows_attachment,
            "requiresAttachment": body.requires_attachment,
            "active": body.active,
            "order": body.order,
        }
        db = firestore.client()
        db.collection(_COLLECTION).document(
            self._doc_id(project_id, slug),
        ).set(data)
        return self._doc_to_type(data)

    @log
    def deactivate(self, project_id: str, slug: str) -> None:
        db = firestore.client()
        doc_ref = db.collection(_COLLECTION).document(
            self._doc_id(project_id, slug),
        )
        if not doc_ref.get().exists:
            raise LookupError("Tipo de justificativa não encontrado")
        doc_ref.update({"active": False})
