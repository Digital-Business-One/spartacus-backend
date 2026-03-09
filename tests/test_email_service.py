import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app)


class TestEmailService:
    def test_send_chama_sdk(self):
        with patch("app.services.email_service.MailerSendClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            from app.services.email_service import EmailService

            EmailService().send("destino@example.com", "Assunto", "<p>Corpo</p>")

            mock_client_cls.assert_called_once()
            mock_client.emails.send.assert_called_once()

    def test_send_constroi_email_com_destinatario_correto(self):
        with patch("app.services.email_service.MailerSendClient") as mock_client_cls:
            with patch("app.services.email_service.EmailBuilder") as mock_builder_cls:
                mock_builder = MagicMock()
                mock_builder.from_email.return_value = mock_builder
                mock_builder.to.return_value = mock_builder
                mock_builder.subject.return_value = mock_builder
                mock_builder.html.return_value = mock_builder
                mock_builder_cls.return_value = mock_builder
                mock_client_cls.return_value = MagicMock()

                from app.services.email_service import EmailService

                EmailService().send("destino@example.com", "Assunto", "<p>Corpo</p>")

                mock_builder.to.assert_called_once_with("destino@example.com")
                mock_builder.subject.assert_called_once_with("Assunto")
                mock_builder.html.assert_called_once_with("<p>Corpo</p>")


_PROJECT_ID = "test-project"
_VALID_CLAIMS = {"uid": "user-123", "email": "test@test.com", "projects": {_PROJECT_ID: ["owner"]}}
_AUTH_HEADER = {"Authorization": "Bearer test-token", "X-Project-Id": _PROJECT_ID}


class TestEndpointEmailTest:
    def test_retorna_403_fora_de_dev(self):
        env = {k: v for k, v in os.environ.items() if k != "APP_ENV"}
        with patch("app.security.middleware.verify_id_token", return_value=_VALID_CLAIMS):
            with patch.dict(os.environ, env, clear=True):
                response = client.post(
                    "/internal/email/test?to=test@example.com", headers=_AUTH_HEADER
                )
        assert response.status_code == 403

    def test_retorna_200_em_dev_com_email_mockado(self):
        with patch("app.security.middleware.verify_id_token", return_value=_VALID_CLAIMS):
            with patch("app.routers.internal.EmailService") as mock_cls:
                mock_cls.return_value = MagicMock()
                with patch.dict(os.environ, {"APP_ENV": "development"}):
                    response = client.post(
                        "/internal/email/test?to=test@example.com", headers=_AUTH_HEADER
                    )
        assert response.status_code == 200
        assert response.json() == {"status": "sent", "to": "test@example.com"}
        mock_cls.return_value.send.assert_called_once()
