"""
Teste de integração do pipeline de notificação.

Exercita a cadeia completa: DomainEvent → Dispatcher → Adapter → EmailBuilder (real)
Só o MailerSendClient (HTTP) é mockado.

Esses testes usam payloads realistas (iguais aos de produção) para garantir
que mudanças no SDK ou nos dados não quebrem silenciosamente.
"""

from unittest.mock import MagicMock, patch

from app.notifications.adapters.mailersend import MailerSendAdapter
from app.notifications.dispatcher import NotificationDispatcher
from app.notifications.models import (
    AccountReceivedPayload,
    DomainEvent,
    ResendVerificationPayload,
    SignupEmailPayload,
)


def _make_dispatcher(mock_client):
    adapter = MailerSendAdapter()
    return NotificationDispatcher(port=adapter)


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

    @patch("app.notifications.adapters.mailersend.MailerSendClient")
    def test_dispatch_builds_valid_email_request(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        dispatcher = _make_dispatcher(mock_client)
        dispatcher.dispatch(self._event())

        mock_client.emails.send.assert_called_once()
        req = mock_client.emails.send.call_args[0][0]
        assert req.template_id == "v69oxl59w12g785k"
        assert req.to[0].email == "carlos@email.com"
        p = req.personalization[0]
        assert p.email == "carlos@email.com"
        assert p.data["email"] == "carlos@email.com"
        assert p.data["name"] == "Carlos Eduardo da Silva"
        assert p.data["phone"] == "(65) 99887-6543"
        assert p.data["link"] == "https://example.com/verify?code=123456"

    @patch("app.notifications.adapters.mailersend.MailerSendClient")
    def test_dispatch_with_dependents(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

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
                    {"name": "Pedro", "age": "8 anos", "classes": "Capoeira — Seg/Qua 17h"}
                ],
            ),
        )

        dispatcher = _make_dispatcher(mock_client)
        dispatcher.dispatch(event)

        req = mock_client.emails.send.call_args[0][0]
        data = req.personalization[0].data
        assert data["show_dependents"] is True
        assert data["dependents"][0]["name"] == "Pedro"


class TestAccountReceived:
    """Pipeline completo para signup.account_received."""

    @patch("app.notifications.adapters.mailersend.MailerSendClient")
    def test_dispatch_sends_account_received(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        event = DomainEvent(
            id="signup.account_received",
            payload=AccountReceivedPayload(
                to="carlos@email.com",
                name="Carlos Eduardo da Silva",
            ),
        )

        dispatcher = _make_dispatcher(mock_client)
        dispatcher.dispatch(event)

        req = mock_client.emails.send.call_args[0][0]
        assert req.template_id == "z86org8zknegew13"
        assert req.personalization[0].data["name"] == "Carlos Eduardo da Silva"


class TestResendVerification:
    """Pipeline completo para signup.resend_verification."""

    @patch("app.notifications.adapters.mailersend.MailerSendClient")
    def test_dispatch_sends_resend_email(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        event = DomainEvent(
            id="signup.resend_verification",
            payload=ResendVerificationPayload(
                to="joao@email.com",
                name="João Silva",
                link="https://example.com/verify?code=abc",
            ),
        )

        dispatcher = _make_dispatcher(mock_client)
        dispatcher.dispatch(event)

        req = mock_client.emails.send.call_args[0][0]
        assert req.template_id == "pxkjn41w1j5lz781"
        assert req.personalization[0].data["name"] == "João Silva"
        assert req.personalization[0].data["link"] == "https://example.com/verify?code=abc"


class TestUnregisteredEvent:
    """Evento sem template registrado deve ser ignorado."""

    @patch("app.notifications.adapters.mailersend.MailerSendClient")
    def test_unknown_event_skips_send(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        event = DomainEvent(
            id="signup.google_completed",
            payload=MagicMock(spec=[]),
        )

        dispatcher = _make_dispatcher(mock_client)
        dispatcher.dispatch(event)

        mock_client.emails.send.assert_not_called()
