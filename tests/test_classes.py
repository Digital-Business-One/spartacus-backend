from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

with patch("firebase_admin.initialize_app"):
    from app.main import app

from app.models.classes import ClassCreate, ScheduleItem

client = TestClient(app, raise_server_exceptions=False)

_PROJECT_ID = "spartacus"
_MOD_JJ_ID = f"{_PROJECT_ID}_jiu-jitsu"
_MOD_MT_ID = f"{_PROJECT_ID}_muay-thai"
_AUTH = "Bearer tok"


def _claims(project_id: str, roles: list[str]) -> dict:
    return {
        "uid": "admin-user",
        "email": "admin@test.com",
        "projects": {project_id: roles},
    }


def _headers(project_id: str) -> dict:
    return {"Authorization": _AUTH, "X-Project-Id": project_id}


def _mock_update_db(existing_data: dict | None):
    """Mock Firestore for update(): one doc via collection().document()."""
    mock_doc = MagicMock()
    mock_doc.id = f"{_PROJECT_ID}_jj-kids"
    mock_doc.exists = existing_data is not None
    mock_doc.to_dict.return_value = existing_data or {}

    mock_ref = MagicMock()
    mock_ref.get.return_value = mock_doc

    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_ref

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_db.get_all.return_value = []
    return mock_db, mock_ref


def _mock_db(class_docs, modality_docs=None):
    """Build mock Firestore with classes + modalities batch resolve."""
    mock_class_docs = []
    for doc_id, data in class_docs:
        d = MagicMock()
        d.id = doc_id
        d.to_dict.return_value = data
        mock_class_docs.append(d)

    mock_mod_docs = []
    for doc_id, data in (modality_docs or []):
        d = MagicMock()
        d.id = doc_id
        d.exists = True
        d.to_dict.return_value = data
        mock_mod_docs.append(d)

    mock_query = MagicMock()
    mock_query.stream.return_value = mock_class_docs
    mock_query.where.return_value = mock_query

    mock_collection = MagicMock()
    mock_collection.where.return_value = mock_query

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_collection
    mock_db.get_all.return_value = mock_mod_docs
    return mock_db


_CLASS_JJ = {
    "projectId": _PROJECT_ID,
    "name": "Juvenil e Kids",
    "modalityId": _MOD_JJ_ID,
    "schedule": [
        {"day": "mon", "startTime": "08:00", "endTime": "09:00"},
        {"day": "wed", "startTime": "08:00", "endTime": "09:00"},
        {"day": "fri", "startTime": "08:00", "endTime": "09:00"},
    ],
    "teacherName": "Istanrley",
    "location": "Tatame Principal",
    "ageRange": {"min": 5, "max": 12},
    "iconUrl": None,
    "active": True,
}

_CLASS_MT = {
    "projectId": _PROJECT_ID,
    "name": "Cardio",
    "modalityId": _MOD_MT_ID,
    "schedule": [
        {"day": "mon", "startTime": "20:00", "endTime": "21:00"},
    ],
    "teacherName": None,
    "location": None,
    "ageRange": {"min": 16, "max": None},
    "iconUrl": None,
    "active": True,
}

_MOD_DOCS = [
    (_MOD_JJ_ID, {"name": "Jiu-Jitsu", "slug": "jiu-jitsu"}),
    (_MOD_MT_ID, {"name": "Muay Thai", "slug": "muay-thai"}),
]


class TestListClasses:
    def test_public_returns_200(self):
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db([])
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        assert r.status_code == 200

    def test_empty_project(self):
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db([])
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        assert r.json() == {"classes": []}

    def test_returns_classes(self):
        docs = [
            (f"{_PROJECT_ID}_jj-kids", _CLASS_JJ),
            (f"{_PROJECT_ID}_mt-cardio", _CLASS_MT),
        ]
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db(docs, _MOD_DOCS)
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        assert len(r.json()["classes"]) == 2

    def test_schedule_pt(self):
        docs = [(f"{_PROJECT_ID}_jj-kids", _CLASS_JJ)]
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db(docs, _MOD_DOCS)
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        cls = r.json()["classes"][0]
        assert cls["schedule"] == "Seg/Qua/Sex 08:00–09:00"

    def test_fields(self):
        docs = [(f"{_PROJECT_ID}_jj-kids", _CLASS_JJ)]
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db(docs, _MOD_DOCS)
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        cls = r.json()["classes"][0]
        assert cls["name"] == "Juvenil e Kids"
        assert cls["modality_id"] == _MOD_JJ_ID
        assert cls["modality_name"] == "Jiu-Jitsu"
        assert cls["teacher"] == "Istanrley"
        assert cls["location"] == "Tatame Principal"
        assert cls["age_range"] == {"min": 5, "max": 12}
        assert len(cls["schedule_items"]) == 3

    def test_teacher_none(self):
        docs = [(f"{_PROJECT_ID}_mt-cardio", _CLASS_MT)]
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db(docs, _MOD_DOCS)
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        cls = r.json()["classes"][0]
        assert cls["teacher"] is None
        assert cls["location"] is None


