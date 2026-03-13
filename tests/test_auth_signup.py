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
    mock_db.get_all.return_value = []
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
            patch("app.routers.auth.dispatcher") as mock_disp,
        ):
            mock_fs.client.return_value = _mock_db()
            mock_auth.create_user.return_value = _mock_auth_create()
            mock_auth.generate_email_verification_link.return_value = "http://verify"
            response = client.post(
                "/auth/signup",
                json=_BASE_PAYLOAD,
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["uid"] == "uid-123"
        assert body["status"] == "pending_email"
        mock_disp.dispatch.assert_called_once()
        event = mock_disp.dispatch.call_args[0][0]
        assert event.id == "signup.email_confirmation"

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
            patch("app.routers.auth.dispatcher") as mock_disp,
        ):
            mock_fs.client.return_value = _mock_db()
            mock_auth.create_user.return_value = _mock_auth_create()
            mock_auth.generate_email_verification_link.return_value = "http://verify"
            response = client.post(
                "/auth/signup",
                json=payload,
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 201
        assert response.json()["status"] == "pending_email"
        event = mock_disp.dispatch.call_args[0][0]
        assert event.payload.show_dependents is True
        assert len(event.payload.dependents) == 1
        assert event.payload.dependents[0]["name"] == "Maria Silva"
        assert "ano" in event.payload.dependents[0]["age"]

    def test_role_invalida_retorna_422(self):
        payload = {**_BASE_PAYLOAD, "roles": ["admin"]}
        response = client.post(
            "/auth/signup",
            json=payload,
            headers={"X-Project-Id": _PROJECT_ID},
        )
        assert response.status_code == 422

    def test_guardian_aluno_com_turmas_busca_nomes(self):
        """RN-05 and RN-06: ages calculated, class names fetched."""
        payload = {
            **_BASE_PAYLOAD,
            "roles": ["guardian", "student"],
            "classIds": ["class-jj"],
            "dependents": [
                {
                    "id": "dep-1",
                    "name": "Pedro Silva",
                    "birthDate": "15/06/2014",
                    "taxId": None,
                    "classIds": ["class-cap"],
                }
            ],
        }
        mock_class_jj = MagicMock()
        mock_class_jj.exists = True
        mock_class_jj.id = "class-jj"
        mock_class_jj.to_dict.return_value = {"name": "Jiu Jitsu — Ter/Qui 18h"}

        mock_class_cap = MagicMock()
        mock_class_cap.exists = True
        mock_class_cap.id = "class-cap"
        mock_class_cap.to_dict.return_value = {"name": "Capoeira — Seg/Qua 17h"}

        mock_db = _mock_db()
        mock_db.get_all.return_value = [mock_class_jj, mock_class_cap]

        with (
            patch("app.services.auth_service.firestore") as mock_fs,
            patch("app.services.auth_service.auth") as mock_auth,
            patch("app.routers.auth.dispatcher") as mock_disp,
        ):
            mock_fs.client.return_value = mock_db
            mock_auth.create_user.return_value = _mock_auth_create()
            mock_auth.generate_email_verification_link.return_value = "http://verify"
            response = client.post(
                "/auth/signup",
                json=payload,
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 201
        event = mock_disp.dispatch.call_args[0][0]
        p = event.payload
        assert p.show_classes is True
        assert p.show_dependents is True
        assert p.classes == [{"name": "Jiu Jitsu — Ter/Qui 18h"}]
        assert p.dependents[0]["name"] == "Pedro Silva"
        assert "ano" in p.dependents[0]["age"]
        assert p.dependents[0]["classes"] == "Capoeira — Seg/Qua 17h"
        assert p.roles_label == "Responsável, Aluno"


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
            patch("app.routers.auth.dispatcher") as mock_disp,
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
        body = response.json()
        assert body["uid"] == "google-uid-456"
        assert body["status"] == "pending_approval"
        event = mock_disp.dispatch.call_args[0][0]
        assert event.id == "signup.google_completed"


class TestEmailVerified:
    _CLAIMS = {"uid": "uid-123", "email": "joao@example.com", "projects": {"spartacus": []}}

    def test_sem_token_retorna_401(self):
        response = client.post(
            "/auth/email-verified",
            headers={"X-Project-Id": _PROJECT_ID},
        )
        assert response.status_code == 401

    def test_email_nao_verificado_no_firebase_retorna_400(self):
        mock_record = MagicMock()
        mock_record.email_verified = False
        with (
            patch("app.security.middleware.verify_id_token", return_value=self._CLAIMS),
            patch("app.services.auth_service.auth") as mock_auth,
            patch("app.routers.auth.dispatcher"),
        ):
            mock_auth.get_user.return_value = mock_record
            response = client.post(
                "/auth/email-verified",
                headers={
                    "X-Project-Id": _PROJECT_ID,
                    "Authorization": "Bearer valid-token",
                },
            )
        assert response.status_code == 400

    def test_confirma_email_verificado_retorna_200(self):
        mock_record = MagicMock()
        mock_record.email_verified = True

        mock_user_doc = MagicMock()
        mock_user_doc.exists = True
        mock_user_doc.to_dict.return_value = {
            "approvalStatus": "pending_email",
            "name": "João Silva",
        }

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value.get.return_value = mock_user_doc
        mock_db.collection.return_value.document.return_value.update.return_value = None

        with (
            patch("app.security.middleware.verify_id_token", return_value=self._CLAIMS),
            patch("app.services.auth_service.auth") as mock_auth,
            patch("app.services.auth_service.firestore") as mock_fs,
            patch("app.routers.auth.dispatcher") as mock_disp,
        ):
            mock_auth.get_user.return_value = mock_record
            mock_fs.client.return_value = mock_db
            response = client.post(
                "/auth/email-verified",
                headers={
                    "X-Project-Id": _PROJECT_ID,
                    "Authorization": "Bearer valid-token",
                },
            )
        assert response.status_code == 200
        assert response.json()["status"] == "pending_approval"
        mock_disp.dispatch.assert_called_once()
        event = mock_disp.dispatch.call_args[0][0]
        assert event.id == "signup.account_received"
        assert event.payload.name == "João Silva"

    def test_ja_aprovado_nao_reverte_retorna_200(self):
        mock_record = MagicMock()
        mock_record.email_verified = True

        mock_user_doc = MagicMock()
        mock_user_doc.exists = True
        mock_user_doc.to_dict.return_value = {"approvalStatus": "pending_approval"}

        mock_db = MagicMock()
        mock_db.collection.return_value.document.return_value.get.return_value = mock_user_doc

        with (
            patch("app.security.middleware.verify_id_token", return_value=self._CLAIMS),
            patch("app.services.auth_service.auth") as mock_auth,
            patch("app.services.auth_service.firestore") as mock_fs,
            patch("app.routers.auth.dispatcher") as mock_disp,
        ):
            mock_auth.get_user.return_value = mock_record
            mock_fs.client.return_value = mock_db
            response = client.post(
                "/auth/email-verified",
                headers={
                    "X-Project-Id": _PROJECT_ID,
                    "Authorization": "Bearer valid-token",
                },
            )
        assert response.status_code == 200
        mock_db.collection.return_value.document.return_value.update.assert_not_called()
        mock_disp.dispatch.assert_not_called()


class TestResendVerification:
    def test_reenvio_bem_sucedido_retorna_200(self):
        mock_user_doc = MagicMock()
        mock_user_doc.to_dict.return_value = {
            "approvalStatus": "pending_email",
            "name": "João Silva",
        }

        mock_db = MagicMock()
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = iter(
            [mock_user_doc]
        )

        with (
            patch("app.services.auth_service.firestore") as mock_fs,
            patch("app.services.auth_service.auth") as mock_auth,
            patch("app.routers.auth.dispatcher") as mock_disp,
        ):
            mock_fs.client.return_value = mock_db
            mock_auth.generate_email_verification_link.return_value = "http://verify"
            response = client.post(
                "/auth/resend-verification",
                json={"email": "joao@example.com"},
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        mock_disp.dispatch.assert_called_once()
        event = mock_disp.dispatch.call_args[0][0]
        assert event.id == "signup.resend_verification"

    def test_email_nao_encontrado_retorna_200_silencioso(self):
        mock_db = MagicMock()
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = iter(
            []
        )

        with (
            patch("app.services.auth_service.firestore") as mock_fs,
            patch("app.routers.auth.dispatcher") as mock_disp,
        ):
            mock_fs.client.return_value = mock_db
            response = client.post(
                "/auth/resend-verification",
                json={"email": "naoexiste@example.com"},
                headers={"X-Project-Id": _PROJECT_ID},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        mock_disp.dispatch.assert_not_called()
