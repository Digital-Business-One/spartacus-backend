"""TDD tests for Task A4 — graduation snapshot + modalitySlug on attendance
records at creation time (3 write paths: checkin, staff confirm, absence job).

See: docs/superpowers/specs/2026-07-18-frequencia-analitica-design.md
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

from app.services.absence_job_service import AbsenceJobService
from app.services.attendance_service import AttendanceService
from app.services.graduation_snapshot import (
    build_graduation_snapshot,
    resolve_modality_slug,
)

client = TestClient(app, raise_server_exceptions=False)

_PROJECT_ID = "spartacus"
_USER_ID = "user123"
_VERIFY = "app.security.middleware.verify_id_token"
_FS_CHECKIN = "app.services.checkin_service.firestore"

# Relógio injetado no job de faltas: 16/07/2026 é quinta, depois da aula das
# 19h da agenda usada nas fixtures.
_JOB_NOW = datetime(2026, 7, 16, 23, 59, tzinfo=timezone(timedelta(hours=-4)))

_HEADERS = {
    "Authorization": "Bearer tok",
    "X-Project-Id": _PROJECT_ID,
}

_CLAIMS = {
    "uid": _USER_ID,
    "email": "test@test.com",
    "projects": {_PROJECT_ID: ["student"]},
}


# ── Pure helper unit tests ─────────────────────────────────────────────────


class TestBuildGraduationSnapshot:
    def test_user_with_graduation_for_modality_returns_snapshot(self):
        user_data = {
            "graduation": {
                "jiu-jitsu": {"belt": "azul", "degree": 2, "status": "approved"},
            },
        }
        snap = build_graduation_snapshot(user_data, "jiu-jitsu")
        assert snap == {"belt": "azul", "degree": 2, "status": "approved"}

    def test_user_without_graduation_map_returns_none(self):
        assert build_graduation_snapshot({}, "jiu-jitsu") is None

    def test_user_without_entry_for_modality_returns_none(self):
        user_data = {
            "graduation": {
                "capoeira": {"belt": "amarela", "degree": 0, "status": "approved"},
            },
        }
        assert build_graduation_snapshot(user_data, "jiu-jitsu") is None

    def test_none_modality_slug_returns_none(self):
        user_data = {
            "graduation": {
                "jiu-jitsu": {"belt": "azul", "degree": 2, "status": "approved"},
            },
        }
        assert build_graduation_snapshot(user_data, None) is None

    def test_none_user_data_returns_none(self):
        assert build_graduation_snapshot(None, "jiu-jitsu") is None

    def test_legacy_key_normalized_to_slug(self):
        # graduation map keyed by legacy modality name, not slug
        user_data = {
            "graduation": {
                "Jiu-Jitsu": {"belt": "azul", "degree": 2, "status": "approved"},
            },
        }
        snap = build_graduation_snapshot(user_data, "jiu-jitsu")
        assert snap == {"belt": "azul", "degree": 2, "status": "approved"}

    def test_pending_status_preserved(self):
        user_data = {
            "graduation": {
                "jiu-jitsu": {"belt": "roxa", "degree": 0, "status": "pending"},
            },
        }
        snap = build_graduation_snapshot(user_data, "jiu-jitsu")
        assert snap["status"] == "pending"


class TestResolveModalitySlug:
    def test_resolves_slug_from_modality_doc(self):
        mod_doc = MagicMock()
        mod_doc.exists = True
        mod_doc.to_dict.return_value = {"slug": "jiu-jitsu", "name": "Jiu-Jitsu"}

        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value = mod_doc

        slug = resolve_modality_slug(db, {"modalityId": "spartacus_jiu-jitsu"})
        assert slug == "jiu-jitsu"

    def test_no_modality_id_returns_none(self):
        db = MagicMock()
        assert resolve_modality_slug(db, {}) is None
        assert resolve_modality_slug(db, None) is None

    def test_modality_doc_missing_returns_none(self):
        mod_doc = MagicMock()
        mod_doc.exists = False

        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value = mod_doc

        assert resolve_modality_slug(db, {"modalityId": "ghost"}) is None


# ── Check-in write path ─────────────────────────────────────────────────────


def _user_doc(graduation=None):
    d = MagicMock()
    d.exists = True
    d.to_dict.return_value = {"name": "Aluno X", "graduation": graduation or {}}
    return d


def _class_doc(modality_id="spartacus_jiu-jitsu"):
    d = MagicMock()
    d.exists = True
    d.to_dict.return_value = {
        "name": "Turma A",
        "modalityId": modality_id,
        "teacherName": "Prof X",
        # Attendance engine (Task A5 gate): on with a start date well
        # before every fixture date used in this file's tests.
        "attendanceEngineEnabled": True,
        "attendanceStartDate": "2020-01-01",
    }
    return d


def _modality_doc(slug="jiu-jitsu", name="Jiu-Jitsu"):
    d = MagicMock()
    d.exists = True
    d.to_dict.return_value = {"slug": slug, "name": name}
    return d


def _aula_doc(turma_id="t1"):
    d = MagicMock()
    d.exists = True
    d.to_dict.return_value = {"turmaId": turma_id, "endTime": ""}
    return d


class TestCheckinWritesSnapshot:
    def _run_checkin(self, graduation):
        mock_db = MagicMock()

        users_col = MagicMock()
        users_col.document.return_value.get.return_value = _user_doc(graduation)

        classes_col = MagicMock()
        classes_col.document.return_value.get.return_value = _class_doc()

        modalities_col = MagicMock()
        modalities_col.document.return_value.get.return_value = _modality_doc()

        aulas_col = MagicMock()
        aulas_col.document.return_value.get.return_value = _aula_doc()

        attendance_col = MagicMock()
        q = attendance_col.where.return_value
        q = q.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = []
        mock_ref = MagicMock()
        mock_ref.id = "attendance_123"
        attendance_col.add.return_value = (None, mock_ref)

        def col_side(name):
            return {
                "users": users_col,
                "classes": classes_col,
                "modalities": modalities_col,
                "aulas": aulas_col,
                "attendance": attendance_col,
            }.get(name, MagicMock())

        mock_db.collection.side_effect = col_side

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS_CHECKIN) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.post(
                "/checkin",
                headers=_HEADERS,
                json={"aulaId": "t1_20260716_1900"},
            )

        assert r.status_code == 201
        assert attendance_col.add.call_count == 1
        (written,), _ = attendance_col.add.call_args
        return written

    def test_checkin_writes_modality_slug_and_snapshot(self):
        written = self._run_checkin(
            {"jiu-jitsu": {"belt": "azul", "degree": 1, "status": "approved"}},
        )
        assert written["modalitySlug"] == "jiu-jitsu"
        assert written["graduationSnapshot"] == {
            "belt": "azul", "degree": 1, "status": "approved",
        }

    def test_checkin_user_without_graduation_writes_null_snapshot(self):
        written = self._run_checkin({})
        assert written["modalitySlug"] == "jiu-jitsu"
        assert written["graduationSnapshot"] is None


# ── Staff confirm write path (create branch only) ──────────────────────────


class TestConfirmAttendanceWritesSnapshotOnCreate:
    def _mock_db(self, graduation=None, existing_attendance=None):
        mock_db = MagicMock()

        users_col = MagicMock()
        users_col.document.return_value.get.return_value = _user_doc(graduation)

        classes_col = MagicMock()
        classes_col.document.return_value.get.return_value = _class_doc()

        modalities_col = MagicMock()
        modalities_col.document.return_value.get.return_value = _modality_doc()

        aulas_col = MagicMock()
        aula_ref = MagicMock()
        existing_aula = MagicMock()
        existing_aula.exists = True
        aula_ref.get.return_value = existing_aula
        aulas_col.document.return_value = aula_ref

        attendance_col = MagicMock()
        q = attendance_col.where.return_value.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = existing_attendance or []
        mock_ref = MagicMock()
        mock_ref.id = "att_new"
        attendance_col.add.return_value = (None, mock_ref)

        def col_side(name):
            return {
                "users": users_col,
                "classes": classes_col,
                "modalities": modalities_col,
                "aulas": aulas_col,
                "attendance": attendance_col,
            }.get(name, MagicMock())

        mock_db.collection.side_effect = col_side
        return mock_db, attendance_col

    def test_new_confirm_writes_modality_slug_and_snapshot(self):
        mock_db, attendance_col = self._mock_db(
            graduation={
                "jiu-jitsu": {"belt": "roxa", "degree": 3, "status": "approved"},
            },
        )
        svc = AttendanceService()
        with patch(
            "app.services.attendance_service.firestore",
        ) as fs:
            fs.client.return_value = mock_db
            svc.confirm_attendance(
                project_id=_PROJECT_ID,
                class_id="t1",
                user_id=_USER_ID,
                actor_uid="staff1",
                aula_id="t1_20260716_1900",
            )

        assert attendance_col.add.call_count == 1
        (written,), _ = attendance_col.add.call_args
        assert written["modalitySlug"] == "jiu-jitsu"
        assert written["graduationSnapshot"] == {
            "belt": "roxa", "degree": 3, "status": "approved",
        }

    def test_confirm_update_of_existing_record_does_not_touch_snapshot(self):
        existing_doc = MagicMock()
        existing_doc.to_dict.return_value = {
            "status": "registered",
            "modalitySlug": "jiu-jitsu",
            "graduationSnapshot": {
                "belt": "azul", "degree": 0, "status": "approved",
            },
        }
        existing_doc.reference = MagicMock()
        existing_doc.reference.id = "att_existing"

        mock_db, attendance_col = self._mock_db(
            graduation={
                # user has since been promoted — must NOT leak into the
                # already-existing record's snapshot
                "jiu-jitsu": {"belt": "roxa", "degree": 4, "status": "approved"},
            },
            existing_attendance=[existing_doc],
        )
        svc = AttendanceService()
        with patch(
            "app.services.attendance_service.firestore",
        ) as fs:
            fs.client.return_value = mock_db
            svc.confirm_attendance(
                project_id=_PROJECT_ID,
                class_id="t1",
                user_id=_USER_ID,
                actor_uid="staff1",
                aula_id="t1_20260716_1900",
            )

        assert attendance_col.add.call_count == 0
        (update_payload,), _ = existing_doc.reference.update.call_args
        assert "modalitySlug" not in update_payload
        assert "graduationSnapshot" not in update_payload


# ── Absence job write path ──────────────────────────────────────────────────


class TestAbsenceJobWritesSnapshot:
    def _mock_db(self, graduation=None):
        """Job dirigido pela agenda: a turma vem da query de `classes` e o
        `aula` é materializado pelo próprio job (spec 29/07 §5)."""
        mock_db = MagicMock()

        aulas_col = MagicMock()
        aulas_col.document.return_value.get.return_value.exists = False

        class_doc = _class_doc()
        class_doc.id = "t1"
        class_doc.to_dict.return_value = {
            **class_doc.to_dict.return_value,
            "projectId": _PROJECT_ID,
            "schedule": [
                {"day": "thu", "startTime": "19:00", "endTime": "20:00"},
            ],
        }
        classes_col = MagicMock()
        classes_col.where.return_value.stream.return_value = [class_doc]
        classes_col.document.return_value.get.return_value = class_doc

        modalities_col = MagicMock()
        modalities_col.document.return_value.get.return_value = _modality_doc()

        user_doc = MagicMock()
        user_doc.id = _USER_ID
        user_doc.to_dict.return_value = {
            "name": "Aluno Y", "graduation": graduation or {},
        }
        users_col = MagicMock()
        users_col.where.return_value.stream.return_value = [user_doc]

        attendance_col = MagicMock()
        attendance_q = attendance_col.where.return_value.where.return_value
        attendance_q.stream.return_value = []  # nobody checked in
        mock_ref = MagicMock()
        mock_ref.id = "absence_1"
        attendance_col.add.return_value = (None, mock_ref)

        def col_side(name):
            return {
                "aulas": aulas_col,
                "classes": classes_col,
                "modalities": modalities_col,
                "users": users_col,
                "attendance": attendance_col,
            }.get(name, MagicMock())

        mock_db.collection.side_effect = col_side
        return mock_db, attendance_col

    def test_absence_job_writes_modality_slug_and_snapshot(self):
        mock_db, attendance_col = self._mock_db(
            graduation={
                "jiu-jitsu": {"belt": "branca", "degree": 2, "status": "approved"},
            },
        )
        svc = AbsenceJobService()
        with patch(
            "app.services.absence_job_service.firestore",
        ) as fs:
            fs.client.return_value = mock_db
            svc.compute(_PROJECT_ID, now=_JOB_NOW)

        assert attendance_col.add.call_count == 1
        (written,), _ = attendance_col.add.call_args
        assert written["modalitySlug"] == "jiu-jitsu"
        assert written["graduationSnapshot"] == {
            "belt": "branca", "degree": 2, "status": "approved",
        }

    def test_absence_job_user_without_graduation_writes_null_snapshot(self):
        mock_db, attendance_col = self._mock_db(graduation={})
        svc = AbsenceJobService()
        with patch(
            "app.services.absence_job_service.firestore",
        ) as fs:
            fs.client.return_value = mock_db
            svc.compute(_PROJECT_ID, now=_JOB_NOW)

        assert attendance_col.add.call_count == 1
        (written,), _ = attendance_col.add.call_args
        assert written["modalitySlug"] == "jiu-jitsu"
        assert written["graduationSnapshot"] is None
