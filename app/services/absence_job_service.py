"""AbsenceJobService — computes absences for classes without check-in (RFC-11).

Triggered daily at 23:59 by Cloud Scheduler.
For each class that ended today without a check-in record,
creates a presenca document with status "absent" and emits checkin.absent event.
"""

from datetime import datetime, timezone

from firebase_admin import firestore

from app.domain.enums import ValidationStatus
from app.events.models import CheckinRegisteredPayload, DomainEvent
from app.logging.decorator import log


class AbsenceJobService:

    @log
    def compute(self, project_id: str) -> list[DomainEvent]:
        """Compute absences for all classes that ended today without check-in.

        Returns list of DomainEvents (checkin.absent) for each absence created.
        """
        db = firestore.client()
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Find aulas that ended today
        aulas = (
            db.collection("aulas")
            .where("projectId", "==", project_id)
            .where("endDate", ">=", today_start.isoformat())
            .where("endDate", "<=", now.isoformat())
            .stream()
        )

        events = []
        for aula_doc in aulas:
            aula = aula_doc.to_dict()
            aula_id = aula_doc.id
            turma_id = aula.get("turmaId", "")

            # Get turma info
            turma_doc = db.collection("turmas").document(turma_id).get()
            if not turma_doc.exists:
                continue
            turma = turma_doc.to_dict()
            turma_name = turma.get("nome", "")
            modalidade_name = turma.get("modalidadeNome", "")

            # Get enrolled students
            memberships = (
                db.collection("memberships")
                .where("projectId", "==", project_id)
                .where("status", "==", "active")
                .stream()
            )
            student_uids = []
            for m in memberships:
                m_data = m.to_dict()
                if "student" in m_data.get("roles", []):
                    # Check if student is enrolled in this turma
                    user_classes = m_data.get("classIds", [])
                    if turma_id in user_classes or not user_classes:
                        student_uids.append(m_data["userId"])

            # Find students who already have a presenca for this aula
            existing = (
                db.collection("presencas")
                .where("projectId", "==", project_id)
                .where("aulaId", "==", aula_id)
                .stream()
            )
            checked_in = {p.to_dict().get("userId") for p in existing}

            # Create absences for students without check-in
            for uid in student_uids:
                if uid in checked_in:
                    continue

                # Get student name
                user_doc = db.collection("users").document(uid).get()
                user_data = user_doc.to_dict() if user_doc.exists else {}
                user_name = user_data.get("name", "")

                absence_now = datetime.now(timezone.utc).isoformat()
                _, presenca_ref = db.collection("presencas").add({
                    "projectId": project_id,
                    "userId": uid,
                    "userName": user_name,
                    "aulaId": aula_id,
                    "turmaId": turma_id,
                    "turmaName": turma_name,
                    "status": ValidationStatus.ABSENT,
                    "validatedBy": "system",
                    "validatedAt": absence_now,
                    "reviewRequested": False,
                    "reviewRequestedAt": None,
                    "reviewResolved": False,
                    "reviewResolvedAt": None,
                    "createdAt": absence_now,
                })

                events.append(
                    DomainEvent(
                        id="checkin.absent",
                        payload=CheckinRegisteredPayload(
                            entity_id=presenca_ref.id,
                            source_entity_ref=f"presencas/{presenca_ref.id}",
                            source_entity_type="presencas",
                            target_uid=uid,
                            target_name=user_name,
                            author_uid="system",
                            author_name="Sistema",
                            turma_name=turma_name,
                            modalidade_name=modalidade_name,
                            class_date=aula.get("dateTime", ""),
                        ),
                    )
                )

        return events
