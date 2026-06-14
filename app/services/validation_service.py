"""ValidationService — confirm/absent/review (RFC-11)."""

from datetime import datetime, timezone

from firebase_admin import firestore

from app.domain.enums import ValidationStatus
from app.events.models import DomainEvent, ReviewRequestedPayload, ValidationPayload
from app.logging.decorator import log
from app.services.account_history_service import AccountHistoryService

_SUPPORT_COLLECTIONS = ("support", "donations")


class ValidationService:

    _ATTENDANCE_VALID_STATUSES = (
        ValidationStatus.CONFIRMED,
        ValidationStatus.ABSENT,
        ValidationStatus.ABSENT_JUSTIFIED,
    )
    _DONATION_VALID_STATUSES = (
        ValidationStatus.RECEIVED,
        ValidationStatus.ABSENT,
    )

    @staticmethod
    def _support_label(data: dict) -> str:
        """Human label for a support doc (uses stored itemLabel)."""
        item_label = data.get("itemLabel") or data.get("item", "")
        desc = data.get("itemDescription") or ""
        return f"{item_label}: {desc}" if desc else item_label

    @staticmethod
    def _describe_validation(
        collection: str, status: str, data: dict, actor_name: str,
    ) -> str:
        if collection == "attendance":
            turma = data.get("turmaName", "treino")
            labels = {
                "confirmed": f"Presença confirmada em {turma}",
                "absent": f"Falta registrada em {turma}",
                "absent_justified": f"Falta justificada em {turma}",
            }
        else:
            label = data.get("itemLabel") or data.get("item", "apoio")
            labels = {
                "received": f"Apoio validado ({label})",
                "absent": f"Apoio recusado ({label})",
            }
        base = labels.get(status, f"{collection}: {status}")
        return f"{base} por {actor_name}" if actor_name else base

    @log
    def validate(
        self,
        collection: str,
        doc_id: str,
        project_id: str,
        status: str,
        actor_uid: str,
    ) -> DomainEvent:
        """Confirm or mark absent on an attendance/donation record."""
        if collection == "attendance":
            valid = self._ATTENDANCE_VALID_STATUSES
        elif collection in _SUPPORT_COLLECTIONS:
            valid = self._DONATION_VALID_STATUSES
        else:
            raise ValueError(f"Coleção inválida: {collection}")

        if status not in valid:
            raise ValueError(f"Status inválido para {collection}: {status}")

        db = firestore.client()
        doc_ref = db.collection(collection).document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            raise LookupError(f"Registro não encontrado: {collection}/{doc_id}")

        data = doc.to_dict()
        if data.get("projectId") != project_id:
            raise PermissionError("Acesso negado ao registro")

        now = datetime.now(timezone.utc).isoformat()
        update_payload = {
            "status": status,
            "validatedBy": actor_uid,
            "validatedAt": now,
        }
        # When confirming/approving, remember the column the card came from
        # so undo can return it there (middle when self-registered).
        if status in ("confirmed", "received"):
            cur = data.get("status")
            update_payload["previousStatus"] = (
                cur if cur not in ("confirmed", "received")
                else data.get("previousStatus")
            )
        doc_ref.update(update_payload)

        # Record in account history (target user)
        target_uid = data.get("userId", "")
        if target_uid:
            actor_name = ""
            actor_doc = db.collection("users").document(actor_uid).get()
            if actor_doc.exists:
                actor_name = actor_doc.to_dict().get("name", "")
            history_event_type = (
                "attendance" if collection == "attendance" else "support"
            )
            AccountHistoryService().record(
                uid=target_uid,
                project_id=project_id,
                event_type=history_event_type,
                event_subtype=status,
                actor_uid=actor_uid,
                actor_name=actor_name,
                actor_roles=[],
                description=self._describe_validation(
                    collection, status, data, actor_name,
                ),
            )

        # Map collection → event prefix
        prefix = "checkin" if collection == "attendance" else "support"
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
                donation_amount=(
                    self._support_label(data)
                    if collection in _SUPPORT_COLLECTIONS else ""
                ),
            ),
        )

    @log
    def undo_validation(
        self,
        collection: str,
        doc_id: str,
        project_id: str,
        actor_uid: str,
    ) -> DomainEvent:
        """Revert a confirmation made by mistake.

        The card returns to the **column it came from** — middle column when
        the user self-registered (check-in / pledged donation), or column 1
        when staff registered it straight from there. The origin is read from
        the `previousStatus` recorded at confirmation time; legacy records
        without it fall back to the middle column. No push is sent.
        """
        if collection == "attendance":
            expected, default_prev = "confirmed", "registered"
        elif collection in _SUPPORT_COLLECTIONS:
            expected, default_prev = "received", "pledged"
        else:
            raise ValueError(f"Coleção inválida: {collection}")

        db = firestore.client()
        doc_ref = db.collection(collection).document(doc_id)
        doc = doc_ref.get()
        if not doc.exists:
            raise LookupError(f"Registro não encontrado: {collection}/{doc_id}")

        data = doc.to_dict()
        if data.get("projectId") != project_id:
            raise PermissionError("Acesso negado ao registro")
        if data.get("status") != expected:
            raise ValueError(
                f"Registro não está {expected} — não há o que desfazer",
            )

        reverted = data.get("previousStatus") or default_prev

        now = datetime.now(timezone.utc).isoformat()
        update = {
            "status": reverted,
            "previousStatus": None,
            "validatedBy": None,
            "validatedAt": None,
        }
        if collection in _SUPPORT_COLLECTIONS:
            update.update({"receivedBy": None, "receivedAt": None})
        doc_ref.update(update)

        target_uid = data.get("userId", "")
        if target_uid:
            actor_doc = db.collection("users").document(actor_uid).get()
            actor_name = (
                actor_doc.to_dict().get("name", "") if actor_doc.exists else ""
            )
            event_type = (
                "attendance" if collection == "attendance" else "support"
            )
            AccountHistoryService().record(
                uid=target_uid,
                project_id=project_id,
                event_type=event_type,
                event_subtype="validation_undone",
                actor_uid=actor_uid,
                actor_name=actor_name,
                actor_roles=[],
                description="Confirmação desfeita"
                + (f" por {actor_name}" if actor_name else ""),
            )

        prefix = "checkin" if collection == "attendance" else "support"
        return DomainEvent(
            id=f"{prefix}.validation_undone",
            payload=ValidationPayload(
                entity_id=doc_id,
                target_uid=target_uid,
                target_name=data.get("userName", ""),
                validated_by=actor_uid,
                validated_at=now,
                turma_name=data.get("turmaName", ""),
                donation_amount=(
                    self._support_label(data)
                    if collection in _SUPPORT_COLLECTIONS else ""
                ),
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

        if data.get("status") not in (
            ValidationStatus.ABSENT, ValidationStatus.ABSENT_JUSTIFIED,
        ):
            raise ValueError("Revisão só é possível para registros não confirmados")

        if data.get("reviewRequested"):
            raise ValueError("Revisão já solicitada para este registro")

        now = datetime.now(timezone.utc).isoformat()
        doc_ref.update({
            "reviewRequested": True,
            "reviewRequestedAt": now,
        })

        prefix = "checkin" if collection == "attendance" else "support"

        return DomainEvent(
            id=f"{prefix}.review_requested",
            payload=ReviewRequestedPayload(
                entity_id=doc_id,
                target_uid=data.get("userId", ""),
                target_name=data.get("userName", ""),
                review_requested_at=now,
                turma_name=data.get("turmaName", ""),
                donation_amount=(
                    self._support_label(data)
                    if collection in _SUPPORT_COLLECTIONS else ""
                ),
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
