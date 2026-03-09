from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PROJECT_ID = "test-project"
_OTHER_ID = "other-project"
_USER_ID = "user-abc"
_AUTH = "Bearer tok"

_STORED_MEMBERSHIP = {
    "projectId": _PROJECT_ID,
    "userId": _USER_ID,
    "roles": ["teacher"],
    "status": "active",
    "joined_at": "2025-01-01T00:00:00+00:00",
}


def _claims(project_id: str, roles: list[str]) -> dict:
    return {
        "uid": "admin-user",
        "email": "admin@test.com",
        "projects": {project_id: roles},
    }


def _headers(project_id: str) -> dict:
    return {"Authorization": _AUTH, "X-Project-Id": project_id}


def _mock_db(doc_data: dict | None = None, stream_docs: list[dict] | None = None):
    """Monta um Firestore client fake para operações de membership."""
    mock_doc = MagicMock()
    mock_doc.exists = doc_data is not None
    mock_doc.to_dict.return_value = doc_data or {}

    mock_ref = MagicMock()
    mock_ref.get.return_value = mock_doc

    # Suporte a .where().where().stream()
    stream_items = []
    for d in (stream_docs or []):
        m = MagicMock()
        m.to_dict.return_value = d
        stream_items.append(m)

    mock_query = MagicMock()
    mock_query.where.return_value = mock_query
    mock_query.stream.return_value = iter(stream_items)

    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_ref
    mock_collection.where.return_value = mock_query

    mock_db = MagicMock()
    mock_db.collection.return_value = mock_collection
    return mock_db


# ── POST /projects/{id}/members ───────────────────────────────────────────────


