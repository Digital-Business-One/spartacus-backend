"""Check-in service — matches user's classes to current time window.

An aula (class session) is created on-demand when a student checks in.
Document ID format: {turmaId}_{YYYYMMDD}_{HHmm}
"""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from firebase_admin import firestore

from app.events.models import CheckinRegisteredPayload, DomainEvent
from app.logging.decorator import log
from app.models.checkin import (
    AvailableCheckinOut,
    CheckinResponse,
    NextClassOut,
    NoCheckinAvailableOut,
)

_TZ_OFFSET = timezone(timedelta(hours=-4))  # Brasnorte-MT = UTC-4

_DAY_CODES = {
    0: "mon", 1: "tue", 2: "wed", 3: "thu",
    4: "fri", 5: "sat", 6: "sun",
}
_DAY_LABELS_PT = {
    "mon": "Segunda-feira", "tue": "Terça-feira",
    "wed": "Quarta-feira", "thu": "Quinta-feira",
    "fri": "Sexta-feira", "sat": "Sábado", "sun": "Domingo",
}
_DAY_SHORT_PT = {
    "mon": "Seg", "tue": "Ter", "wed": "Qua", "thu": "Qui",
    "fri": "Sex", "sat": "Sáb", "sun": "Dom",
}

_CHECKIN_WINDOW_BEFORE_MIN = 15


def _now_local() -> datetime:
    return datetime.now(_TZ_OFFSET)


def _parse_time(t: str) -> tuple[int, int]:
    parts = t.split(":")
    return int(parts[0]), int(parts[1])