class TestLegacyFormat:
    """Old weeklySchedule format still works."""

    def test_old_format_parsed(self):
        old_doc = {
            "projectId": _PROJECT_ID,
            "name": "Old Turma",
            "modality": "Capoeira",
            "weeklySchedule": {
                "days": ["tue", "thu"],
                "startTime": "17:00",
                "endTime": "18:00",
            },
            "teacherName": None,
            "ageRange": None,
            "iconUrl": None,
            "active": True,
        }
        docs = [(f"{_PROJECT_ID}_old", old_doc)]
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db(docs)
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        cls = r.json()["classes"][0]
        assert cls["schedule"] == "Ter/Qui 17:00–18:00"
        assert cls["modality_name"] == "Capoeira"
        assert len(cls["schedule_items"]) == 2


# ── Attendance engine fields (RFC "Frequência Analítica" — Task A2) ───────────


class TestClassOutAttendanceFields:
    def test_defaults_when_absent_from_doc(self):
        docs = [(f"{_PROJECT_ID}_jj-kids", _CLASS_JJ)]
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db(docs, _MOD_DOCS)
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        cls = r.json()["classes"][0]
        assert cls["attendanceEngineEnabled"] is False
        assert cls["attendanceStartDate"] is None

    def test_reads_persisted_values(self):
        doc = dict(
            _CLASS_JJ,
            attendanceEngineEnabled=True,
            attendanceStartDate="2026-01-01",
        )
        docs = [(f"{_PROJECT_ID}_jj-kids", doc)]
        with patch("app.services.class_service.firestore") as fs:
            fs.client.return_value = _mock_db(docs, _MOD_DOCS)
            r = client.get(f"/projects/{_PROJECT_ID}/classes")
        cls = r.json()["classes"][0]
        assert cls["attendanceEngineEnabled"] is True
        assert cls["attendanceStartDate"] == "2026-01-01"


class TestClassCreateModelValidation:
    """Unit-level: ClassCreate enforces enabled ⇒ start date required."""

    _SCHEDULE = [ScheduleItem(day="mon", start_time="08:00", end_time="09:00")]

    def test_enabled_without_date_raises(self):
        with pytest.raises(ValidationError):
            ClassCreate(
                id="x",
                name="X",
                modality_id="m1",
                schedule=self._SCHEDULE,
                attendanceEngineEnabled=True,
            )

    def test_enabled_with_date_ok(self):
        c = ClassCreate(
            id="x",
            name="X",
            modality_id="m1",
            schedule=self._SCHEDULE,
            attendanceEngineEnabled=True,
            attendanceStartDate="2026-01-01",
        )
        assert c.attendance_engine_enabled is True
        assert c.attendance_start_date == "2026-01-01"

    def test_disabled_without_date_defaults(self):
        c = ClassCreate(id="x", name="X", modality_id="m1", schedule=self._SCHEDULE)
        assert c.attendance_engine_enabled is False
        assert c.attendance_start_date is None

    def test_invalid_date_format_raises(self):
        with pytest.raises(ValidationError):
            ClassCreate(
                id="x",
                name="X",
                modality_id="m1",
                schedule=self._SCHEDULE,
                attendanceEngineEnabled=True,
                attendanceStartDate="01/01/2026",
            )


