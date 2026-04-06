from datetime import datetime, timezone
from typing import Optional

from firebase_admin import firestore

from app.events.models import DomainEvent
from app.logging.decorator import log
from app.models.medical_history import (
    DailyActivitiesIn,
    HealthBehaviorIn,
    MedicalHistoryIn,
    MedicalHistoryOut,
    MedicalHistoryRequest,
    SymptomsIn,
)
from app.services.account_service import AccountService


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
        }

        db.collection(self._COLLECTION).document(doc_id).set(doc_data)

        # Trigger state transition:
        # waiting_medical_history → pending_medical_history_approval
        _, event = AccountService().execute_transition(
            project_id, user_id, "submit_medical_history", actor_uid
        )

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
        )

        return out, event

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
        )
