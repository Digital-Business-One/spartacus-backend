from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PROJECT_ID = "spartacus"
_MOD_JJ_ID = f"{_PROJECT_ID}_jiu-jitsu"
_MOD_MT_ID = f"{_PROJECT_ID}_muay-thai"


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
