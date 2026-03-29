"""
Teste de integração do pipeline de notificação.

Exercita a cadeia completa: DomainEvent → Dispatcher → NotificationAdapter
O Firestore e PubSub são mockados — valida que os dados corretos são escritos.
"""

import os
from unittest.mock import MagicMock, patch

from app.notifications.adapters.firestore_mail import NotificationAdapter
from app.notifications.dispatcher import NotificationDispatcher
from app.notifications.models import (
    AccountNotificationPayload,
    DomainEvent,
    ResendVerificationPayload,
    SignupEmailPayload,
)


def _make_dispatcher():
    """Create dispatcher with mocked Firestore (PubSub skipped in dev)."""
    mock_db = MagicMock()
    os.environ["FIRESTORE_EMULATOR_HOST"] = "localhost:8080"
    with patch(
        "app.notifications.adapters.firestore_mail.firestore"
    ) as mock_fs:
        mock_fs.client.return_value = mock_db
        adapter = NotificationAdapter()
        dispatcher = NotificationDispatcher(port=adapter)
    return dispatcher, mock_db, mock_fs


class TestSignupEmailConfirmation:
    def test_dispatch_writes_notification(self):
        dispatcher, mock_db, mock_fs = _make_dispatcher()
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

        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            dispatcher.dispatch(event)

        mock_db.collection.assert_called_with("notifications")
        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["template_id"] == "TBD_SIGNUP_WELCOME"
        assert doc["subject"] == "Confirme seu e-mail — Spartacus"
        assert doc["to"] == "carlos@email.com"
        assert doc["data"]["name"] == "Carlos Eduardo da Silva"
        assert doc["data"]["show_link"] is True

    def test_dispatch_with_dependents(self):
        dispatcher, mock_db, mock_fs = _make_dispatcher()
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

        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            dispatcher.dispatch(event)

        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["data"]["show_dependents"] is True
        assert doc["data"]["dependents"][0]["name"] == "Pedro"


class TestAccountNotification:
    def test_dispatch_approve(self):
        dispatcher, mock_db, _ = _make_dispatcher()
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

        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            dispatcher.dispatch(event)

        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["template_id"] == "TBD_ACCOUNT_NOTIFICATION"
        assert doc["data"]["title"] == "Cadastro aprovado!"
        assert doc["data"]["cta_text"] == "Acessar"


class TestResendVerification:
    def test_dispatch_resend(self):
        dispatcher, mock_db, _ = _make_dispatcher()
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
            dispatcher.dispatch(event)

        doc = mock_db.collection.return_value.add.call_args[0][0]
        assert doc["data"]["name"] == "João Silva"
        assert doc["data"]["link"] == "https://example.com/verify?code=abc"


class TestUnregisteredEvent:
    def test_unknown_event_skips(self):
        dispatcher, mock_db, _ = _make_dispatcher()
        event = DomainEvent(
            id="some.unknown.event",
            payload=MagicMock(spec=[]),
        )

        with patch(
            "app.notifications.adapters.firestore_mail.firestore"
        ) as mock_fs:
            mock_fs.client.return_value = mock_db
            dispatcher.dispatch(event)

        mock_db.collection.return_value.add.assert_not_called()