class TestCreateClassAttendanceEndpoint:
    _BASE_PAYLOAD = {
        "id": "jj-new",
        "name": "Nova Turma",
        "modality_id": _MOD_JJ_ID,
        "schedule": [{"day": "mon", "start_time": "08:00", "end_time": "09:00"}],
    }

    def test_enabled_without_date_returns_422(self):
        payload = dict(self._BASE_PAYLOAD, attendanceEngineEnabled=True)
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            r = client.post(
                f"/projects/{_PROJECT_ID}/classes",
                json=payload,
                headers=_headers(_PROJECT_ID),
            )
        assert r.status_code == 422

    def test_enabled_with_date_persists(self):
        payload = dict(
            self._BASE_PAYLOAD,
            attendanceEngineEnabled=True,
            attendanceStartDate="2026-02-01",
        )
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.class_service.firestore") as fs:
                fs.client.return_value = _mock_db([], _MOD_DOCS)
                r = client.post(
                    f"/projects/{_PROJECT_ID}/classes",
                    json=payload,
                    headers=_headers(_PROJECT_ID),
                )
        assert r.status_code == 201
        body = r.json()
        assert body["attendanceEngineEnabled"] is True
        assert body["attendanceStartDate"] == "2026-02-01"

    def test_disabled_default_persists(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.class_service.firestore") as fs:
                fs.client.return_value = _mock_db([], _MOD_DOCS)
                r = client.post(
                    f"/projects/{_PROJECT_ID}/classes",
                    json=self._BASE_PAYLOAD,
                    headers=_headers(_PROJECT_ID),
                )
        assert r.status_code == 201
        body = r.json()
        assert body["attendanceEngineEnabled"] is False
        assert body["attendanceStartDate"] is None


class TestUpdateClassAttendanceEndpoint:
    _CLASS_ID = f"{_PROJECT_ID}_jj-kids"

    def test_enable_without_any_date_returns_422(self):
        existing = {
            "projectId": _PROJECT_ID,
            "name": "X",
            "modalityId": "",
            "attendanceEngineEnabled": False,
            "attendanceStartDate": None,
        }
        mock_db, mock_ref = _mock_update_db(existing)
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.class_service.firestore") as fs:
                fs.client.return_value = mock_db
                r = client.patch(
                    f"/projects/{_PROJECT_ID}/classes/{self._CLASS_ID}",
                    json={"attendanceEngineEnabled": True},
                    headers=_headers(_PROJECT_ID),
                )
        assert r.status_code == 422
        mock_ref.update.assert_not_called()

    def test_enable_relying_on_persisted_date_ok(self):
        """PATCH enabled=true alone, using a start date set in an earlier call."""
        existing = {
            "projectId": _PROJECT_ID,
            "name": "X",
            "modalityId": "",
            "attendanceEngineEnabled": False,
            "attendanceStartDate": "2026-01-01",
        }
        mock_db, mock_ref = _mock_update_db(existing)
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.class_service.firestore") as fs:
                fs.client.return_value = mock_db
                r = client.patch(
                    f"/projects/{_PROJECT_ID}/classes/{self._CLASS_ID}",
                    json={"attendanceEngineEnabled": True},
                    headers=_headers(_PROJECT_ID),
                )
        assert r.status_code == 200
        mock_ref.update.assert_called_once_with({"attendanceEngineEnabled": True})

    def test_enable_with_date_in_same_payload_ok(self):
        existing = {
            "projectId": _PROJECT_ID,
            "name": "X",
            "modalityId": "",
            "attendanceEngineEnabled": False,
            "attendanceStartDate": None,
        }
        mock_db, mock_ref = _mock_update_db(existing)
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.class_service.firestore") as fs:
                fs.client.return_value = mock_db
                r = client.patch(
                    f"/projects/{_PROJECT_ID}/classes/{self._CLASS_ID}",
                    json={
                        "attendanceEngineEnabled": True,
                        "attendanceStartDate": "2026-03-01",
                    },
                    headers=_headers(_PROJECT_ID),
                )
        assert r.status_code == 200
        updates = mock_ref.update.call_args[0][0]
        assert updates["attendanceEngineEnabled"] is True
        assert updates["attendanceStartDate"] == "2026-03-01"

    def test_disable_without_date_ok(self):
        existing = {
            "projectId": _PROJECT_ID,
            "name": "X",
            "modalityId": "",
            "attendanceEngineEnabled": True,
            "attendanceStartDate": "2026-01-01",
        }
        mock_db, mock_ref = _mock_update_db(existing)
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.class_service.firestore") as fs:
                fs.client.return_value = mock_db
                r = client.patch(
                    f"/projects/{_PROJECT_ID}/classes/{self._CLASS_ID}",
                    json={"attendanceEngineEnabled": False},
                    headers=_headers(_PROJECT_ID),
                )
        assert r.status_code == 200
        mock_ref.update.assert_called_once_with({"attendanceEngineEnabled": False})
