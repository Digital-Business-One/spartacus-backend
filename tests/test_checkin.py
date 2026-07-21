from datetime import datetime
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

from app.services.checkin_service import _DAY_CODES, _TZ_OFFSET

client = TestClient(app, raise_server_exceptions=False)

_PROJECT_ID = "spartacus"
_USER_ID = "user123"
_VERIFY = "app.security.middleware.verify_id_token"
_FS = "app.services.checkin_service.firestore"

_HEADERS = {
    "Authorization": "Bearer tok",
    "X-Project-Id": _PROJECT_ID,
}

_CLAIMS = {
    "uid": _USER_ID,
    "email": "test@test.com",
    "projects": {_PROJECT_ID: ["student"]},
}


def _user_doc(class_ids=None):
    d = MagicMock()
    d.exists = True
    d.to_dict.return_value = {
        "classIds": class_ids or [],
    }
    return d


def _class_doc(
    doc_id, name="Turma", modality_id="mod1",
    schedule=None, active=True,
    attendance_engine_enabled=True,
    attendance_start_date="2020-01-01",
):
    d = MagicMock()
    d.exists = True
    d.id = doc_id
    d.to_dict.return_value = {
        "name": name,
        "modalityId": modality_id,
        "schedule": schedule or [],
        "teacherName": "Prof X",
        "location": "Tatame",
        "active": active,
        "attendanceEngineEnabled": attendance_engine_enabled,
        "attendanceStartDate": attendance_start_date,
    }
    return d


def _modality_doc(name="Jiu-Jitsu"):
    d = MagicMock()
    d.exists = True
    d.to_dict.return_value = {"name": name}
    return d


def _aula_doc(exists=False, turma_id="t1", end_time=""):
    d = MagicMock()
    d.exists = exists
    d.to_dict.return_value = {
        "turmaId": turma_id,
        "endTime": end_time,
    }
    return d


class TestGetAvailable:
    def test_no_classes_returns_no_checkin(self):
        mock_db = MagicMock()
        user = _user_doc(class_ids=[])
        mock_db.collection.return_value.document.return_value.get.return_value = user

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.get("/checkin/available", headers=_HEADERS)

        assert r.status_code == 200
        assert r.json()["message"] == "Nenhuma aula disponível no momento"

    def test_user_not_found_returns_404(self):
        mock_db = MagicMock()
        not_found = MagicMock()
        not_found.exists = False
        col = mock_db.collection.return_value
        col.document.return_value.get.return_value = not_found

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.get("/checkin/available", headers=_HEADERS)

        assert r.status_code == 404

    def test_engine_off_turma_not_offered(self):
        fixed_now = datetime(2026, 7, 20, 19, 5, tzinfo=_TZ_OFFSET)  # Monday
        day_code = _DAY_CODES[fixed_now.weekday()]

        mock_db = MagicMock()
        users_col = MagicMock()
        users_col.document.return_value.get.return_value = _user_doc(
            class_ids=["t1"],
        )

        class_doc = _class_doc(
            "t1",
            schedule=[
                {"day": day_code, "startTime": "19:00", "endTime": "20:00"},
            ],
            attendance_engine_enabled=False,
        )
        mock_db.get_all.return_value = [class_doc]

        def col_side(name):
            if name == "users":
                return users_col
            return MagicMock()

        mock_db.collection.side_effect = col_side

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
            patch(
                "app.services.checkin_service._now_local",
                return_value=fixed_now,
            ),
        ):
            fs.client.return_value = mock_db
            r = client.get("/checkin/available", headers=_HEADERS)

        assert r.status_code == 200
        body = r.json()
        assert body["message"] == "Nenhuma aula disponível no momento"
        assert body["nextClass"] is None

    def test_engine_on_turma_is_offered(self):
        fixed_now = datetime(2026, 7, 20, 19, 5, tzinfo=_TZ_OFFSET)  # Monday
        day_code = _DAY_CODES[fixed_now.weekday()]

        mock_db = MagicMock()
        users_col = MagicMock()
        users_col.document.return_value.get.return_value = _user_doc(
            class_ids=["t1"],
        )

        class_doc = _class_doc(
            "t1",
            schedule=[
                {"day": day_code, "startTime": "19:00", "endTime": "20:00"},
            ],
            attendance_engine_enabled=True,
        )
        mock_db.get_all.return_value = [class_doc]

        modalities_col = MagicMock()
        modalities_col.document.return_value.get.return_value = _modality_doc()

        aulas_col = MagicMock()
        aulas_col.document.return_value.get.return_value = _aula_doc(
            exists=False,
        )

        attendance_col = MagicMock()
        q = attendance_col.where.return_value
        q = q.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = []

        def col_side(name):
            return {
                "users": users_col,
                "modalities": modalities_col,
                "aulas": aulas_col,
                "attendance": attendance_col,
            }.get(name, MagicMock())

        mock_db.collection.side_effect = col_side

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
            patch(
                "app.services.checkin_service._now_local",
                return_value=fixed_now,
            ),
        ):
            fs.client.return_value = mock_db
            r = client.get("/checkin/available", headers=_HEADERS)

        assert r.status_code == 200
        body = r.json()
        assert body["aulaId"] == "t1_20260720_1900"
        assert body["turmaId"] == "t1"


