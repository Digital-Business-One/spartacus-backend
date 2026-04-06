"""ValidationService — confirm/absent/review (RFC-11)."""

from datetime import datetime, timezone

from firebase_admin import firestore

from app.domain.enums import ValidationStatus
from app.events.models import DomainEvent, ReviewRequestedPayload, ValidationPayload
from app.logging.decorator import log


class ValidationService:

    @log
    def validate(
        self,
        collection: str,
        doc_id: str,
        project_id: str,
        status: str,
        actor_uid: str,
    ) -> DomainEvent:
        """Confirm or mark absent on a presenca/doacao record."""
        if status not in (ValidationStatus.CONFIRMED, ValidationStatus.ABSENT):
            raise ValueError(f"Status inválido: {status}")

        db = firestore.client()
        doc_ref = db.collection(collection).document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            raise LookupError(f"Registro não encontrado: {collection}/{doc_id}")

        data = doc.to_dict()
        if data.get("projectId") != project_id:
            raise PermissionError("Acesso negado ao registro")

        now = datetime.now(timezone.utc).isoformat()
        doc_ref.update({
            "status": status,
            "validatedBy": actor_uid,
            "validatedAt": now,
        })

        # Map collection → event prefix
        prefix = "checkin" if collection == "presencas" else "donation"
        event_id = f"{prefix}.{status}"

        return DomainEvent(
            id=event_id,
            payload=ValidationPayload(
                entity_id=doc_id,
                target_uid=data.get("userId", ""),
                target_name=data.get("userName", ""),
                validated_by=actor_uid,
                validated_at=now,
                turma_name=data.get("turmaName", ""),
                donation_amount=data.get("amount", ""),
            ),
        )

    @log
    def request_review(
        self,
        collection: str,
        doc_id: str,
        project_id: str,
        actor_uid: str,
    ) -> DomainEvent:
        """Request review on an absent record. One-time only."""
        db = firestore.client()
        doc_ref = db.collection(collection).document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            raise LookupError(f"Registro não encontrado: {collection}/{doc_id}")

        data = doc.to_dict()
        if data.get("projectId") != project_id:
            raise PermissionError("Acesso negado ao registro")

        # Only the target user (or guardian) can request review
        if data.get("userId") != actor_uid:
            raise PermissionError("Apenas o titular pode solicitar revisão")

        if data.get("status") != ValidationStatus.ABSENT:
            raise ValueError("Revisão só é possível para registros não confirmados")

        if data.get("reviewRequested"):
            raise ValueError("Revisão já solicitada para este registro")

        now = datetime.now(timezone.utc).isoformat()
        doc_ref.update({
            "reviewRequested": True,
            "reviewRequestedAt": now,
        })

        prefix = "checkin" if collection == "presencas" else "donation"

        return DomainEvent(
            id=f"{prefix}.review_requested",
            payload=ReviewRequestedPayload(
                entity_id=doc_id,
                target_uid=data.get("userId", ""),
                target_name=data.get("userName", ""),
                review_requested_at=now,
                turma_name=data.get("turmaName", ""),
                donation_amount=data.get("amount", ""),
            ),
        )

    @log
    def resolve_review(
        self,
        collection: str,
        doc_id: str,
        project_id: str,
        actor_uid: str,
    ) -> None:
        """Staff maintains absence — closes review cycle."""
        db = firestore.client()
        doc_ref = db.collection(collection).document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            raise LookupError(f"Registro não encontrado: {collection}/{doc_id}")

        data = doc.to_dict()
        if data.get("projectId") != project_id:
            raise PermissionError("Acesso negado ao registro")

        if not data.get("reviewRequested"):
            raise ValueError("Nenhuma revisão pendente")

        now = datetime.now(timezone.utc).isoformat()
        doc_ref.update({
            "reviewResolved": True,
            "reviewResolvedAt": now,
        })
