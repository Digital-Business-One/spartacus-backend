"""Tests for disciplinary warning (account.warned) + suspension moderation."""

from unittest.mock import MagicMock, patch

from app.services.account_service import AccountService


def _db_with_users(names: dict[str, str]) -> MagicMock:
    db = MagicMock()
    users = MagicMock()

    def document(uid):
        ref = MagicMock()
        doc = MagicMock()
        doc.exists = uid in names
        doc.to_dict.return_value = {"name": names.get(uid, "")}
        ref.get.return_value = doc
        return ref

    users.document.side_effect = document
    db.collection.return_value = users
    return db


class TestWarnAccount:
    def test_records_history_and_emits_event(self):
        db = _db_with_users({"aluno-1": "Júnior Silva", "staff-1": "Prof. Carlos"})
        with patch("app.services.account_service.firestore") as fs, \
             patch("app.services.account_service.AccountHistoryService") as hist, \
             patch.object(AccountService, "_get_roles", return_value=["teacher"]):
            fs.client.return_value = db
            event = AccountService().warn_account(
                project_id="spartacus",
                uid="aluno-1",
                actor_uid="staff-1",
                reason="Faltou com respeito ao colega",
            )

        # History recorded as a filterable "warning" type, no status change
        rec = hist.return_value.record.call_args.kwargs
        assert rec["event_type"] == "warning"
        assert "Faltou com respeito" in rec["description"]

        assert event.id == "account.warned"
        p = event.payload.personalization()
        assert p["target_uid"] == "aluno-1"
        assert p["target_name"] == "Júnior Silva"
        assert p["author_name"] == "Prof. Carlos"
        assert p["reason"] == "Faltou com respeito ao colega"

    def test_missing_user_raises(self):
        db = _db_with_users({"staff-1": "Prof"})
        with patch("app.services.account_service.firestore") as fs, \
             patch("app.services.account_service.AccountHistoryService"), \
             patch.object(AccountService, "_get_roles", return_value=[]):
            fs.client.return_value = db
            import pytest
            with pytest.raises(LookupError):
                AccountService().warn_account(
                    project_id="spartacus",
                    uid="ghost",
                    actor_uid="staff-1",
                    reason="x",
                )
