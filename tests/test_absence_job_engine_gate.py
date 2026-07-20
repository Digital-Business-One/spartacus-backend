"""TDD tests for Task A5 — attendance-engine gates in the nightly absence
job (compute-absences), plus the inconsistency log.

See: docs/superpowers/specs/2026-07-18-frequencia-analitica-design.md
(sections "Motor de frequência — por turma" and "Tratamento de erros").
"""

import logging
from unittest.mock import MagicMock, patch

from app.services.absence_job_service import AbsenceJobService

_PROJECT_ID = "spartacus"


def _aula_doc(doc_id="t1_20260718_1900", turma_id="t1", end_time=""):
    d = MagicMock()
    d.id = doc_id
    d.to_dict.return_value = {
        "projectId": _PROJECT_ID,
        "turmaId": turma_id,
        "endTime": end_time,
    }
    return d


def _class_doc(
    name="Turma A",
    modality_id="spartacus_jiu-jitsu",
    attendance_engine_enabled=True,
    attendance_start_date="2020-01-01",
):
    d = MagicMock()
    d.exists = True
    d.to_dict.return_value = {
        "name": name,
        "modalityId": modality_id,
        "attendanceEngineEnabled": attendance_engine_enabled,
        "attendanceStartDate": attendance_start_date,
    }
    return d


def _modality_doc(slug="jiu-jitsu", name="Jiu-Jitsu"):
    d = MagicMock()
    d.exists = True
    d.to_dict.return_value = {"slug": slug, "name": name}
    return d


def _user_doc(uid="user123", name="Aluno Y"):
    d = MagicMock()
    d.id = uid
    d.to_dict.return_value = {"name": name, "graduation": {}}
    return d


def _mock_db(class_doc, aula_end_time="2026-07-18T20:00:00+00:00"):
    mock_db = MagicMock()

    aula_doc = _aula_doc(end_time=aula_end_time)
    aulas_col = MagicMock()
    aulas_q = aulas_col.where.return_value.where.return_value.where.return_value
    aulas_q.stream.return_value = [aula_doc]

    classes_col = MagicMock()
    classes_col.document.return_value.get.return_value = class_doc

    modalities_col = MagicMock()
    modalities_col.document.return_value.get.return_value = _modality_doc()

    users_col = MagicMock()
    users_col.where.return_value.stream.return_value = [_user_doc()]

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


class TestJobSkipsEngineOff:
    def test_engine_off_turma_creates_no_absences(self):
        class_doc = _class_doc(attendance_engine_enabled=False)
        mock_db, attendance_col = _mock_db(class_doc)

        svc = AbsenceJobService()
        with patch("app.services.absence_job_service.firestore") as fs:
            fs.client.return_value = mock_db
            events = svc.compute(_PROJECT_ID)

        assert attendance_col.add.call_count == 0
        assert events == []


class TestJobNeverRetroacts:
    def test_aula_date_before_start_date_creates_no_absences(self):
        # Engine's data-base is tomorrow relative to the aula being
        # processed today — the job must never mark absences for dates
        # before the turma's attendanceStartDate.
        class_doc = _class_doc(attendance_start_date="2026-07-19")
        mock_db, attendance_col = _mock_db(
            class_doc, aula_end_time="2026-07-18T20:00:00+00:00",
        )

        svc = AbsenceJobService()
        with patch("app.services.absence_job_service.firestore") as fs:
            fs.client.return_value = mock_db
            events = svc.compute(_PROJECT_ID)

        assert attendance_col.add.call_count == 0
        assert events == []

    def test_aula_date_on_or_after_start_date_creates_absences(self):
        class_doc = _class_doc(attendance_start_date="2026-07-18")
        mock_db, attendance_col = _mock_db(
            class_doc, aula_end_time="2026-07-18T20:00:00+00:00",
        )

        svc = AbsenceJobService()
        with patch("app.services.absence_job_service.firestore") as fs:
            fs.client.return_value = mock_db
            events = svc.compute(_PROJECT_ID)

        assert attendance_col.add.call_count == 1
        assert len(events) == 1


class TestInconsistencyGuard:
    def test_enabled_without_start_date_is_treated_as_off_and_logged(
        self, caplog,
    ):
        class_doc = _class_doc(
            attendance_engine_enabled=True, attendance_start_date=None,
        )
        mock_db, attendance_col = _mock_db(class_doc)

        svc = AbsenceJobService()
        with (
            patch("app.services.absence_job_service.firestore") as fs,
            caplog.at_level(logging.ERROR),
        ):
            fs.client.return_value = mock_db
            events = svc.compute(_PROJECT_ID)

        # Treated as off: no counting, no absences.
        assert attendance_col.add.call_count == 0
        assert events == []

        # Logged with a stable, greppable marker + the offending classId.
        error_records = [
            r for r in caplog.records if r.levelno == logging.ERROR
        ]
        assert any(
            "attendance-engine-inconsistency" in r.getMessage()
            and "t1" in r.getMessage()
            for r in error_records
        )
