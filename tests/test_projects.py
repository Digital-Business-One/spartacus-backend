import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_ROOT_ID = "root-project"
_PROJECT_ID = "member-project"
_AUTH = "Bearer tok"


def _claims(project_id: str, roles: list[str]) -> dict:
    return {
        "uid": "user-123",
        "email": "test@test.com",
        "projects": {project_id: roles},
    }


def _headers(project_id: str) -> dict:
    return {"Authorization": _AUTH, "X-Project-Id": project_id}


def _mock_db(doc_data: dict | None = None):
    mock_doc = MagicMock()
    mock_doc.exists = doc_data is not None
    mock_doc.to_dict.return_value = doc_data or {}
    mock_ref = MagicMock()
    mock_ref.get.return_value = mock_doc
    mock_ref.set.return_value = None
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_ref
    mock_db = MagicMock()
    mock_db.collection.return_value = mock_collection
    return mock_db


_STORED_PROJECT = {
    "id": _PROJECT_ID,
    "name": "Member Project",
    "razao_social": None,
    "cnpj": None,
    "logo_url": None,
    "address": None,
    "city": None,
    "state": None,
    "zip_code": None,
    "legal_nature": None,
    "founded_at": None,
    "is_root": False,
    "created_at": "2025-01-01T00:00:00+00:00",
}


# ── POST /projects ─────────────────────────────────────────────────────────────


class TestCreateProject:
    _PAYLOAD = {"id": "new-project", "name": "New Project"}

    def test_sem_token_retorna_401(self):
        response = client.post("/projects", json=self._PAYLOAD)
        assert response.status_code == 401

    def test_sem_project_id_retorna_400(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_ROOT_ID, ["owner"]),
        ):
            response = client.post(
                "/projects",
                json=self._PAYLOAD,
                headers={"Authorization": _AUTH},
            )
        assert response.status_code == 400

    def test_fora_do_root_retorna_403(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch.dict(os.environ, {"ROOT_PROJECT_ID": _ROOT_ID}):
                response = client.post(
                    "/projects",
                    json=self._PAYLOAD,
                    headers=_headers(_PROJECT_ID),
                )
        assert response.status_code == 403

    def test_owner_root_cria_projeto(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_ROOT_ID, ["owner"]),
        ):
            with patch.dict(os.environ, {"ROOT_PROJECT_ID": _ROOT_ID}):
                with patch(
                    "app.services.project_service.firestore"
                ) as mock_fs:
                    mock_fs.client.return_value = _mock_db()
                    response = client.post(
                        "/projects",
                        json=self._PAYLOAD,
                        headers=_headers(_ROOT_ID),
                    )
        assert response.status_code == 201
        body = response.json()
        assert body["id"] == "new-project"
        assert body["name"] == "New Project"
        assert body["is_root"] is False
        assert "created_at" in body

    def test_assistant_root_cria_projeto(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_ROOT_ID, ["assistant"]),
        ):
            with patch.dict(os.environ, {"ROOT_PROJECT_ID": _ROOT_ID}):
                with patch(
                    "app.services.project_service.firestore"
                ) as mock_fs:
                    mock_fs.client.return_value = _mock_db()
                    response = client.post(
                        "/projects",
                        json=self._PAYLOAD,
                        headers=_headers(_ROOT_ID),
                    )
        assert response.status_code == 201


# ── GET /projects/{project_id} ─────────────────────────────────────────────────


class TestGetProject:
    def test_sem_token_retorna_401(self):
        response = client.get(f"/projects/{_PROJECT_ID}")
        assert response.status_code == 401

    def test_sem_roles_retorna_403(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, []),
        ):
            response = client.get(
                f"/projects/{_PROJECT_ID}",
                headers=_headers(_PROJECT_ID),
            )
        assert response.status_code == 403

    def test_projeto_inexistente_retorna_404(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["teacher"]),
        ):
            with patch(
                "app.services.project_service.firestore"
            ) as mock_fs:
                mock_fs.client.return_value = _mock_db(None)
                response = client.get(
                    f"/projects/{_PROJECT_ID}",
                    headers=_headers(_PROJECT_ID),
                )
        assert response.status_code == 404

    def test_membro_com_role_retorna_projeto(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["teacher"]),
        ):
            with patch(
                "app.services.project_service.firestore"
            ) as mock_fs:
                mock_fs.client.return_value = _mock_db(_STORED_PROJECT)
                response = client.get(
                    f"/projects/{_PROJECT_ID}",
                    headers=_headers(_PROJECT_ID),
                )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == _PROJECT_ID
        assert body["name"] == "Member Project"

    def test_owner_retorna_projeto(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch(
                "app.services.project_service.firestore"
            ) as mock_fs:
                mock_fs.client.return_value = _mock_db(_STORED_PROJECT)
                response = client.get(
                    f"/projects/{_PROJECT_ID}",
                    headers=_headers(_PROJECT_ID),
                )
        assert response.status_code == 200