class TestAddMember:
    _PAYLOAD = {"user_id": _USER_ID, "roles": ["teacher"]}

    def test_sem_token_retorna_401(self):
        response = client.post(f"/projects/{_PROJECT_ID}/members", json=self._PAYLOAD)
        assert response.status_code == 401

    def test_role_insuficiente_retorna_403(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["student"]),
        ):
            response = client.post(
                f"/projects/{_PROJECT_ID}/members",
                json=self._PAYLOAD,
                headers=_headers(_PROJECT_ID),
            )
        assert response.status_code == 403

    def test_project_id_divergente_retorna_403(self):
        """X-Project-Id diferente do project_id da rota → 403."""
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_OTHER_ID, ["owner"]),
        ):
            response = client.post(
                f"/projects/{_PROJECT_ID}/members",
                json=self._PAYLOAD,
                headers=_headers(_OTHER_ID),
            )
        assert response.status_code == 403

    def test_owner_adiciona_membro(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.membership_service.firestore") as mock_fs:
                with patch("app.services.membership_service.auth") as mock_auth:
                    mock_fs.client.return_value = _mock_db(doc_data=None)
                    response = client.post(
                        f"/projects/{_PROJECT_ID}/members",
                        json=self._PAYLOAD,
                        headers=_headers(_PROJECT_ID),
                    )
        assert response.status_code == 201
        body = response.json()
        assert body["user_id"] == _USER_ID
        assert body["roles"] == ["teacher"]
        assert body["status"] == "active"
        assert "joined_at" in body
        mock_auth.set_custom_user_claims.assert_called_once()

    def test_membro_duplicado_retorna_409(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.membership_service.firestore") as mock_fs:
                with patch("app.services.membership_service.auth"):
                    mock_fs.client.return_value = _mock_db(
                        doc_data=_STORED_MEMBERSHIP
                    )
                    response = client.post(
                        f"/projects/{_PROJECT_ID}/members",
                        json=self._PAYLOAD,
                        headers=_headers(_PROJECT_ID),
                    )
        assert response.status_code == 409


# ── GET /projects/{id}/members ────────────────────────────────────────────────


class TestListMembers:
    def test_sem_token_retorna_401(self):
        response = client.get(f"/projects/{_PROJECT_ID}/members")
        assert response.status_code == 401

    def test_role_insuficiente_retorna_403(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["teacher"]),
        ):
            response = client.get(
                f"/projects/{_PROJECT_ID}/members",
                headers=_headers(_PROJECT_ID),
            )
        assert response.status_code == 403

    def test_lista_membros_ativos(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.membership_service.firestore") as mock_fs:
                mock_fs.client.return_value = _mock_db(
                    stream_docs=[_STORED_MEMBERSHIP]
                )
                response = client.get(
                    f"/projects/{_PROJECT_ID}/members",
                    headers=_headers(_PROJECT_ID),
                )
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["user_id"] == _USER_ID
        assert body[0]["roles"] == ["teacher"]

    def test_lista_vazia(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["assistant"]),
        ):
            with patch("app.services.membership_service.firestore") as mock_fs:
                mock_fs.client.return_value = _mock_db(stream_docs=[])
                response = client.get(
                    f"/projects/{_PROJECT_ID}/members",
                    headers=_headers(_PROJECT_ID),
                )
        assert response.status_code == 200
        assert response.json() == []


# ── PATCH /projects/{id}/members/{user_id} ────────────────────────────────────


class TestUpdateMember:
    def test_sem_token_retorna_401(self):
        response = client.patch(
            f"/projects/{_PROJECT_ID}/members/{_USER_ID}",
            json={"status": "suspended"},
        )
        assert response.status_code == 401

    def test_membership_inexistente_retorna_404(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.membership_service.firestore") as mock_fs:
                with patch("app.services.membership_service.auth"):
                    mock_fs.client.return_value = _mock_db(doc_data=None)
                    response = client.patch(
                        f"/projects/{_PROJECT_ID}/members/{_USER_ID}",
                        json={"status": "suspended"},
                        headers=_headers(_PROJECT_ID),
                    )
        assert response.status_code == 404

    def test_atualiza_status(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.membership_service.firestore") as mock_fs:
                with patch("app.services.membership_service.auth") as mock_auth:
                    mock_fs.client.return_value = _mock_db(
                        doc_data=_STORED_MEMBERSHIP
                    )
                    response = client.patch(
                        f"/projects/{_PROJECT_ID}/members/{_USER_ID}",
                        json={"status": "suspended"},
                        headers=_headers(_PROJECT_ID),
                    )
        assert response.status_code == 200
        assert response.json()["status"] == "suspended"
        mock_auth.set_custom_user_claims.assert_called_once()

    def test_atualiza_roles(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.membership_service.firestore") as mock_fs:
                with patch("app.services.membership_service.auth") as mock_auth:
                    mock_fs.client.return_value = _mock_db(
                        doc_data=_STORED_MEMBERSHIP
                    )
                    response = client.patch(
                        f"/projects/{_PROJECT_ID}/members/{_USER_ID}",
                        json={"roles": ["teacher", "instructor"]},
                        headers=_headers(_PROJECT_ID),
                    )
        assert response.status_code == 200
        assert response.json()["roles"] == ["teacher", "instructor"]
        mock_auth.set_custom_user_claims.assert_called_once()

    def test_claims_sincronizadas_com_todos_projetos(self):
        """_sync_claims deve incluir memberships de outros projetos do usuário."""
        other_membership = {
            "projectId": "outro-projeto",
            "userId": _USER_ID,
            "roles": ["student"],
            "status": "active",
            "joined_at": "2025-01-01T00:00:00+00:00",
        }
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, ["owner"]),
        ):
            with patch("app.services.membership_service.firestore") as mock_fs:
                with patch("app.services.membership_service.auth") as mock_auth:
                    mock_fs.client.return_value = _mock_db(
                        doc_data=_STORED_MEMBERSHIP,
                        stream_docs=[_STORED_MEMBERSHIP, other_membership],
                    )
                    client.patch(
                        f"/projects/{_PROJECT_ID}/members/{_USER_ID}",
                        json={"roles": ["teacher"]},
                        headers=_headers(_PROJECT_ID),
                    )
        claims_arg = mock_auth.set_custom_user_claims.call_args[0][1]
        assert _PROJECT_ID in claims_arg["projects"]
        assert "outro-projeto" in claims_arg["projects"]
