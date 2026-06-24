from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from firebase_admin import firestore

from app.domain.account_states import AccountStatus
from app.events.models import DomainEvent
from app.logging.decorator import log
from app.models.medical_history import (
    DailyActivitiesIn,
    HealthBehaviorIn,
    MedicalHistoryIn,
    MedicalHistoryOut,
    MedicalHistoryRequest,
    PendingAnamneseItem,
    SymptomsIn,
)


class MedicalHistoryService:
    _COLLECTION = "medical_history"
    _USERS = "users"

    @log
    def submit(
        self,
        project_id: str,
        user_id: str,
        data: MedicalHistoryRequest,
        actor_uid: str,
    ) -> tuple[MedicalHistoryOut, DomainEvent | None]:
        """Submit medical history.

        - user_id: target user (whose anamnese this is)
        - actor_uid: who is filling (may be guardian filling for dependent)
        """
        # If actor != target, validate guardian relationship
        if actor_uid != user_id:
            self._assert_guardian_of(actor_uid, user_id)

        db = firestore.client()
        now = datetime.now(timezone.utc).isoformat()
        doc_id = f"{project_id}_{user_id}"

        doc_data = {
            "projectId": project_id,
            "userId": user_id,
            "status": "pending_approval",
            "dailyActivities": (
                data.daily_activities.model_dump(by_alias=True)
                if data.daily_activities
                else None
            ),
            "medicalHistory": data.medical_history.model_dump(by_alias=True),
            "healthBehavior": data.health_behavior.model_dump(by_alias=True),
            "goals": data.goals,
            "goalsOther": data.goals_other,
            "generalComments": data.general_comments,
            "filledAt": now,
            "filledBy": actor_uid,
            "reviewedAt": None,
            "reviewedBy": None,
            "reviewNote": None,
        }

        db.collection(self._COLLECTION).document(doc_id).set(doc_data)

        out = MedicalHistoryOut(
            project_id=project_id,
            user_id=user_id,
            status="pending_approval",
            daily_activities=data.daily_activities,
            medical_history=data.medical_history,
            health_behavior=data.health_behavior,
            goals=data.goals,
            goals_other=data.goals_other,
            general_comments=data.general_comments,
            filled_at=now,
            filled_by=actor_uid,
            review_note=None,
        )

        return out, None

    @log
    def list_pending(
        self, project_id: str, requesting_uid: str
    ) -> list[PendingAnamneseItem]:
        """Return list of users (self + dependents) needing anamnese."""
        db = firestore.client()
        items: list[PendingAnamneseItem] = []

        # Self
        self_doc = db.collection(self._USERS).document(requesting_uid).get()
        if self_doc.exists:
            self_data = self_doc.to_dict()
            if (
                self_data.get("approvalStatus")
                == AccountStatus.WAITING_MEDICAL_HISTORY
            ):
                items.append(
                    PendingAnamneseItem(
                        uid=requesting_uid,
                        name=self_data.get("name", ""),
                        birth_date=self_data.get("birthDate", ""),
                        is_self=True,
                        is_dependent=False,
                    )
                )

        # Dependents whose status is waiting_medical_history
        deps = (
            db.collection(self._USERS)
            .where("guardianUid", "==", requesting_uid)
            .where("isDependent", "==", True)
            .stream()
        )
        for dep_doc in deps:
            dep = dep_doc.to_dict()
            if (
                dep.get("approvalStatus")
                == AccountStatus.WAITING_MEDICAL_HISTORY
            ):
                items.append(
                    PendingAnamneseItem(
                        uid=dep_doc.id,
                        name=dep.get("name", ""),
                        birth_date=dep.get("birthDate", ""),
                        is_self=False,
                        is_dependent=True,
                    )
                )

        return items

    def _assert_guardian_of(
        self, guardian_uid: str, target_uid: str
    ) -> None:
        db = firestore.client()
        target_doc = db.collection(self._USERS).document(target_uid).get()
        if not target_doc.exists:
            raise HTTPException(
                status_code=404, detail="Dependente não encontrado"
            )
        target = target_doc.to_dict()
        if not target.get("isDependent"):
            raise HTTPException(
                status_code=403,
                detail="Usuário alvo não é dependente",
            )
        if target.get("guardianUid") != guardian_uid:
            raise HTTPException(
                status_code=403,
                detail="Você não é responsável deste dependente",
            )

    @log
    def get_admin(
        self, project_id: str, user_id: str,
    ) -> MedicalHistoryOut:
        """Read medical history for a user — staff version (no guardian check).

        Authorization is enforced upstream by the router (owner/assistant only).
        """
        result = self.get(project_id, user_id)
        if result is None:
            raise LookupError("Anamnese não encontrada")
        return result

    @log
    def review(
        self,
        project_id: str,
        user_id: str,
        action: str,
        note: str,
        reviewer_uid: str,
    ) -> MedicalHistoryOut:
        db = firestore.client()
        doc_id = f"{project_id}_{user_id}"
        ref = db.collection(self._COLLECTION).document(doc_id)
        if not ref.get().exists:
            raise LookupError("Anamnese não encontrada")

        now = datetime.now(timezone.utc).isoformat()
        new_status = "approved" if action == "approve" else "needs_revision"
        ref.update(
            {
                "status": new_status,
                "reviewedAt": now,
                "reviewedBy": reviewer_uid,
                "reviewNote": note or None,
            }
        )

        # Audit trail on the account history
        reviewer_doc = db.collection(self._USERS).document(reviewer_uid).get()
        reviewer_name = (
            reviewer_doc.to_dict().get("name", "") if reviewer_doc.exists else ""
        )
        from app.services.account_history_service import AccountHistoryService

        AccountHistoryService().record(
            uid=user_id,
            project_id=project_id,
            event_type="account",
            event_subtype=f"anamnese_{action}",
            actor_uid=reviewer_uid,
            actor_name=reviewer_name,
            actor_roles=[],
            description=(
                "Anamnese aprovada"
                if action == "approve"
                else f"Anamnese devolvida para revisão: {note}"
            ),
        )

        result = self.get(project_id, user_id)
        assert result is not None
        return result

    @log
    def get(
        self, project_id: str, user_id: str
    ) -> Optional[MedicalHistoryOut]:
        db = firestore.client()
        doc_id = f"{project_id}_{user_id}"
        doc = db.collection(self._COLLECTION).document(doc_id).get()
        if not doc.exists:
            return None
        d = doc.to_dict()
        return MedicalHistoryOut(
            project_id=d.get("projectId", ""),
            user_id=d.get("userId", ""),
            status=d.get("status", ""),
            daily_activities=(
                DailyActivitiesIn(**d["dailyActivities"])
                if d.get("dailyActivities")
                else None
            ),
            medical_history=MedicalHistoryIn(
                symptoms=SymptomsIn(**d["medicalHistory"]["symptoms"]),
                **{
                    k: v
                    for k, v in d["medicalHistory"].items()
                    if k != "symptoms"
                },
            ),
            health_behavior=HealthBehaviorIn(**d["healthBehavior"]),
            goals=d.get("goals", []),
            goals_other=d.get("goalsOther", ""),
            general_comments=d.get("generalComments", ""),
            filled_at=d.get("filledAt"),
            filled_by=d.get("filledBy"),
            reviewed_at=d.get("reviewedAt"),
            reviewed_by=d.get("reviewedBy"),
            review_note=d.get("reviewNote"),
        )
