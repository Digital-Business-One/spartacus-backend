"""AbsenceJobService — computes absences for classes without check-in (RFC-11).

Triggered daily at 23:59 by Cloud Scheduler.

The job is driven by the **schedule of each turma**, not by the `aulas` that
happen to exist: `aulas` is created on demand by the first check-in, so a day
where nobody checked in produced no `aula` — and therefore no absence at all.
The student saw a synthetic absence in the calendar that could not be
justified and did not count in the percentage. Walking the schedule
materializes the session (same deterministic `aulaId` as the check-in) and
creates a real absence for every enrolled student without a record.

See: docs/superpowers/specs/2026-07-29-frequencia-app-ajustes-design.md (§5)
"""

import logging
from datetime import date, datetime, timedelta, timezone

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

_TZ_OFFSET = timezone(timedelta(hours=-4))  # Brasnorte-MT = UTC-4

_DAY_CODES = {
    0: "mon", 1: "tue", 2: "wed", 3: "thu",
    4: "fri", 5: "sat", 6: "sun",
}


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


def _parse_time(value: str) -> tuple[int, int]:
    hh, _, mm = (value or "").partition(":")
    return int(hh), int(mm)


class Session:
    """One scheduled class session on a concrete date."""

    __slots__ = ("class_id", "day", "start", "end", "start_time")

    def __init__(self, class_id: str, day: date, start_time: str, end_time: str):
        sh, sm = _parse_time(start_time)
        eh, em = _parse_time(end_time)
        self.class_id = class_id
        self.day = day
        self.start_time = start_time
        self.start = datetime(
            day.year, day.month, day.day, sh, sm, tzinfo=_TZ_OFFSET,
        )
        self.end = datetime(
            day.year, day.month, day.day, eh, em, tzinfo=_TZ_OFFSET,
        )

    @property
    def aula_id(self) -> str:
        """Same deterministic id the check-in builds — one aula per session."""
        return (
            f"{self.class_id}_{self.day.strftime('%Y%m%d')}"
            f"_{self.start_time.replace(':', '')}"
        )


def schedule_for_day(class_id: str, class_data: dict, day: date) -> list[Session]:
    """Sessions of `class_data` on `day` (empty when it isn't a class day)."""
    schedule = class_data.get("schedule") or []
    if not schedule and class_data.get("weeklySchedule"):
        ws = class_data["weeklySchedule"]
        schedule = [
            {
                "day": d,
                "startTime": ws.get("startTime", ""),
                "endTime": ws.get("endTime", ""),
            }
            for d in ws.get("days", [])
        ]

    day_code = _DAY_CODES[day.weekday()]
    sessions = []
    for item in schedule:
        if item.get("day") != day_code:
            continue
        if not item.get("startTime") or not item.get("endTime"):
            continue
        sessions.append(
            Session(class_id, day, item["startTime"], item["endTime"]),
        )
    return sessions


