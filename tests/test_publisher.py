"""Tests for EventPublisher — publishing must be resilient and observable.

A publish failure is a post-commit side-effect miss: it must never raise
(which would undo the already-committed primary operation) and must never be
reported as success. The publisher retries transient failures and returns a
truthful boolean.
"""
from unittest.mock import MagicMock, patch

from app.events.models import DomainEvent
from app.events.publisher import EventPublisher


class _Payload:
    def personalization(self):
        return {}


def _event():
    ev = MagicMock(spec=DomainEvent)
    ev.id = "signup.account_created"
    ev.payload = _Payload()
    return ev


class TestPublish:
    def test_returns_true_on_success(self):
        port = MagicMock()
        pub = EventPublisher(port=port)
        assert pub.publish(_event(), project_id="p", source="s") is True
        port.publish.assert_called_once()

    def test_retries_then_succeeds_returns_true(self):
        port = MagicMock()
        # Fail twice, succeed on the third attempt.
        port.publish.side_effect = [RuntimeError("blip"), RuntimeError("blip"), None]
        pub = EventPublisher(port=port)
        with patch("app.events.publisher.time.sleep"):  # no real backoff in tests
            ok = pub.publish(_event(), project_id="p", source="s")
        assert ok is True
        assert port.publish.call_count == 3

    def test_definitive_failure_returns_false_and_does_not_raise(self):
        port = MagicMock()
        port.publish.side_effect = RuntimeError("firestore down")
        pub = EventPublisher(port=port)
        with patch("app.events.publisher.time.sleep"):
            ok = pub.publish(_event(), project_id="p", source="s")
        # No exception propagated (primary operation stays intact) and the
        # miss is reported truthfully as False, not a phantom success.
        assert ok is False
        assert port.publish.call_count == 3
