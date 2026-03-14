import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app)


class TestMailerSendAdapter:
    def test_send_builds_email_and_calls_sdk(self):
        """Usa o EmailBuilder real para garantir compatibilidade com o SDK."""
        with patch(
            "app.notifications.adapters.mailersend.MailerSendClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            from app.notifications.adapters.mailersend import MailerSendAdapter

            MailerSendAdapter().send(
                event_id="test.event",
                template_id="tmpl-123",
                to="destino@example.com",
                data={"name": "Test"},
            )

            mock_client_cls.assert_called_once()
            mock_client.emails.send.assert_called_once()
            email_request = mock_client.emails.send.call_args[0][0]
            assert email_request.template_id == "tmpl-123"
            assert email_request.to[0].email == "destino@example.com"
            assert email_request.personalization[0].email == "destino@example.com"
            assert email_request.personalization[0].data == {"name": "Test"}


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
