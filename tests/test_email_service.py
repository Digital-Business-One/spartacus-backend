import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app)


class TestMailerSendAdapter:
    def test_send_chama_sdk(self):
        with patch(
            "app.notifications.adapters.mailersend.MailerSendClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            with patch(
                "app.notifications.adapters.mailersend.EmailBuilder"
            ) as mock_builder_cls:
                mock_builder = MagicMock()
                mock_builder.from_email.return_value = mock_builder
                mock_builder.to.return_value = mock_builder
                mock_builder.template.return_value = mock_builder
                mock_builder.personalize.return_value = mock_builder
                mock_builder_cls.return_value = mock_builder

                from app.notifications.adapters.mailersend import (
                    MailerSendAdapter,
                )

                MailerSendAdapter().send(
                    event_id="test.event",
                    template_id="tmpl-123",
                    to="destino@example.com",
                    data={"name": "Test"},
                )

                mock_client_cls.assert_called_once()
                mock_client.emails.send.assert_called_once()
                mock_builder.template.assert_called_once_with("tmpl-123")
                mock_builder.personalize.assert_called_once_with(
                    "destino@example.com", name="Test"
                )


_PROJECT_ID = "test-project"
_VALID_CLAIMS = {
    "uid": "user-123",
    "email": "test@test.com",
    "projects": {_PROJECT_ID: ["owner"]},
}
_AUTH_HEADER = {
    "Authorization": "Bearer test-token",
    "X-Project-Id": _PROJECT_ID,
}


class TestEndpointEmailTest:
    def test_retorna_403_fora_de_dev(self):
        env = {k: v for k, v in os.environ.items() if k != "APP_ENV"}
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_VALID_CLAIMS,
        ):
            with patch.dict(os.environ, env, clear=True):
                response = client.post(
                    "/internal/email/test?to=test@example.com",
                    headers=_AUTH_HEADER,
                )
        assert response.status_code == 403

    def test_retorna_200_em_dev_com_email_mockado(self):
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_VALID_CLAIMS,
        ):
            with patch(
                "app.routers.internal.MailerSendClient"
            ) as mock_client_cls:
                mock_client_cls.return_value = MagicMock()
                with patch.dict(os.environ, {"APP_ENV": "development"}):
                    response = client.post(
                        "/internal/email/test?to=test@example.com",
                        headers=_AUTH_HEADER,
                    )
        assert response.status_code == 200
        assert response.json() == {
            "status": "sent",
            "to": "test@example.com",
        }
        mock_client_cls.return_value.emails.send.assert_called_once()
