from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PROJECT_ID = "spartacus"

_BASE_PAYLOAD = {
    "authMethod": "email",
    "email": "joao@example.com",
    "password": "senha1234",
    "name": "João Silva",
    "birthDate": "01/01/1990",
    "taxId": "111.444.777-35",
    "phone": "65999990000",
    "whatsapp": "65999990000",
    "postalCode": "78350-000",
    "street": "Rua Rotary Internacional",
    "number": "270",
    "complement": None,
    "neighborhood": "Centro",
    "city": "Brasnorte",
    "state": "MT",
    "roles": ["student"],
    "dependents": [],
    "classIds": [],
}


def _mock_db(email_exists: bool = False, cpf_exists: bool = False):
    """Build a Firestore client mock for duplicate checks and writes."""
    mock_db = MagicMock()

    def collection_side_effect(name):
        mock_col = MagicMock()

        def where_side_effect(field, op, value):
            mock_query = MagicMock()
            results = []
            if field == "email" and email_exists:
                results = [MagicMock()]
            if field == "taxId" and cpf_exists:
                results = [MagicMock()]
            mock_query.limit.return_value.stream.return_value = iter(results)
            return mock_query

        mock_col.where.side_effect = where_side_effect
        mock_col.document.return_value.set.return_value = None
        return mock_col

    mock_db.collection.side_effect = collection_side_effect
    return mock_db


def _mock_auth_create(uid: str = "uid-123"):
    record = MagicMock()
    record.uid = uid
    return record


class TestSignupEmail:
    def test_criacao_bem_sucedida_retorna_201(self):
        with (
            patch("app.services.auth_service.firestore") as mock_fs,
            patch("app.services.auth_service.auth") as mock_auth,
        ):
            mock_fs.client.return_value = _mock_db()
            mock_auth.create_user.return_value = _mock_auth_create()
            response = client.post(
                "/auth/signup",
                json=_BASE_PAYLOAD,
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["uid"] == "uid-123"
        assert body["status"] == "pending_approval"

    def test_sem_x_project_id_retorna_422(self):
        response = client.post("/auth/signup", json=_BASE_PAYLOAD)
        assert response.status_code == 422

    def test_email_duplicado_retorna_409(self):
        with (
            patch("app.services.auth_service.firestore") as mock_fs,
        ):
            mock_fs.client.return_value = _mock_db(email_exists=True)
            response = client.post(
                "/auth/signup",
                json=_BASE_PAYLOAD,
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 409
        assert "Email" in response.json()["detail"]

    def test_cpf_duplicado_retorna_409(self):
        with (
            patch("app.services.auth_service.firestore") as mock_fs,
        ):
            mock_fs.client.return_value = _mock_db(cpf_exists=True)
            response = client.post(
                "/auth/signup",
                json=_BASE_PAYLOAD,
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 409
        assert "CPF" in response.json()["detail"]

    def test_sem_password_retorna_422(self):
        payload = {**_BASE_PAYLOAD, "password": None}
        response = client.post(
            "/auth/signup",
            json=payload,
            headers={"X-Project-Id": _PROJECT_ID},
        )
        assert response.status_code == 422

    def test_cpf_invalido_retorna_422(self):
        payload = {**_BASE_PAYLOAD, "taxId": "111.111.111-11"}
        response = client.post(
            "/auth/signup",
            json=payload,
            headers={"X-Project-Id": _PROJECT_ID},
        )
        assert response.status_code == 422

    def test_menor_de_16_anos_retorna_422(self):
        payload = {**_BASE_PAYLOAD, "birthDate": "01/01/2015"}
        response = client.post(
            "/auth/signup",
            json=payload,
            headers={"X-Project-Id": _PROJECT_ID},
        )
        assert response.status_code == 422

    def test_guardian_sem_dependentes_retorna_422(self):
        payload = {**_BASE_PAYLOAD, "roles": ["guardian"], "dependents": []}
        response = client.post(
            "/auth/signup",
            json=payload,
            headers={"X-Project-Id": _PROJECT_ID},
        )
        assert response.status_code == 422

    def test_guardian_com_dependente_retorna_201(self):
        payload = {
            **_BASE_PAYLOAD,
            "roles": ["guardian"],
            "dependents": [
                {
                    "id": "dep-1",
                    "name": "Maria Silva",
                    "birthDate": "10/05/2018",
                    "taxId": None,
                    "classIds": [],
                }
            ],
        }
        with (
            patch("app.services.auth_service.firestore") as mock_fs,
            patch("app.services.auth_service.auth") as mock_auth,
        ):
            mock_fs.client.return_value = _mock_db()
            mock_auth.create_user.return_value = _mock_auth_create()
            response = client.post(
                "/auth/signup",
                json=payload,
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 201

    def test_role_invalida_retorna_422(self):
        payload = {**_BASE_PAYLOAD, "roles": ["admin"]}
        response = client.post(
            "/auth/signup",
            json=payload,
            headers={"X-Project-Id": _PROJECT_ID},
        )
        assert response.status_code == 422


class TestSignupGoogle:
    _GOOGLE_PAYLOAD = {**_BASE_PAYLOAD, "authMethod": "google", "password": None}

    def test_sem_token_retorna_401(self):
        response = client.post(
            "/auth/signup",
            json=self._GOOGLE_PAYLOAD,
            headers={"X-Project-Id": _PROJECT_ID},
        )
        assert response.status_code == 401

    def test_token_invalido_retorna_401(self):
        with patch("app.routers.auth.verify_id_token", side_effect=Exception("invalid")):
            response = client.post(
                "/auth/signup",
                json=self._GOOGLE_PAYLOAD,
                headers={
                    "X-Project-Id": _PROJECT_ID,
                    "Authorization": "Bearer token-invalido",
                },
            )
        assert response.status_code == 401

    def test_google_signup_bem_sucedido_retorna_201(self):
        with (
            patch("app.routers.auth.verify_id_token", return_value={"uid": "google-uid-456"}),
            patch("app.services.auth_service.firestore") as mock_fs,
        ):
            mock_fs.client.return_value = _mock_db()
            response = client.post(
                "/auth/signup",
                json=self._GOOGLE_PAYLOAD,
                headers={
                    "X-Project-Id": _PROJECT_ID,
                    "Authorization": "Bearer valid-google-token",
                },
            )
        assert response.status_code == 201
        assert response.json()["uid"] == "google-uid-456"
