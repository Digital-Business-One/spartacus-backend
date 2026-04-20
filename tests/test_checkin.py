from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

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