class AbsenceJobService:
    _AULAS = "aulas"
    _CLASSES = "classes"
    _ATTENDANCE = "attendance"
    _USERS = "users"

    @log
    def compute(
        self, project_id: str, now: datetime | None = None,
    ) -> list[DomainEvent]:
        """Compute absences for every session of today that already ended.

        `now` (local time) is injectable so tests don't depend on the clock.
        Returns list of DomainEvents (checkin.absent) for each absence created.
        """
        db = firestore.client()
        now = now or datetime.now(_TZ_OFFSET)

        events: list[DomainEvent] = []
        for class_doc in self._active_classes(db, project_id):
            class_data = class_doc.to_dict()
            for session in schedule_for_day(
                class_doc.id, class_data, now.date(),
            ):
                if session.end > now:
                    continue  # aula ainda não terminou
                events += self.materialize_absences(
                    db, project_id, class_doc.id, class_data, session,
                )
        return events

    def _active_classes(self, db, project_id: str) -> list:
        """Turmas of the project with the attendance engine on."""
        docs = (
            db.collection(self._CLASSES)
            .where("projectId", "==", project_id)
            .stream()
        )
        result = []
        for doc in docs:
            data = doc.to_dict() or {}
            if data.get("active") is False:
                continue
            if not _engine_active(doc.id, data):
                continue
            result.append(doc)
        return result

    def materialize_absences(
        self,
        db,
        project_id: str,
        class_id: str,
        class_data: dict,
        session: Session,
    ) -> list[DomainEvent]:
        """Create the missing absences of one session. Idempotent.

        Guards (spec §5.1): the session must be on/after the turma's
        data-base and on/after the student's `createdAt`, and a student who
        already has any record for this `aulaId` is left alone — so a second
        run (or the backfill) creates nothing extra.
        """
        start_date = class_data.get("attendanceStartDate") or ""
        if start_date and session.day.isoformat() < start_date:
            return []

        self._ensure_aula(db, project_id, class_id, session)

        existing = (
            db.collection(self._ATTENDANCE)
            .where("projectId", "==", project_id)
            .where("aulaId", "==", session.aula_id)
            .stream()
        )
        already = {p.to_dict().get("userId") for p in existing}

        class_name = class_data.get("name", "")
        # Denormalized once per session — same for every student below.
        modality_slug = resolve_modality_slug(db, class_data)

        enrolled = (
            db.collection(self._USERS)
            .where("classIds", "array_contains", class_id)
            .stream()
        )

        events: list[DomainEvent] = []
        for user_doc in enrolled:
            uid = user_doc.id
            if uid in already:
                continue

            user_data = user_doc.to_dict() or {}
            if not self._enrolled_by(user_data, session):
                continue

            user_name = user_data.get("name", "")
            graduation_snapshot = build_graduation_snapshot(
                user_data, modality_slug,
            )

            # timestamp = início da aula (não o horário da execução do job):
            # é o que põe a falta no dia certo do calendário, e o que torna o
            # backfill de dias passados possível.
            absence_at = session.start.isoformat()
            created_at = datetime.now(timezone.utc).isoformat()
            _, attendance_ref = db.collection(self._ATTENDANCE).add({
                "projectId": project_id,
                "userId": uid,
                "userName": user_name,
                "aulaId": session.aula_id,
                "turmaId": class_id,
                "turmaName": class_name,
                "modalitySlug": modality_slug,
                "graduationSnapshot": graduation_snapshot,
                "timestamp": absence_at,
                "status": ValidationStatus.ABSENT,
                "validatedBy": "system",
                "validatedAt": absence_at,
                "reviewRequested": False,
                "reviewRequestedAt": None,
                "reviewResolved": False,
                "reviewResolvedAt": None,
                "createdAt": created_at,
            })

            events.append(
                DomainEvent(
                    id="checkin.absent",
                    payload=ValidationPayload(
                        entity_id=attendance_ref.id,
                        target_uid=uid,
                        target_name=user_name,
                        validated_by="system",
                        validated_at=absence_at,
                        turma_name=class_name,
                    ),
                )
            )

        return events

    @staticmethod
    def _enrolled_by(user_data: dict, session: Session) -> bool:
        """Whether the student already existed at the session's date.

        Without this the backfill would invent absences for whoever joined
        after the class happened. Missing `createdAt` = no guard (mirrors
        `_turma_window`, which falls back to the data-base alone).
        """
        created = user_data.get("createdAt")
        if not created or not isinstance(created, str):
            return True
        try:
            created_dt = datetime.fromisoformat(created)
        except (ValueError, TypeError):
            return True
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=_TZ_OFFSET)
        return session.start >= created_dt.astimezone(_TZ_OFFSET)

    def _ensure_aula(self, db, project_id: str, class_id: str, session: Session):
        ref = db.collection(self._AULAS).document(session.aula_id)
        if ref.get().exists:
            return
        ref.set({
            "projectId": project_id,
            "turmaId": class_id,
            "startTime": session.start.isoformat(),
            "endTime": session.end.isoformat(),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
