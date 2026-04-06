"""
Integration test for the event publishing pipeline.

Exercises the full chain: DomainEvent → EventPublisher → FirestoreEventStore
Firestore is mocked — validates that the correct data is written to `events`.
"""

from unittest.mock import MagicMock, patch

from app.events.adapters.firestore import FirestoreEventStore
from app.events.publisher import EventPublisher
from app.events.models import (
    AccountNotificationPayload,
    DomainEvent,
    ResendVerificationPayload,
    SignupEmailPayload,
)


def _make_publisher():
    """Create publisher with mocked Firestore."""
    mock_db = MagicMock()
    with patch("app.events.adapters.firestore.firestore") as mock_fs:
        mock_fs.client.return_value = mock_db
        store = FirestoreEventStore()
        pub = EventPublisher(port=store)
    return pub, mock_db, mock_fs


class TestSignupEmailConfirmation:
    def test_publish_writes_event(self):
        pub, mock_db, _ = _make_publisher()
        event = DomainEvent(
            id="signup.email_confirmation",
            payload=SignupEmailPayload(
                uid="uid-123",
                status="pending_approval",
                to="carlos@email.com",
                name="Carlos Eduardo da Silva",
                email="carlos@email.com",
                phone="(65) 99887-6543",
                roles_label="Professor",
                link="https://example.com/verify?code=123456",
                show_classes=True,
                classes=[{"name": "Jiu Jitsu — Ter/Qui 18h"}],
                show_dependents=False,
                dependents=[],
            ),
        )

        with patch("app.events.adapters.firestore.firestore") as mock_fs:
            mock_fs.client.return_value = mock_db
            pub.publish(event, project_id="spartacus", source="auth_service")

        mock_db.collection.assert_called_with("events")
        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["eventId"] == "signup.email_confirmation"
        assert doc["projectId"] == "spartacus"
        assert doc["source"] == "auth_service"
        assert doc["status"] == "pending"
        assert doc["payload"]["name"] == "Carlos Eduardo da Silva"
        assert doc["payload"]["show_link"] is True

    def test_publish_with_dependents(self):
        pub, mock_db, _ = _make_publisher()
        event = DomainEvent(
            id="signup.email_confirmation",
            payload=SignupEmailPayload(
                uid="uid-456",
                status="pending_approval",
                to="maria@email.com",
                name="Maria Aparecida",
                email="maria@email.com",
                phone="(65) 99111-2222",
                roles_label="Responsável, Aluno",
                link="https://example.com/verify?code=789",
                show_classes=True,
                classes=[{"name": "Capoeira — Seg/Qua 17h"}],
                show_dependents=True,
                dependents=[
                    {"name": "Pedro", "age": "8 anos", "classes": "Capoeira — Seg/Qua 17h"}
                ],
            ),
        )

        with patch("app.events.adapters.firestore.firestore") as mock_fs:
            mock_fs.client.return_value = mock_db
            pub.publish(event, project_id="spartacus", source="auth_service")

        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["payload"]["show_dependents"] is True
        assert doc["payload"]["dependents"][0]["name"] == "Pedro"


class TestAccountNotification:
    def test_publish_approve(self):
        pub, mock_db, _ = _make_publisher()
        event = DomainEvent(
            id="account.approve",
            payload=AccountNotificationPayload(
                to="user@email.com",
                name="User Test",
                title="Cadastro aprovado!",
                message="Parabéns!",
                cta_text="Acessar",
                cta_url="https://spartacus.app.br",
            ),
        )

        with patch("app.events.adapters.firestore.firestore") as mock_fs:
            mock_fs.client.return_value = mock_db
            pub.publish(
                event, project_id="spartacus", source="account_service"
            )

        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["eventId"] == "account.approve"
        assert doc["source"] == "account_service"
        assert doc["payload"]["title"] == "Cadastro aprovado!"
        assert doc["payload"]["cta_text"] == "Acessar"


class TestResendVerification:
    def test_publish_resend(self):
        pub, mock_db, _ = _make_publisher()
        event = DomainEvent(
            id="signup.resend_verification",
            payload=ResendVerificationPayload(
                to="joao@email.com",
                name="João Silva",
                link="https://example.com/verify?code=abc",
            ),
        )

        with patch("app.events.adapters.firestore.firestore") as mock_fs:
            mock_fs.client.return_value = mock_db
            pub.publish(event, project_id="spartacus", source="auth_service")

        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["payload"]["name"] == "João Silva"
        assert doc["payload"]["link"] == "https://example.com/verify?code=abc"


class TestUnregisteredEvent:
    def test_unknown_event_still_published(self):
        """Events without rules are still persisted in the events collection."""
        pub, mock_db, _ = _make_publisher()
        event = DomainEvent(
            id="signup.email_verified",
            payload=AccountNotificationPayload(
                to="user@email.com",
                name="User",
                title="E-mail verificado",
                message="Ok",
            ),
        )

        with patch("app.events.adapters.firestore.firestore") as mock_fs:
            mock_fs.client.return_value = mock_db
            pub.publish(event, project_id="spartacus", source="auth_service")

        mock_db.collection.assert_called_with("events")
        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["eventId"] == "signup.email_verified"
        assert doc["status"] == "pending"
