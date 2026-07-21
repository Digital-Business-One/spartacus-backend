"""Attendance history — monthly summaries with individual records."""

import calendar as cal
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from firebase_admin import firestore

from app.events.models import DomainEvent, ValidationPayload
from app.logging.decorator import log
from app.models.account import GraduationEntry
from app.models.attendance import (
    AnalyticsStudentOut,
    AttendanceActionOut,
    AttendanceAnalyticsOut,
    AttendanceCounts,
    AttendanceDashboardOut,
    AttendanceHistoryOut,
    AttendanceRecord,
    BeltBreakdown,
    ClassBriefOut,
    GraduationBreakdown,
    MonthSummary,
    PendingJustificationOut,
    StudentAttendanceCard,
)
from app.services.attendance_analytics import (
    NO_GRADUATION_KEY,
    aggregate_attendance,
    graduation_bucket,
)
from app.services.graduation_snapshot import (
    build_graduation_snapshot,
    resolve_modality_slug,
)

_TZ_OFFSET = timezone(timedelta(hours=-4))  # Brasnorte-MT = UTC-4

# Sentinel window fed to `aggregate_attendance`: the per-turma window is
# applied in the service (each record may belong to a different turma with
# its own start date), so records reaching the pure aggregation have already
# passed the window. This sentinel therefore always passes.
_OPEN_WINDOW = "0001-01-01T00:00:00-04:00"

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
    """A class schedule item with denormalized class metadata."""

    __slots__ = (
        "day_code", "weekday", "class_id", "class_name",
        "modality_name", "teacher_name",
    )

    def __init__(
        self,
        day_code: str,
        class_id: str,
        class_name: str,
        modality_name: str,
        teacher_name: str | None,
    ):
        self.day_code = day_code
        self.weekday = _DAY_CODES_TO_WEEKDAY[day_code]
        self.class_id = class_id
        self.class_name = class_name
        self.modality_name = modality_name
        self.teacher_name = teacher_name


