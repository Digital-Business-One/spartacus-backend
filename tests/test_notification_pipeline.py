"""
Teste de integração do pipeline de notificação.

Exercita a cadeia completa: DomainEvent → Dispatcher → FirestoreMailAdapter
O Firestore é mockado — valida que o documento correto é escrito na collection 'mail'.

Usa payloads realistas (iguais aos de produção) para garantir que mudanças
nos dados não quebrem silenciosamente.
"""

from unittest.mock import MagicMock, patch

from app.notifications.adapters.firestore_mail import FirestoreMailAdapter
from app.notifications.dispatcher import NotificationDispatcher
from app.notifications.models import (
    AccountReceivedPayload,
    DomainEvent,
    ResendVerificationPayload,
    SignupEmailPayload,
)


def _make_dispatcher_and_db():
    mock_db = MagicMock()
    with patch(
        "app.notifications.adapters.firestore_mail.firestore"
    ) as mock_fs:
        mock_fs.client.return_value = mock_db
        adapter = FirestoreMailAdapter()
        dispatcher = NotificationDispatcher(port=adapter)
    return dispatcher, mock_db, mock_fs


class TestSignupEmailConfirmation:
    """Pipeline completo para signup.email_confirmation com payload realista."""

    def _event(self):
        return DomainEvent(
            id="signup.email_confirmation",
            payload=SignupEmailPayload(
                uid="uid-123",
                status="pending_email",
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

    def test_dispatch_writes_mail_document(self):
        mock_db = MagicMock()
        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            adapter = FirestoreMailAdapter()
            dispatcher = NotificationDispatcher(port=adapter)
            dispatcher.dispatch(self._event())

        mock_db.collection.assert_called_once_with("emails")
        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["template_id"] == "v69oxl59w12g785k"
        assert doc["to"] == [{"email": "carlos@email.com"}]
        assert doc["from"]["email"] == "noreply@spartacus.app.br"
        assert doc["tags"] == ["signup.email_confirmation"]
        p = doc["personalization"][0]
        assert p["email"] == "carlos@email.com"
        assert p["data"]["name"] == "Carlos Eduardo da Silva"
        assert p["data"]["phone"] == "(65) 99887-6543"
        assert p["data"]["link"] == "https://example.com/verify?code=123456"

    def test_dispatch_with_dependents(self):
        mock_db = MagicMock()
        event = DomainEvent(
            id="signup.email_confirmation",
            payload=SignupEmailPayload(
                uid="uid-456",
                status="pending_email",
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
                    {
                        "name": "Pedro",
                        "age": "8 anos",
                        "classes": "Capoeira — Seg/Qua 17h",
                    }
                ],
            ),
        )

        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            adapter = FirestoreMailAdapter()
            dispatcher = NotificationDispatcher(port=adapter)
            dispatcher.dispatch(event)

        doc = mock_db.collection.return_value.add.call_args[0][0]
        data = doc["personalization"][0]["data"]
        assert data["show_dependents"] is True
        assert data["dependents"][0]["name"] == "Pedro"


class TestAccountReceived:
    """Pipeline completo para signup.account_received."""

    def test_dispatch_writes_account_received(self):
        mock_db = MagicMock()
        event = DomainEvent(
            id="signup.account_received",
            payload=AccountReceivedPayload(
                to="carlos@email.com",
                name="Carlos Eduardo da Silva",
            ),
        )

        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            adapter = FirestoreMailAdapter()
            dispatcher = NotificationDispatcher(port=adapter)
            dispatcher.dispatch(event)

        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["template_id"] == "z86org8zknegew13"
        assert doc["personalization"][0]["data"]["name"] == "Carlos Eduardo da Silva"


class TestResendVerification:
    """Pipeline completo para signup.resend_verification."""

    def test_dispatch_writes_resend_email(self):
        mock_db = MagicMock()
        event = DomainEvent(
            id="signup.resend_verification",
            payload=ResendVerificationPayload(
                to="joao@email.com",
                name="João Silva",
                link="https://example.com/verify?code=abc",
            ),
        )

        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            adapter = FirestoreMailAdapter()
            dispatcher = NotificationDispatcher(port=adapter)
            dispatcher.dispatch(event)

        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["template_id"] == "pxkjn41w1j5lz781"
        assert doc["personalization"][0]["data"]["name"] == "João Silva"
        assert doc["personalization"][0]["data"]["link"] == (
            "https://example.com/verify?code=abc"
        )


class TestUnregisteredEvent:
    """Evento sem template registrado deve ser ignorado."""

    def test_unknown_event_skips_write(self):
        mock_db = MagicMock()
        event = DomainEvent(
            id="signup.google_completed",
            payload=MagicMock(spec=[]),
        )

        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            adapter = FirestoreMailAdapter()
            dispatcher = NotificationDispatcher(port=adapter)
            dispatcher.dispatch(event)

        mock_db.collection.return_value.add.assert_not_called()