class CheckinService:
    _USERS = "users"
    _CLASSES = "classes"
    _MODALITIES = "modalities"
    _AULAS = "aulas"
    _ATTENDANCE = "attendance"

    @log
    def get_available(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None = None,
    ) -> AvailableCheckinOut | NoCheckinAvailableOut:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        db = firestore.client()
        now = _now_local()
        today_code = _DAY_CODES[now.weekday()]

        # Get user's enrolled classes
        user_doc = db.collection(self._USERS).document(target_uid).get()
        if not user_doc.exists:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        class_ids = user_doc.to_dict().get("classIds", [])
        if not class_ids:
            return NoCheckinAvailableOut()

        # Batch fetch classes
        refs = [db.collection(self._CLASSES).document(cid) for cid in class_ids]
        class_docs = db.get_all(refs)

        # Find classes active right now
        matches = []
        all_schedules = []  # for next_class fallback

        for doc in class_docs:
            if not doc.exists:
                continue
            data = doc.to_dict()
            if not data.get("active", True):
                continue

            schedule = data.get("schedule") or []
            # Support legacy weeklySchedule
            if not schedule and data.get("weeklySchedule"):
                ws = data["weeklySchedule"]
                schedule = [
                    {"day": d, "startTime": ws.get("startTime", ""),
                     "endTime": ws.get("endTime", "")}
                    for d in ws.get("days", [])
                ]

            for item in schedule:
                all_schedules.append((doc, data, item))
                if item.get("day") != today_code:
                    continue

                sh, sm = _parse_time(item["startTime"])
                eh, em = _parse_time(item["endTime"])
                start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
                end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
                window_start = start - timedelta(minutes=_CHECKIN_WINDOW_BEFORE_MIN)

                if window_start <= now <= end:
                    matches.append((doc, data, item, start, end))

        if not matches:
            next_class = self._find_next_class(db, all_schedules, now)
            return NoCheckinAvailableOut(next_class=next_class)

        # Use first match (if multiple, could show selection — for now first)
        doc, data, item, start, end = matches[0]
        turma_id = doc.id

        # Resolve modality name
        modality_name = self._resolve_modality(db, data)

        # Get or create aula
        date_str = now.strftime("%Y%m%d")
        time_str = item["startTime"].replace(":", "")
        aula_id = f"{turma_id}_{date_str}_{time_str}"
        self._ensure_aula(
            db, aula_id, project_id, turma_id,
            start.isoformat(), end.isoformat(),
        )

        # Check if already checked in
        already = self._already_checked_in(db, project_id, target_uid, aula_id)

        return AvailableCheckinOut(
            aula_id=aula_id,
            turma_id=turma_id,
            turma_name=data.get("name", ""),
            modality_name=modality_name,
            date=now.strftime("%d/%m/%Y"),
            day_of_week=_DAY_LABELS_PT.get(today_code, ""),
            start_time=item["startTime"],
            end_time=item["endTime"],
            teacher=data.get("teacherName"),
            location=data.get("location"),
            already_checked_in=already,
        )

    @log
    def checkin(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None,
        aula_id: str,
    ) -> CheckinResponse:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        db = firestore.client()

        # Validate aula exists
        aula_doc = db.collection(self._AULAS).document(aula_id).get()
        if not aula_doc.exists:
            raise HTTPException(
                status_code=400, detail="Aula não encontrada",
            )

        # Check duplicate
        if self._already_checked_in(db, project_id, target_uid, aula_id):
            raise HTTPException(
                status_code=409, detail="Presença já registrada nesta aula",
            )

        # Validate time window
        aula_data = aula_doc.to_dict()
        now = _now_local()
        end_str = aula_data.get("endTime", "")
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str)
                if now > end_dt:
                    raise HTTPException(
                        status_code=400,
                        detail="A janela de check-in já encerrou",
                    )
            except ValueError:
                pass

        # Resolve names so we can denormalize on the attendance doc
        now_iso = datetime.now(timezone.utc).isoformat()
        turma_id = aula_data.get("turmaId", "")

        user_doc = db.collection(self._USERS).document(target_uid).get()
        user_name = ""
        if user_doc.exists:
            user_name = user_doc.to_dict().get("name", "")

        turma_doc = db.collection(self._CLASSES).document(turma_id).get()
        turma_name = ""
        modality_name = ""
        teacher_name: str | None = None
        if turma_doc.exists:
            td = turma_doc.to_dict()
            turma_name = td.get("name", "")
            modality_name = self._resolve_modality(db, td)
            teacher_name = td.get("teacherName") or td.get("teacher")

        # Create attendance record (with denormalized names)
        attendance_data = {
            "projectId": project_id,
            "userId": target_uid,
            "userName": user_name,
            "actingAs": acting_as,
            "aulaId": aula_id,
            "turmaId": turma_id,
            "turmaName": turma_name,
            "teacherName": teacher_name,
            "timestamp": now_iso,
            "status": "registered",
            "validatedBy": None,
            "validatedAt": None,
            "reviewRequested": False,
            "reviewRequestedAt": None,
            "reviewResolved": False,
            "reviewResolvedAt": None,
        }

        _, ref = db.collection(self._ATTENDANCE).add(attendance_data)

        event = DomainEvent(
            id="checkin.registered",
            payload=CheckinRegisteredPayload(
                entity_id=ref.id,
                source_entity_ref=f"attendance/{ref.id}",
                source_entity_type="attendance",
                target_uid=target_uid,
                target_name=user_name,
                author_uid=uid,
                author_name=user_name,
                turma_name=turma_name,
                modalidade_name=modality_name,
                class_date=now.strftime("%d/%m/%Y %H:%M"),
            ),
        )

        return CheckinResponse(
            attendance_id=ref.id,
            status="registered",
            timestamp=now_iso,
        ), event

    # ── Helpers ──────────────────────────────────────────────────────

    def _ensure_aula(
        self, db, aula_id, project_id, turma_id,
        start_iso, end_iso,
    ):
        ref = db.collection(self._AULAS).document(aula_id)
        if ref.get().exists:
            return
        ref.set({
            "projectId": project_id,
            "turmaId": turma_id,
            "startTime": start_iso,
            "endTime": end_iso,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })

    def _already_checked_in(
        self, db, project_id, uid, aula_id,
    ) -> bool:
        results = list(
            db.collection(self._ATTENDANCE)
            .where("projectId", "==", project_id)
            .where("userId", "==", uid)
            .where("aulaId", "==", aula_id)
            .limit(1)
            .stream()
        )
        return len(results) > 0

    def _resolve_modality(self, db, class_data: dict) -> str:
        mid = class_data.get("modalityId", "")
        if mid:
            mod_doc = db.collection(self._MODALITIES).document(mid).get()
            if mod_doc.exists:
                return mod_doc.to_dict().get("name", "")
        return class_data.get("modality", "")

    def _find_next_class(
        self, db, all_schedules, now: datetime,
    ) -> NextClassOut | None:
        today_idx = now.weekday()
        day_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

        best = None
        best_distance = 999

        for doc, data, item in all_schedules:
            day_code = item.get("day", "")
            if day_code not in day_order:
                continue
            day_idx = day_order.index(day_code)
            sh, sm = _parse_time(item["startTime"])

            # Distance in days (wrap around week)
            if day_idx > today_idx:
                dist = day_idx - today_idx
            elif day_idx == today_idx:
                class_time = now.replace(
                    hour=sh, minute=sm, second=0, microsecond=0,
                )
                if class_time > now:
                    dist = 0
                else:
                    dist = 7
            else:
                dist = 7 - (today_idx - day_idx)

            if dist < best_distance:
                best_distance = dist
                modality_name = self._resolve_modality(db, data)
                best = NextClassOut(
                    turma_name=data.get("name", ""),
                    modality_name=modality_name,
                    day_of_week=_DAY_SHORT_PT.get(day_code, ""),
                    start_time=item["startTime"],
                )

        return best

    def _assert_guardian_of(
        self, guardian_uid: str, target_uid: str,
    ) -> None:
        db = firestore.client()
        doc = db.collection(self._USERS).document(target_uid).get()
        if not doc.exists:
            raise HTTPException(
                status_code=404, detail="Dependente não encontrado",
            )
        if doc.to_dict().get("guardianUid") != guardian_uid:
            raise HTTPException(
                status_code=403,
                detail="Você não é responsável deste dependente",
            )