class AttendanceService:
    _USERS = "users"
    _CLASSES = "classes"
    _MODALITIES = "modalities"
    _ATTENDANCE = "attendance"

    @log
    def get_history_admin(
        self,
        project_id: str,
        target_uid: str,
        year: int | None = None,
        months: list[int] | None = None,
        statuses: list[str] | None = None,
    ) -> AttendanceHistoryOut:
        """Staff-only history (no guardian check). RFC-12.

        Authorization is enforced upstream (owner/assistant only).
        """
        return self._compute_history(
            project_id=project_id,
            target_uid=target_uid,
            year=year,
            months=months,
            statuses=statuses,
        )

    @log
    def get_history(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None = None,
        filters: dict | None = None,
    ) -> AttendanceHistoryOut:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)
        return self._compute_history(
            project_id=project_id, target_uid=target_uid, filters=filters,
        )

    def _compute_history(
        self,
        project_id: str,
        target_uid: str,
        year: int | None = None,
        months: list[int] | None = None,
        statuses: list[str] | None = None,
        filters: dict | None = None,
    ) -> AttendanceHistoryOut:
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
        class_data_by_id: dict[str, dict] = {}
        if class_ids:
            refs = [
                db.collection(self._CLASSES).document(cid)
                for cid in class_ids
            ]
            for doc in db.get_all(refs):
                if not doc.exists:
                    continue
                data = doc.to_dict()
                # Include inactive classes in history — historical attendance
                # should remain visible even after a class is deactivated.
                class_data_by_id[doc.id] = data

                modality_name = self._resolve_modality(db, data)
                class_id = doc.id
                class_name = data.get("name", class_id)
                teacher_name = data.get("teacherName") or data.get("teacher")

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
                            _ScheduleItem(
                                day_code=day_code,
                                class_id=class_id,
                                class_name=class_name,
                                modality_name=modality_name,
                                teacher_name=teacher_name,
                            ),
                        )

        # 2b. Compute the months window early (needed for the month-summary
        # bucketing below) and the user's `createdAt`, which is the only
        # universally-safe Firestore query lower bound: every effective
        # per-turma counting window is `max(user.createdAt,
        # turma.attendanceStartDate)` (see `_turma_window`), which is
        # therefore always >= user.createdAt — regardless of which turmas
        # the user is CURRENTLY enrolled in. Bounding the query by
        # current-enrollment turma windows instead (as a previous version
        # of this code did) silently drops in-window records belonging to
        # turmas the user has since left, because `classIds` is mutable.
        today = datetime.now(_TZ_OFFSET).date()
        months_range = self._resolve_months_range(today, year, months)

        user_created_dt = self._parse_local_dt(
            user_doc.to_dict().get("createdAt"),
        )

        # 3. Get all attendance records for the user, windowed at the query
        # level by `user.createdAt` only (exact per-turma windowing is
        # still applied below / in `_compute_analytics`). If `createdAt`
        # is missing, fall back to an unbounded read — correct, just not
        # optimized.
        query = (
            db.collection(self._ATTENDANCE)
            .where("projectId", "==", project_id)
            .where("userId", "==", target_uid)
        )
        if user_created_dt is not None:
            query = query.where(
                "timestamp", ">=", user_created_dt.isoformat(),
            )
        attendance_docs = list(query.stream())

        # Index attendance records by local date → list of doc data
        attendance_by_date: dict[date, list[dict]] = {}
        attendance_dates: set[date] = set()

        for doc in attendance_docs:
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
            attendance_by_date.setdefault(local_date, []).append(data)
            attendance_dates.add(local_date)

        # 4. Build month summaries with individual records
        # (`today`/`months_range` computed earlier, in step 2b)
        month_summaries: list[MonthSummary] = []
        total_attended = 0
        total_expected = 0

        for y, m in months_range:
            records, attended, expected = self._build_month_records(
                y, m, schedule_items, attendance_by_date, today,
            )
            # Apply status filter if provided
            if statuses:
                records = [r for r in records if r.status in statuses]
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

        # 5. Resolve validator names in batch
        self._resolve_validator_names(db, month_summaries)

        # 6. Streak + overall average
        streak = self._calc_streak(attendance_dates, today)
        overall = (
            round(total_attended / total_expected * 100)
            if total_expected > 0 else 0
        )

        # 7. Frequência Analítica (Task A6): counts/percent/byGraduation over
        # the per-turma counting window, honouring month/modality/belt/degree.
        counts, percent, by_graduation = self._compute_analytics(
            db, user_doc.to_dict(), attendance_docs, filters,
        )

        return AttendanceHistoryOut(
            streak_days=streak,
            overall_percent=overall,
            months=month_summaries,
            counts=counts,
            percent=percent,
            by_graduation=by_graduation,
        )

    def _compute_analytics(
        self,
        db,
        user_data: dict,
        attendance_docs: list,
        filters: dict | None,
    ) -> tuple[AttendanceCounts, float | None, list[GraduationBreakdown]]:
        """Window per turma → pure aggregation → typed payload.

        For each attendance record we look up its turma (by `turmaId`):
        turmas with the engine off — or enabled without a start date (the
        inconsistency case) — are excluded entirely. The effective window is
        `max(user.createdAt, turma.attendanceStartDate)`; records before it
        are dropped. Turma docs are batch-fetched and cached by id so the
        same turma is never fetched twice.

        Timestamps are localized to Brasnorte (UTC-4) before aggregation so
        that the month filter and byGraduation buckets are consistent with
        the legacy monthly bucketing (which localizes the same way).
        """
        docs_data = [doc.to_dict() for doc in attendance_docs]

        user_created_dt = self._parse_local_dt(user_data.get("createdAt"))

        # Batch-fetch every referenced turma once, cache by id.
        turma_ids = {
            d.get("turmaId") for d in docs_data if d.get("turmaId")
        }
        class_cache: dict[str, dict | None] = {}
        if turma_ids:
            refs = [
                db.collection(self._CLASSES).document(tid)
                for tid in turma_ids
            ]
            for doc in db.get_all(refs):
                class_cache[doc.id] = doc.to_dict() if doc.exists else None

        # Effective window per turma (None = excluded: engine off / no date /
        # unknown turma).
        windows = {
            tid: self._turma_window(class_cache.get(tid), user_created_dt)
            for tid in turma_ids
        }

        records: list[dict] = []
        for d in docs_data:
            window = windows.get(d.get("turmaId"))
            if window is None:
                continue
            record_dt = self._parse_local_dt(d.get("timestamp"))
            if record_dt is None or record_dt < window:
                continue
            records.append({
                "status": d.get("status"),
                "timestamp": record_dt.isoformat(),
                "modalitySlug": d.get("modalitySlug"),
                "graduationSnapshot": d.get("graduationSnapshot"),
            })

        agg = aggregate_attendance(records, _OPEN_WINDOW, filters)

        counts = AttendanceCounts(**agg["counts"])
        by_graduation = [
            GraduationBreakdown(
                key=g["key"],
                belt=g["belt"],
                degree=g["degree"],
                pending=g["pending"],
                counts=AttendanceCounts(**g["counts"]),
                percent=g["percent"],
            )
            for g in agg["byGraduation"]
        ]
        return counts, agg["percent"], by_graduation

    @staticmethod
    def _parse_local_dt(value) -> datetime | None:
        """Parse an ISO timestamp and localize to Brasnorte (UTC-4).

        Naive timestamps are assumed to already be local. Returns None for
        empty/invalid values.
        """
        if not value or not isinstance(value, str):
            return None
        try:
            dt = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ_OFFSET)
        return dt.astimezone(_TZ_OFFSET)

    @staticmethod
    def _turma_window(
        class_data: dict | None, user_created_dt: datetime | None,
    ) -> datetime | None:
        """Effective counting window for one turma, or None if excluded.

        None means the record's turma does not count: it doesn't exist, its
        engine is off, or it's enabled without a start date (inconsistency).
        Otherwise the window is `max(user.createdAt, attendanceStartDate)`.
        """
        if not class_data or not class_data.get("attendanceEngineEnabled"):
            return None
        start_date = class_data.get("attendanceStartDate")
        if not start_date:
            return None
        try:
            start_date_obj = date.fromisoformat(start_date)
            start_dt = datetime(
                start_date_obj.year,
                start_date_obj.month,
                start_date_obj.day,
                tzinfo=_TZ_OFFSET,
            )
        except (ValueError, TypeError):
            return None
        if user_created_dt is None:
            return start_dt
        return max(user_created_dt, start_dt)

    def _resolve_validator_names(
        self, db, month_summaries: list[MonthSummary],
    ) -> None:
        """Batch-resolve validator UIDs → names. Mutates records in-place."""
        validator_uids: set[str] = set()
        for ms in month_summaries:
            for rec in ms.records:
                if rec.validated_by and rec.validated_by != "system":
                    validator_uids.add(rec.validated_by)

        if not validator_uids:
            # Mark "system" entries with explicit label
            for ms in month_summaries:
                for rec in ms.records:
                    if rec.validated_by == "system":
                        rec.validated_by_name = "Sistema"
            return

        refs = [
            db.collection(self._USERS).document(uid)
            for uid in validator_uids
        ]
        names: dict[str, str] = {}
        for doc in db.get_all(refs):
            if doc.exists:
                names[doc.id] = doc.to_dict().get("name", "")

        for ms in month_summaries:
            for rec in ms.records:
                if rec.validated_by == "system":
                    rec.validated_by_name = "Sistema"
                elif rec.validated_by:
                    rec.validated_by_name = names.get(rec.validated_by)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_month_records(
        year: int,
        month: int,
        schedule_items: list[_ScheduleItem],
        attendance_by_date: dict[date, list[dict]],
        today: date,
    ) -> tuple[list[AttendanceRecord], int, int]:
        """Build individual attendance records for a month.

        Returns (records, attended_count, expected_count).
        Records are sorted newest-first.
        """
        if not schedule_items:
            # No schedule = show only actual attendance docs (orphaned records)
            orphaned: list[AttendanceRecord] = []
            orphaned_attended = 0
            for dt, recs in sorted(
                attendance_by_date.items(), reverse=True
            ):
                if dt.year != year or dt.month != month:
                    continue
                for r in recs:
                    d = dt.day
                    s_raw = r.get("status", "registered")
                    if s_raw in ("confirmed", "registered"):
                        orphaned_attended += 1
                    orphaned.append(
                        AttendanceRecord(
                            id=r.get("_id", f"{year}-{month:02d}-{d:02d}"),
                            date=f"{d:02d} de {_MONTH_NAMES_PT[month]}",
                            date_sort=f"{year}-{month:02d}-{d:02d}",
                            time=AttendanceService._extract_time(
                                r.get("timestamp", "")
                            ),
                            class_id=r.get("turmaId", ""),
                            class_name=r.get("turmaName", ""),
                            modality_name="",
                            teacher_name=r.get("teacherName"),
                            status=s_raw,
                            status_label={
                                "confirmed": "Confirmado",
                                "registered": "Aguardando confirmação",
                                "absent": "Não confirmado",
                                "absent_justified": "Falta Justificada",
                                "absent_justification_pending": (
                                    "Justificativa em análise"
                                ),
                                "rejected": "Rejeitado",
                            }.get(s_raw, s_raw),
                            validated_by=r.get("validatedBy"),
                            validated_at=r.get("validatedAt"),
                        )
                    )
            return orphaned, orphaned_attended, max(len(orphaned), 0)

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
                day_records = attendance_by_date.get(current_date, [])
                date_label = (
                    f"{d:02d} de {_MONTH_NAMES_PT[month]}"
                )
                date_sort = f"{year}-{month:02d}-{d:02d}"

                # Check if there's an attendance record for this day
                matched = None
                for p in day_records:
                    matched = p
                    break

                if matched:
                    status_raw = matched.get("status", "registered")
                    if status_raw == "absent_justified":
                        status = "absent_justified"
                        status_label = "Falta Justificada"
                        justification = matched.get("justification")
                    elif status_raw == "absent_justification_pending":
                        status = "absent_justification_pending"
                        status_label = "Justificativa em análise"
                        justification = None
                    elif status_raw == "absent":
                        status = "absent"
                        status_label = "Não confirmado"
                        justification = None
                    elif status_raw == "confirmed":
                        status = "confirmed"
                        status_label = "Confirmado"
                        justification = None
                        attended += 1
                    else:  # "registered" (check-in done, not yet validated)
                        status = "registered"
                        status_label = "Aguardando confirmação"
                        justification = None
                        attended += 1
                    rec_id = matched.get("_id", date_sort)
                    rec_time = AttendanceService._extract_time(
                        matched.get("timestamp", "")
                    )
                    class_id = matched.get("turmaId", "")
                    class_name = matched.get("turmaName", "") or si.class_name
                    teacher_name = matched.get("teacherName")
                    validated_by = matched.get("validatedBy")
                    validated_at = matched.get("validatedAt")
                else:
                    status = "absent"
                    status_label = "Não confirmado"
                    justification = None
                    rec_id = f"absent_{date_sort}"
                    rec_time = None
                    class_id = si.class_id
                    class_name = si.class_name
                    teacher_name = si.teacher_name
                    validated_by = None
                    validated_at = None

                records.append(AttendanceRecord(
                    id=rec_id,
                    date=date_label,
                    date_sort=date_sort,
                    time=rec_time,
                    class_id=class_id,
                    class_name=class_name,
                    modality_name=si.modality_name,
                    teacher_name=teacher_name,
                    status=status,
                    status_label=status_label,
                    validated_by=validated_by,
                    validated_by_name=None,  # resolved in batch below
                    validated_at=validated_at,
                    justification=justification,
                ))

        return records, attended, expected

    @staticmethod
    def _extract_time(timestamp_iso: str) -> str | None:
        """Extract HH:MM from an ISO timestamp in local TZ."""
        if not timestamp_iso:
            return None
        try:
            dt = datetime.fromisoformat(timestamp_iso)
            return dt.astimezone(_TZ_OFFSET).strftime("%H:%M")
        except (ValueError, TypeError):
            return None

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
    def _resolve_months_range(
        today: date,
        year: int | None,
        months: list[int] | None,
    ) -> list[tuple[int, int]]:
        """Compute (year, month) tuples to iterate.

        - If year+months provided: those exact months
        - If year only: all 12 months of the year
        - Otherwise: last N months ending at today
        """
        if year is not None and months:
            return sorted(((year, m) for m in months), reverse=True)
        if year is not None:
            return [(year, m) for m in range(12, 0, -1)]
        return AttendanceService._last_n_months(today, _HISTORY_MONTHS)

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

    # ── Frequência Analítica (Task A7): analytics agregado por turma ──────

    @log
    def get_analytics(
        self,
        project_id: str,
        class_id: str,
        month: str | None = None,
    ) -> AttendanceAnalyticsOut:
        """Staff-only aggregated view of one turma.

        Reuses A6's per-student counting window (`_turma_window` +
        `_parse_local_dt`: `max(user.createdAt, turma.attendanceStartDate)`,
        engine-off/no-start-date turmas rejected upfront with 409) and the
        pure `aggregate_attendance` engine — called once per student (for
        `students[]`) and once pooled over every window-filtered record
        (for `totals`). `byBelt` buckets students by their CURRENT
        graduation for the turma's modality (via `build_graduation_snapshot`
        called fresh, and the same `graduation_bucket` classification A6
        uses for historical snapshots), not the historical per-record one.

        The student universe for `students[]`/`byBelt`/`totals` is the
        turma ROSTER (`users` where `classIds` array-contains `class_id` —
        same query `list_today_for_class` uses for the dashboard), not
        "whoever has a countable record". Every enrolled student appears,
        even with all-zero counts and `percent=None`; a student who has
        historical attendance records for this turma but is no longer on
        the roster (e.g. removed from the turma later) is excluded from
        `students`/`byBelt`/`totals` — they're not part of the turma
        anymore.
        """
        db = firestore.client()

        class_doc = db.collection(self._CLASSES).document(class_id).get()
        if (
            not class_doc.exists
            or class_doc.to_dict().get("projectId") != project_id
        ):
            raise HTTPException(
                status_code=404, detail="Turma não encontrada",
            )
        class_data = class_doc.to_dict()

        if not class_data.get("attendanceEngineEnabled"):
            raise HTTPException(
                status_code=409,
                detail="Motor de frequência desativado para esta turma",
            )
        if not class_data.get("attendanceStartDate"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Turma com motor de frequência ligado mas sem "
                    "data-base configurada"
                ),
            )

        effective_month = month or datetime.now(_TZ_OFFSET).strftime(
            "%Y-%m",
        )
        filters = {"month": effective_month}

        attendance_docs = list(
            db.collection(self._ATTENDANCE)
            .where("projectId", "==", project_id)
            .where("turmaId", "==", class_id)
            .stream()
        )
        docs_data = [doc.to_dict() for doc in attendance_docs]

        # Turma roster — same query `list_today_for_class` uses for the
        # dashboard. This is the student universe for students[]/byBelt/
        # totals, not just whoever has a record: non-roster uids (e.g. a
        # student later removed from the turma) are never looked at below.
        roster_docs = list(
            db.collection(self._USERS)
            .where("classIds", "array_contains", class_id)
            .stream()
        )
        roster_uids = [doc.id for doc in roster_docs]
        roster_set = set(roster_uids)
        user_cache: dict[str, dict | None] = {
            doc.id: doc.to_dict() for doc in roster_docs
        }

        windows = {
            uid: self._turma_window(
                class_data,
                self._parse_local_dt(
                    (user_cache.get(uid) or {}).get("createdAt"),
                ),
            )
            for uid in roster_uids
        }

        modality_slug = resolve_modality_slug(db, class_data)

        records_by_user: dict[str, list[dict]] = {}
        for d in docs_data:
            uid = d.get("userId")
            if uid not in roster_set:
                continue
            window = windows.get(uid)
            if window is None:
                continue
            record_dt = self._parse_local_dt(d.get("timestamp"))
            if record_dt is None or record_dt < window:
                continue
            records_by_user.setdefault(uid, []).append({
                "status": d.get("status"),
                "timestamp": record_dt.isoformat(),
                "modalitySlug": d.get("modalitySlug"),
                "graduationSnapshot": d.get("graduationSnapshot"),
            })

        # Task B5 — staff review queue: turma records currently
        # `absent_justification_pending`, windowed + month-filtered exactly
        # like the counts above (same roster/window; month compared the
        # same way `_matches_filters` does for the aggregation engine).
        pending_justifications: list[PendingJustificationOut] = []
        for doc in attendance_docs:
            d = doc.to_dict()
            uid = d.get("userId")
            if uid not in roster_set:
                continue
            if d.get("status") != "absent_justification_pending":
                continue
            window = windows.get(uid)
            if window is None:
                continue
            record_dt = self._parse_local_dt(d.get("timestamp"))
            if record_dt is None or record_dt < window:
                continue
            if f"{record_dt.year:04d}-{record_dt.month:02d}" != effective_month:
                continue
            justification = d.get("justification") or {}
            student_data = user_cache.get(uid) or {}
            pending_justifications.append(PendingJustificationOut(
                attendance_id=doc.id,
                user_id=uid,
                student_name=student_data.get("name", ""),
                aula_date=record_dt.isoformat(),
                type_id=justification.get("typeId", ""),
                type_name=justification.get("typeName", ""),
                text=justification.get("text", ""),
                attachment=justification.get("attachment"),
            ))
        pending_justifications.sort(key=lambda pj: pj.aula_date)

        students: list[AnalyticsStudentOut] = []
        belt_groups: dict[str, dict] = {}

        for uid in roster_uids:
            recs = records_by_user.get(uid, [])
            agg = aggregate_attendance(recs, _OPEN_WINDOW, filters)
            counts_dict = agg["counts"]

            counts = AttendanceCounts(**counts_dict)
            percent = agg["percent"]
            user_data = user_cache.get(uid) or {}

            current_grad = build_graduation_snapshot(
                user_data, modality_slug,
            )
            graduation_entry = (
                GraduationEntry(
                    belt=current_grad.get("belt", ""),
                    degree=current_grad.get("degree", 0),
                    status=current_grad.get("status", "approved"),
                )
                if current_grad else None
            )

            key, belt, degree, pending = graduation_bucket(
                {"graduationSnapshot": current_grad},
            )
            group = belt_groups.setdefault(key, {
                "key": key, "belt": belt, "degree": degree,
                "pending": pending, "studentCount": 0, "percents": [],
            })
            group["studentCount"] += 1
            if pending:
                group["pending"] = True
            if percent is not None:
                group["percents"].append(percent)

            students.append(AnalyticsStudentOut(
                user_id=uid,
                name=user_data.get("name", ""),
                photo_url=user_data.get("photoUrl"),
                graduation=graduation_entry,
                counts=counts,
                percent=percent,
                has_pending_justification=counts.justification_pending > 0,
            ))

        students.sort(
            key=lambda s: (
                s.percent is None,
                s.percent if s.percent is not None else 0.0,
            ),
        )

        def _sort_key(g: dict) -> tuple:
            if g["key"] == NO_GRADUATION_KEY:
                return (1, "", "")
            return (0, str(g["belt"]), str(g["degree"]))

        by_belt = [
            BeltBreakdown(
                key=g["key"], belt=g["belt"], degree=g["degree"],
                pending=g["pending"], student_count=g["studentCount"],
                average_percent=(
                    sum(g["percents"]) / len(g["percents"])
                    if g["percents"] else None
                ),
            )
            for g in sorted(belt_groups.values(), key=_sort_key)
        ]

        all_window_records = [
            r for recs in records_by_user.values() for r in recs
        ]
        totals_agg = aggregate_attendance(
            all_window_records, _OPEN_WINDOW, filters,
        )
        totals = AttendanceCounts(**totals_agg["counts"])
        all_percents = [s.percent for s in students if s.percent is not None]
        average_percent = (
            sum(all_percents) / len(all_percents) if all_percents else None
        )

        return AttendanceAnalyticsOut(
            average_percent=average_percent,
            totals=totals,
            by_belt=by_belt,
            students=students,
            pending_justifications=pending_justifications,
        )

    # ── RFC-14: Dashboard de frequência ──────────────────────────────────

    @log
    def list_today_for_class(
        self, project_id: str, class_id: str,
    ) -> AttendanceDashboardOut:
        """Aggregate today's roster + attendance docs for a class.

        Returns students enrolled in the class with their current attendance
        status for today's scheduled aula. Students without any attendance
        doc appear as `status="absent"`.
        """
        db = firestore.client()
        today = datetime.now(_TZ_OFFSET).date()
        iso_date = today.isoformat()

        class_doc = db.collection(self._CLASSES).document(class_id).get()
        if (
            not class_doc.exists
            or class_doc.to_dict().get("projectId") != project_id
        ):
            raise HTTPException(
                status_code=404, detail="Turma não encontrada",
            )
        class_data = class_doc.to_dict()

        # Resolve today's scheduled slot, if any
        schedule_items = class_data.get("schedule") or []
        today_code = {
            0: "mon", 1: "tue", 2: "wed", 3: "thu",
            4: "fri", 5: "sat", 6: "sun",
        }[today.weekday()]
        today_slot: dict | None = None
        for s in schedule_items:
            if isinstance(s, dict) and s.get("day") == today_code:
                today_slot = s
                break

        aula_id: str | None = None
        lookup_aula_ids: list[str] = []
        ymd = today.strftime("%Y%m%d")
        if today_slot:
            start = today_slot.get("startTime", "")
            hhmm = start.replace(":", "")
            aula_id = f"{class_id}_{ymd}_{hhmm}"
            lookup_aula_ids.append(aula_id)
        # Also consider retroactive records for this class + day, so that
        # on off-schedule days the dashboard still surfaces the entries
        # created via force-confirm. aula_id stays null in the response to
        # signal "off-schedule" to the UI.
        lookup_aula_ids.append(f"{class_id}_{ymd}_retroactive")

        # Modality name
        modality_id = class_data.get("modalityId", "")
        modality_name = class_data.get("modality", "")
        if modality_id:
            mod_doc = (
                db.collection(self._MODALITIES).document(modality_id).get()
            )
            if mod_doc.exists:
                modality_name = mod_doc.to_dict().get("name", modality_name)

        class_brief = ClassBriefOut(
            id=class_id,
            name=class_data.get("name", ""),
            modality_id=modality_id,
            modality_name=modality_name,
            teacher_name=class_data.get("teacherName"),
            start_time=today_slot.get("startTime") if today_slot else None,
            end_time=today_slot.get("endTime") if today_slot else None,
            total_slots=int(class_data.get("totalSlots", 0) or 0),
            enrolled_count=0,  # filled below
        )

        # Enrolled users: query users where classIds contains class_id.
        # users collection is GLOBAL (no projectId field) — scoping is
        # implicit via class_id which already contains the project prefix.
        user_docs = list(
            db.collection(self._USERS)
            .where("classIds", "array_contains", class_id)
            .stream()
        )

        # Today's attendance docs (scheduled + retroactive aulas)
        att_by_user: dict[str, dict] = {}
        for lookup_id in lookup_aula_ids:
            att_query = (
                db.collection(self._ATTENDANCE)
                .where("projectId", "==", project_id)
                .where("aulaId", "==", lookup_id)
            )
            for doc in att_query.stream():
                data = doc.to_dict()
                att_by_user[data.get("userId", "")] = {
                    "id": doc.id,
                    **data,
                }

        students: list[StudentAttendanceCard] = []
        for u in user_docs:
            ud = u.to_dict()
            uid = u.id
            att = att_by_user.get(uid)
            status = "absent"
            attendance_id: str | None = None
            source: str | None = None
            registered_at: str | None = None
            confirmed_at: str | None = None
            if att:
                raw_status = att.get("status", "")
                if raw_status == "confirmed":
                    status = "confirmed"
                elif raw_status == "registered":
                    status = "registered"
                elif raw_status == "rejected":
                    # Rejected → appears as absent in UI
                    status = "absent"
                attendance_id = att.get("id")
                source = att.get("source")
                registered_at = att.get("timestamp")
                confirmed_at = att.get("validatedAt")

            age = _calc_age(ud.get("birthDate"))
            graduation_raw = ud.get("graduation") or {}
            graduation = {
                k: GraduationEntry(
                    belt=v.get("belt", ""),
                    degree=v.get("degree", 0),
                    prajied=v.get("prajied"),
                )
                for k, v in graduation_raw.items()
                if isinstance(v, dict)
            } or None
            students.append(
                StudentAttendanceCard(
                    user_id=uid,
                    name=ud.get("name", ""),
                    nickname=ud.get("nickname"),
                    initials=_initials(ud.get("name", "")),
                    age=age,
                    age_category=ud.get("ageCategory"),
                    photo_url=ud.get("photoUrl"),
                    roles=ud.get("roles", []) or [],
                    is_dependent=bool(ud.get("guardianUid")),
                    guardian_uid=ud.get("guardianUid"),
                    guardian_name=None,
                    graduation=graduation,
                    status=status,
                    attendance_id=attendance_id,
                    source=source,
                    registered_at=registered_at,
                    confirmed_at=confirmed_at,
                )
            )

        # Resolve guardian names for dependents (batch fetch)
        guardian_uids = {s.guardian_uid for s in students if s.guardian_uid}
        if guardian_uids:
            guardian_refs = [
                db.collection(self._USERS).document(gid)
                for gid in guardian_uids
            ]
            guardian_docs = db.get_all(guardian_refs)
            guardian_names = {
                doc.id: doc.to_dict().get("name")
                for doc in guardian_docs
                if doc.exists
            }
            for s in students:
                if s.guardian_uid and s.guardian_uid in guardian_names:
                    s.guardian_name = guardian_names[s.guardian_uid]

        class_brief.enrolled_count = len(students)
        students.sort(key=lambda s: s.name.lower())

        return AttendanceDashboardOut(
            class_info=class_brief,
            aula_id=aula_id,
            date=iso_date,
            students=students,
        )

    @log
    def confirm_attendance(
        self,
        project_id: str,
        class_id: str,
        user_id: str,
        actor_uid: str,
        source: str = "manual",
        aula_id: str | None = None,
        force: bool = False,
    ) -> tuple[AttendanceActionOut, DomainEvent | None]:
        """Mark attendance as confirmed for today. Creates doc if missing."""
        db = firestore.client()
        now = datetime.now(_TZ_OFFSET)
        now_iso = now.isoformat()

        resolved_aula_id = aula_id or self._resolve_today_aula_id(
            db, project_id, class_id, now.date(),
        )
        if not resolved_aula_id:
            if not force:
                raise HTTPException(
                    status_code=400,
                    detail="Sem aula agendada para hoje nessa turma",
                )
            # Retroactive: synthesize a deterministic aula id so multiple
            # retroactive entries for the same class on the same day share
            # the aula. Source is tagged so audits can spot these later.
            resolved_aula_id = (
                f"{class_id}_{now.strftime('%Y%m%d')}_retroactive"
            )
            source = "retroactive"

        self._ensure_aula(
            db, project_id, class_id, resolved_aula_id, now,
        )

        # Resolve names for event payload
        user_doc = db.collection(self._USERS).document(user_id).get()
        user_data = user_doc.to_dict() if user_doc.exists else None
        user_name = (user_data or {}).get("name", "")
        class_doc = db.collection(self._CLASSES).document(class_id).get()
        class_data = class_doc.to_dict() if class_doc.exists else None
        class_name = (class_data or {}).get("name", "")

        # Graduation snapshot — only used when a *new* attendance doc is
        # created below; never applied to an update of an existing record.
        modality_slug = resolve_modality_slug(db, class_data)
        graduation_snapshot = build_graduation_snapshot(
            user_data, modality_slug,
        )

        # Find existing attendance doc for this user/aula
        existing = list(
            db.collection(self._ATTENDANCE)
            .where("projectId", "==", project_id)
            .where("aulaId", "==", resolved_aula_id)
            .where("userId", "==", user_id)
            .limit(1)
            .stream()
        )

        att_id: str
        if existing:
            cur = existing[0].to_dict()
            # Record which column the card came from, so undo can return it
            # there. A re-confirm keeps the originally captured previous.
            prev_status = (
                cur.get("status")
                if cur.get("status") != "confirmed"
                else cur.get("previousStatus", "registered")
            )
            ref = existing[0].reference
            ref.update({
                "status": "confirmed",
                "previousStatus": prev_status,
                "validatedBy": actor_uid,
                "validatedAt": now_iso,
                "source": source,
            })
            att_id = ref.id
        else:
            # Staff registered straight from the "Ausente" column — the
            # previous column is column 1 (absent).
            _, ref = db.collection(self._ATTENDANCE).add({
                "projectId": project_id,
                "userId": user_id,
                "userName": user_name,
                "aulaId": resolved_aula_id,
                "turmaId": class_id,
                "turmaName": class_name,
                "modalitySlug": modality_slug,
                "graduationSnapshot": graduation_snapshot,
                "timestamp": now_iso,
                "status": "confirmed",
                "previousStatus": "absent",
                "validatedBy": actor_uid,
                "validatedAt": now_iso,
                "source": source,
            })
            att_id = ref.id

        event = DomainEvent(
            id="checkin.confirmed",
            payload=ValidationPayload(
                entity_id=att_id,
                target_uid=user_id,
                target_name=user_name,
                validated_by=actor_uid,
                validated_at=now_iso,
                turma_name=class_name,
            ),
        )

        return (
            AttendanceActionOut(status="confirmed", attendance_id=att_id),
            event,
        )

    @log
    def reject_attendance(
        self,
        project_id: str,
        class_id: str,
        user_id: str,
        actor_uid: str,
        reason: str | None = None,
        aula_id: str | None = None,
        force: bool = False,
    ) -> tuple[AttendanceActionOut, DomainEvent | None]:
        """Reject a registered check-in. Card returns to 'Ausente' column."""
        db = firestore.client()
        now = datetime.now(_TZ_OFFSET)
        now_iso = now.isoformat()

        resolved_aula_id = aula_id or self._resolve_today_aula_id(
            db, project_id, class_id, now.date(),
        )
        if not resolved_aula_id:
            if not force:
                raise HTTPException(
                    status_code=400,
                    detail="Sem aula agendada para hoje nessa turma",
                )
            resolved_aula_id = (
                f"{class_id}_{now.strftime('%Y%m%d')}_retroactive"
            )

        existing = list(
            db.collection(self._ATTENDANCE)
            .where("projectId", "==", project_id)
            .where("aulaId", "==", resolved_aula_id)
            .where("userId", "==", user_id)
            .limit(1)
            .stream()
        )

        if not existing:
            raise HTTPException(
                status_code=404,
                detail="Registro de frequência não encontrado",
            )

        ref = existing[0].reference
        att_data = existing[0].to_dict()
        ref.update({
            "status": "absent",
            "validatedBy": actor_uid,
            "validatedAt": now_iso,
            "rejectionReason": reason or "",
        })

        user_name = att_data.get("userName", "")
        class_name = att_data.get("turmaName", "")

        event = DomainEvent(
            id="checkin.absent",
            payload=ValidationPayload(
                entity_id=ref.id,
                target_uid=user_id,
                target_name=user_name,
                validated_by=actor_uid,
                validated_at=now_iso,
                turma_name=class_name,
            ),
        )

        return (
            AttendanceActionOut(status="rejected", attendance_id=ref.id),
            event,
        )

    def _resolve_today_aula_id(
        self, db, project_id: str, class_id: str, today: date,
    ) -> str | None:
        class_doc = db.collection(self._CLASSES).document(class_id).get()
        if (
            not class_doc.exists
            or class_doc.to_dict().get("projectId") != project_id
        ):
            return None
        schedule = class_doc.to_dict().get("schedule") or []
        today_code = {
            0: "mon", 1: "tue", 2: "wed", 3: "thu",
            4: "fri", 5: "sat", 6: "sun",
        }[today.weekday()]
        for s in schedule:
            if isinstance(s, dict) and s.get("day") == today_code:
                start = s.get("startTime", "").replace(":", "")
                return f"{class_id}_{today.strftime('%Y%m%d')}_{start}"
        return None

    def _ensure_aula(
        self, db, project_id: str, class_id: str,
        aula_id: str, now: datetime,
    ) -> None:
        ref = db.collection("aulas").document(aula_id)
        if ref.get().exists:
            return
        ref.set({
            "projectId": project_id,
            "turmaId": class_id,
            "startTime": now.isoformat(),
            "endTime": now.isoformat(),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })


def _initials(name: str) -> str:
    parts = [p for p in (name or "").strip().split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _calc_age(birth_date: str | None) -> int | None:
    if not birth_date:
        return None
    try:
        parts = birth_date.split("/")
        if len(parts) != 3:
            return None
        d, m, y = (int(p) for p in parts)
        today = datetime.now(_TZ_OFFSET).date()
        age = today.year - y
        if (today.month, today.day) < (m, d):
            age -= 1
        return age
    except (ValueError, TypeError):
        return None
