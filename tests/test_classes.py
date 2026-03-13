from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PROJECT_ID = "spartacus"


def _mock_db_stream(docs: list[tuple[str, dict]]):
    """Build a mock Firestore client that returns `docs` from .stream()."""
    mock_docs = []
    for doc_id, data in docs:
        mock_doc = MagicMock()
        mock_doc.id = doc_id
        mock_doc.to_dict.return_value = data
        mock_docs.append(mock_doc)

    mock_query = MagicMock()
    mock_query.stream.return_value = mock_docs
    mock_query.where.return_value = mock_query  # allow chaining

    mock_collection = MagicMock()
    mock_collection.where.return_value = mock_query

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_collection
    return mock_db


_CLASS_JIU_JITSU = {
    "projectId": _PROJECT_ID,
    "name": "Jiu-Jitsu Kids",
    "modality": "Jiu-Jitsu",
    "weeklySchedule": {
        "days": ["mon", "wed", "fri"],
        "startTime": "08:00",
        "endTime": "09:00",
    },
    "teacherName": "Istanrley",
    "ageRange": {"min": 5, "max": 12},
    "iconUrl": None,
    "active": True,
}

_CLASS_MUAY_THAI = {
    "projectId": _PROJECT_ID,
    "name": "Muay Thai",
    "modality": "Muay Thai",
    "weeklySchedule": {
        "days": ["mon", "wed", "fri"],
        "startTime": "20:00",
        "endTime": "21:00",
    },
    "teacherName": None,
    "ageRange": {"min": 16, "max": None},
    "iconUrl": None,
    "active": True,
}


class TestListClasses:
    def test_sem_autenticacao_retorna_200(self):
        """Endpoint is public — no auth required."""
        with patch("app.services.class_service.firestore") as mock_fs:
            mock_fs.client.return_value = _mock_db_stream([])
            response = client.get(f"/projects/{_PROJECT_ID}/classes")
        assert response.status_code == 200

    def test_projeto_sem_turmas_retorna_lista_vazia(self):
        with patch("app.services.class_service.firestore") as mock_fs:
            mock_fs.client.return_value = _mock_db_stream([])
            response = client.get(f"/projects/{_PROJECT_ID}/classes")
        assert response.status_code == 200
        assert response.json() == {"classes": []}

    def test_retorna_classes_do_projeto(self):
        docs = [
            (f"{_PROJECT_ID}_jiu-jitsu-kids", _CLASS_JIU_JITSU),
            (f"{_PROJECT_ID}_muay-thai", _CLASS_MUAY_THAI),
        ]
        with patch("app.services.class_service.firestore") as mock_fs:
            mock_fs.client.return_value = _mock_db_stream(docs)
            response = client.get(f"/projects/{_PROJECT_ID}/classes")

        assert response.status_code == 200
        body = response.json()
        assert len(body["classes"]) == 2

    def test_formata_schedule_dias_em_portugues(self):
        docs = [(f"{_PROJECT_ID}_jiu-jitsu-kids", _CLASS_JIU_JITSU)]
        with patch("app.services.class_service.firestore") as mock_fs:
            mock_fs.client.return_value = _mock_db_stream(docs)
            response = client.get(f"/projects/{_PROJECT_ID}/classes")

        cls = response.json()["classes"][0]
        assert cls["schedule"] == "Seg/Qua/Sex 08:00–09:00"

    def test_campos_obrigatorios_presentes(self):
        docs = [(f"{_PROJECT_ID}_jiu-jitsu-kids", _CLASS_JIU_JITSU)]
        with patch("app.services.class_service.firestore") as mock_fs:
            mock_fs.client.return_value = _mock_db_stream(docs)
            response = client.get(f"/projects/{_PROJECT_ID}/classes")

        cls = response.json()["classes"][0]
        assert cls["id"] == f"{_PROJECT_ID}_jiu-jitsu-kids"
        assert cls["name"] == "Jiu-Jitsu Kids"
        assert cls["modality"] == "Jiu-Jitsu"
        assert cls["teacher"] == "Istanrley"
        assert cls["age_range"] == {"min": 5, "max": 12}
        assert cls["icon_url"] is None

    def test_teacher_none_quando_sem_professor(self):
        docs = [(f"{_PROJECT_ID}_muay-thai", _CLASS_MUAY_THAI)]
        with patch("app.services.class_service.firestore") as mock_fs:
            mock_fs.client.return_value = _mock_db_stream(docs)
            response = client.get(f"/projects/{_PROJECT_ID}/classes")

        cls = response.json()["classes"][0]
        assert cls["teacher"] is None
        assert cls["age_range"]["max"] is None
