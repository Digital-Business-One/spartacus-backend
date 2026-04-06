import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app)


class TestFirestoreEventStore:
    def test_publish_writes_to_events_collection(self):
        mock_db = MagicMock()
        os.environ["FIRESTORE_EMULATOR_HOST"] = "localhost:8080"
        with patch(
            "app.events.adapters.firestore.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db

            from app.events.adapters.firestore import FirestoreEventStore
            from app.events.models import DomainEvent, AccountNotificationPayload

            event = DomainEvent(
                id="test.event",
                payload=AccountNotificationPayload(
                    to="destino@example.com",
                    name="Test",
                    title="Titulo",
                    message="Mensagem",
                ),
            )
            FirestoreEventStore().publish(
                event, project_id="test-project", source="test_service"
            )

            mock_db.collection.assert_called_once_with("events")
            doc = mock_db.collection.return_value.add.call_args[0][0]
            assert doc["eventId"] == "test.event"
            assert doc["projectId"] == "test-project"
            assert doc["source"] == "test_service"
            assert doc["status"] == "pending"
            assert doc["payload"]["name"] == "Test"


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
