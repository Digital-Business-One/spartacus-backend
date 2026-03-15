import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app)


class TestFirestoreMailAdapter:
    def test_send_writes_document_to_mail_collection(self):
        mock_db = MagicMock()
        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db

            from app.notifications.adapters.firestore_mail import (
                FirestoreMailAdapter,
            )

            FirestoreMailAdapter().send(
                event_id="test.event",
                template_id="tmpl-123",
                to="destino@example.com",
                data={"name": "Test"},
            )

            mock_db.collection.assert_called_once_with("emails")
            doc = mock_db.collection.return_value.add.call_args[0][0]
            assert doc["template_id"] == "tmpl-123"
            assert doc["to"] == [{"email": "destino@example.com"}]
            assert doc["from"]["email"] == "noreply@spartacus.app.br"
            assert doc["personalization"] == [
                {"email": "destino@example.com", "data": {"name": "Test"}}
            ]
            assert doc["tags"] == ["test.event"]

    def test_send_with_email_key_in_data(self):
        mock_db = MagicMock()
        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db

            from app.notifications.adapters.firestore_mail import (
                FirestoreMailAdapter,
            )

            FirestoreMailAdapter().send(
                event_id="signup.email_confirmation",
                template_id="tmpl-456",
                to="carlos@email.com",
                data={
                    "name": "Carlos",
                    "email": "carlos@email.com",
                    "phone": "(65) 99887-6543",
                },
            )

            doc = mock_db.collection.return_value.add.call_args[0][0]
            assert doc["personalization"][0]["data"]["email"] == "carlos@email.com"
            assert doc["personalization"][0]["email"] == "carlos@email.com"


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

    def test_retorna_200_em_dev_com_firestore_mockado(self):
        mock_db = MagicMock()
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_VALID_CLAIMS,
        ):
            with patch(
                "app.routers.internal.firestore"
            ) as mock_fs:
                mock_fs.client.return_value = mock_db
                with patch.dict(os.environ, {"APP_ENV": "development"}):
                    response = client.post(
                        "/internal/email/test?to=test@example.com",
                        headers=_AUTH_HEADER,
                    )
        assert response.status_code == 200
        assert response.json() == {
            "status": "queued",
            "to": "test@example.com",
        }
        mock_db.collection.assert_called_once_with("emails")
        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["to"] == [{"email": "test@example.com"}]
        assert "html" in doc
