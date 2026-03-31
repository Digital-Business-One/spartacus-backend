"""Attendance history — monthly summaries with individual records."""

import calendar as cal
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from firebase_admin import firestore

from app.logging.decorator import log
from app.models.attendance import (
    AttendanceHistoryOut,
    AttendanceRecord,
    MonthSummary,
)

_TZ_OFFSET = timezone(timedelta(hours=-4))  # Brasnorte-MT = UTC-4

_DAY_CODES_TO_WEEKDAY = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}

_MONTH_NAMES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

_HISTORY_MONTHS = 6


class _ScheduleItem:
    """A class schedule item with its modality name."""

    __slots__ = ("day_code", "weekday", "modality_name")

    def __init__(self, day_code: str, modality_name: str):
        self.day_code = day_code
        self.weekday = _DAY_CODES_TO_WEEKDAY[day_code]
        self.modality_name = modality_name


class AttendanceService:
    _USERS = "users"
    _CLASSES = "classes"
    _MODALITIES = "modalities"
    _PRESENCAS = "presencas"

    @log
    def get_history(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None = None,
    ) -> AttendanceHistoryOut:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        db = firestore.client()

        # 1. Get user's enrolled classes
        user_doc = db.collection(self._USERS).document(target_uid).get()
        if not user_doc.exists:
            raise HTTPException(
                status_code=404, detail="Usuário não encontrado",
            )
        class_ids = user_doc.to_dict().get("classIds", [])

        # 2. Get class schedules with modality names
        schedule_items: list[_ScheduleItem] = []
        if class_ids:
            refs = [
                db.collection(self._CLASSES).document(cid)
                for cid in class_ids
            ]
            for doc in db.get_all(refs):
                if not doc.exists:
                    continue
                data = doc.to_dict()
                if not data.get("active", True):
                    continue

                modality_name = self._resolve_modality(db, data)

                schedule = data.get("schedule") or []
                if not schedule and data.get("weeklySchedule"):
                    ws = data["weeklySchedule"]
                    schedule = [
                        {"day": d} for d in ws.get("days", [])
                    ]
                for item in schedule:
                    day_code = item.get("day", "")
                    if day_code in _DAY_CODES_TO_WEEKDAY:
                        schedule_items.append(
                            _ScheduleItem(day_code, modality_name),
                        )

        # 3. Get all presencas for the user
        presencas_docs = list(
            db.collection(self._PRESENCAS)
            .where("projectId", "==", project_id)
            .where("userId", "==", target_uid)
            .stream()
        )

        # Index presencas by local date → list of doc data
        presencas_by_date: dict[date, list[dict]] = {}
        presenca_dates: set[date] = set()

        for doc in presencas_docs:
            data = doc.to_dict()
            data["_id"] = doc.id
            ts = data.get("timestamp", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
                local_date = dt.astimezone(_TZ_OFFSET).date()
            except (ValueError, TypeError):
                continue
            presencas_by_date.setdefault(local_date, []).append(data)
            presenca_dates.add(local_date)

        # 4. Build month summaries with individual records
        today = datetime.now(_TZ_OFFSET).date()
        months_range = self._last_n_months(today, _HISTORY_MONTHS)

        month_summaries: list[MonthSummary] = []
        total_attended = 0
        total_expected = 0

        for y, m in months_range:
            records, attended, expected = self._build_month_records(
                y, m, schedule_items, presencas_by_date, today,
            )
            pct = round(attended / expected * 100) if expected > 0 else 0
            total_attended += attended
            total_expected += expected

            month_summaries.append(MonthSummary(
                month=f"{y}-{m:02d}",
                month_label=f"{_MONTH_NAMES_PT[m]} / {y}",
                attended=attended,
                total=expected,
                percent=pct,
                records=records,
            ))

        # 5. Streak + overall average
        streak = self._calc_streak(presenca_dates, today)
        overall = (
            round(total_attended / total_expected * 100)
            if total_expected > 0 else 0
        )

        return AttendanceHistoryOut(
            streak_days=streak,
            overall_percent=overall,
            months=month_summaries,
        )

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_month_records(
        year: int,
        month: int,
        schedule_items: list[_ScheduleItem],
        presencas_by_date: dict[date, list[dict]],
        today: date,
    ) -> tuple[list[AttendanceRecord], int, int]:
        """Build individual attendance records for a month.

        Returns (records, attended_count, expected_count).
        Records are sorted newest-first.
        """
        if not schedule_items:
            return [], 0, 0

        days_in_month = cal.monthrange(year, month)[1]
        last_day = (
            min(days_in_month, today.day)
            if year == today.year and month == today.month
            else days_in_month
        )

        records: list[AttendanceRecord] = []
        attended = 0
        expected = 0

        for d in range(last_day, 0, -1):  # newest first
            current_date = date(year, month, d)
            weekday = current_date.weekday()

            for si in schedule_items:
                if si.weekday != weekday:
                    continue

                expected += 1
                day_presencas = presencas_by_date.get(current_date, [])
                date_label = (
                    f"{d:02d} de {_MONTH_NAMES_PT[month]}"
                )
                date_sort = f"{year}-{month:02d}-{d:02d}"

                # Check if there's a presenca for this day
                matched = None
                for p in day_presencas:
                    matched = p
                    break

                if matched:
                    status_raw = matched.get("status", "REGISTERED")
                    if status_raw == "JUSTIFIED":
                        status = "justified"
                        status_label = "Falta Justificada"
                        justification = matched.get("justification")
                    else:
                        status = "present"
                        status_label = "Presença"
                        justification = None
                        attended += 1
                    rec_id = matched.get("_id", date_sort)
                else:
                    status = "absent"
                    status_label = "Falta"
                    justification = None
                    rec_id = f"absent_{date_sort}"

                records.append(AttendanceRecord(
                    id=rec_id,
                    date=date_label,
                    date_sort=date_sort,
                    modality_name=si.modality_name,
                    status=status,
                    status_label=status_label,
                    justification=justification,
                ))

        return records, attended, expected

    @staticmethod
    def _last_n_months(
        today: date, n: int,
    ) -> list[tuple[int, int]]:
        result = []
        y, m = today.year, today.month
        for _ in range(n):
            result.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        return result

    @staticmethod
    def _calc_streak(
        presenca_dates: set[date], today: date,
    ) -> int:
        if not presenca_dates:
            return 0
        streak = 0
        check = today
        if check not in presenca_dates:
            check = today - timedelta(days=1)
        while check in presenca_dates:
            streak += 1
            check -= timedelta(days=1)
        return streak

    def _resolve_modality(self, db, class_data: dict) -> str:
        mid = class_data.get("modalityId", "")
        if mid:
            mod_doc = db.collection(self._MODALITIES).document(mid).get()
            if mod_doc.exists:
                return mod_doc.to_dict().get("name", "")
        return class_data.get("modality", "Treino")

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
