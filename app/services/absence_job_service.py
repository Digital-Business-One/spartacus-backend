"""AbsenceJobService — computes absences for classes without check-in (RFC-11).

Triggered daily at 23:59 by Cloud Scheduler.
For each class that ended today without a check-in record,
creates an attendance document with status "absent" and emits checkin.absent event.
"""

import logging
from datetime import datetime, timezone

from firebase_admin import firestore

from app.domain.enums import ValidationStatus
from app.events.models import DomainEvent, ValidationPayload
from app.logging.decorator import log
from app.services.graduation_snapshot import (
    build_graduation_snapshot,
    resolve_modality_slug,
)

logger = logging.getLogger(__name__)

# Stable, greppable marker for Cloud Logging — see design spec "Guarda-costas".
_ENGINE_INCONSISTENCY_MARKER = "attendance-engine-inconsistency"


def _engine_active(class_id: str, class_data: dict) -> bool:
    """Attendance engine gate for a turma (RFC "Frequência Analítica").

    Enabled without a start date is an inconsistent config — treated as off
    (no counting, no absences) and logged so the admin can fix it in the
    backoffice (the marker + classId are greppable in Cloud Logging).
    """
    enabled = class_data.get("attendanceEngineEnabled") is True
    start_date = class_data.get("attendanceStartDate")
    if enabled and not start_date:
        logger.error(
            "%s classId=%s attendanceEngineEnabled=true sem "
            "attendanceStartDate",
            _ENGINE_INCONSISTENCY_MARKER,
            class_id,
        )
        return False
    return enabled and bool(start_date)


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
            .where("endTime", ">=", today_start.isoformat())
            .where("endTime", "<=", now.isoformat())
            .stream()
        )

        events = []
        for aula_doc in aulas:
            aula = aula_doc.to_dict()
            aula_id = aula_doc.id
            turma_id = aula.get("turmaId", "")

            # Get class info (collection is "classes", not "turmas")
            class_doc = db.collection("classes").document(turma_id).get()
            if not class_doc.exists:
                continue
            class_data = class_doc.to_dict()

            # Attendance engine gate: skip turmas with the engine off (or
            # inconsistently configured — logged inside _engine_active).
            if not _engine_active(turma_id, class_data):
                continue

            # Never retroact: only mark absences for dates on/after the
            # turma's data-base (attendanceStartDate).
            start_date = class_data.get("attendanceStartDate", "")
            aula_date = (aula.get("endTime") or aula.get("startTime") or "")[:10]
            if aula_date and start_date and aula_date < start_date:
                continue

            class_name = class_data.get("name", "")
            # Denormalized once per aula/turma — same for every student
            # marked absent below.
            modality_slug = resolve_modality_slug(db, class_data)

            # Get enrolled students via classIds on user docs
            enrolled_users = list(
                db.collection("users")
                .where("classIds", "array_contains", turma_id)
                .stream()
            )

            # Find students who already have an attendance record for this aula
            existing = (
                db.collection("attendance")
                .where("projectId", "==", project_id)
                .where("aulaId", "==", aula_id)
                .stream()
            )
            checked_in = {p.to_dict().get("userId") for p in existing}

            # Create absences for students without check-in
            for user_doc in enrolled_users:
                uid = user_doc.id
                if uid in checked_in:
                    continue

                user_data = user_doc.to_dict()
                user_name = user_data.get("name", "")
                graduation_snapshot = build_graduation_snapshot(
                    user_data, modality_slug,
                )

                absence_now = datetime.now(timezone.utc).isoformat()
                _, attendance_ref = db.collection("attendance").add({
                    "projectId": project_id,
                    "userId": uid,
                    "userName": user_name,
                    "aulaId": aula_id,
                    "turmaId": turma_id,
                    "turmaName": class_name,
                    "modalitySlug": modality_slug,
                    "graduationSnapshot": graduation_snapshot,
                    "timestamp": absence_now,
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
                        payload=ValidationPayload(
                            entity_id=attendance_ref.id,
                            target_uid=uid,
                            target_name=user_name,
                            validated_by="system",
                            validated_at=absence_now,
                            turma_name=class_name,
                        ),
                    )
                )

        return events