class TestDoCheckin:
    def test_duplicate_returns_409(self):
        mock_db = MagicMock()
        aula = _aula_doc(exists=True, turma_id="t1")

        users_col = MagicMock()
        aulas_col = MagicMock()
        aulas_col.document.return_value.get.return_value = aula
        attendance_col = MagicMock()
        # Return 1 existing attendance record = duplicate
        q = attendance_col.where.return_value
        q = q.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = [MagicMock()]

        def col_side(name):
            if name == "users":
                return users_col
            if name == "aulas":
                return aulas_col
            if name == "attendance":
                return attendance_col
            return MagicMock()

        mock_db.collection.side_effect = col_side

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.post(
                "/checkin",
                headers=_HEADERS,
                json={"aulaId": "turma_20260330_1900"},
            )

        assert r.status_code == 409

    def test_aula_not_found_returns_400(self):
        mock_db = MagicMock()
        not_found = _aula_doc(exists=False)
        col = mock_db.collection.return_value
        col.document.return_value.get.return_value = not_found

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.post(
                "/checkin",
                headers=_HEADERS,
                json={"aulaId": "nonexistent"},
            )

        assert r.status_code == 400

    def test_successful_checkin(self):
        mock_db = MagicMock()
        aula = _aula_doc(exists=True, turma_id="t1")

        aulas_col = MagicMock()
        aulas_col.document.return_value.get.return_value = aula

        classes_col = MagicMock()
        classes_col.document.return_value.get.return_value = _class_doc("t1")

        attendance_col = MagicMock()
        q = attendance_col.where.return_value
        q = q.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = []
        mock_ref = MagicMock()
        mock_ref.id = "attendance_123"
        attendance_col.add.return_value = (None, mock_ref)

        def col_side(name):
            if name == "aulas":
                return aulas_col
            if name == "classes":
                return classes_col
            if name == "attendance":
                return attendance_col
            return MagicMock()

        mock_db.collection.side_effect = col_side

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.post(
                "/checkin",
                headers=_HEADERS,
                json={"aulaId": "turma_20260330_1900"},
            )

        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "registered"
        assert data["attendanceId"] == "attendance_123"

    def test_checkin_returns_422_when_engine_off(self):
        mock_db = MagicMock()
        aula = _aula_doc(exists=True, turma_id="t1")

        aulas_col = MagicMock()
        aulas_col.document.return_value.get.return_value = aula

        classes_col = MagicMock()
        classes_col.document.return_value.get.return_value = _class_doc(
            "t1", attendance_engine_enabled=False, attendance_start_date=None,
        )

        attendance_col = MagicMock()
        q = attendance_col.where.return_value
        q = q.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = []

        def col_side(name):
            if name == "aulas":
                return aulas_col
            if name == "classes":
                return classes_col
            if name == "attendance":
                return attendance_col
            return MagicMock()

        mock_db.collection.side_effect = col_side

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.post(
                "/checkin",
                headers=_HEADERS,
                json={"aulaId": "turma_20260330_1900"},
            )

        assert r.status_code == 422
        assert attendance_col.add.call_count == 0

    def test_checkin_returns_422_when_engine_enabled_without_start_date(self):
        """Enabled-without-start-date is an inconsistent config — treated
        as off in the check-in path (the nightly job is responsible for
        logging the inconsistency for the admin)."""
        mock_db = MagicMock()
        aula = _aula_doc(exists=True, turma_id="t1")

        aulas_col = MagicMock()
        aulas_col.document.return_value.get.return_value = aula

        classes_col = MagicMock()
        classes_col.document.return_value.get.return_value = _class_doc(
            "t1", attendance_engine_enabled=True, attendance_start_date=None,
        )

        attendance_col = MagicMock()
        q = attendance_col.where.return_value
        q = q.where.return_value.where.return_value
        q.limit.return_value.stream.return_value = []

        def col_side(name):
            if name == "aulas":
                return aulas_col
            if name == "classes":
                return classes_col
            if name == "attendance":
                return attendance_col
            return MagicMock()

        mock_db.collection.side_effect = col_side

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.post(
                "/checkin",
                headers=_HEADERS,
                json={"aulaId": "turma_20260330_1900"},
            )

        assert r.status_code == 422
        assert attendance_col.add.call_count == 0
