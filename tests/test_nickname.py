"""Tests for nickname assignment (account.nickname_assigned)."""

from unittest.mock import MagicMock, patch

from app.services.account_service import AccountService


def _db() -> tuple[MagicMock, MagicMock]:
    db = MagicMock()
    target_doc = MagicMock()
    target_doc.exists = True
    target_doc.to_dict.return_value = {"name": "Júnior Silva"}
    target_ref = MagicMock()
    target_ref.get.return_value = target_doc

    actor_doc = MagicMock()
    actor_doc.exists = True
    actor_doc.to_dict.return_value = {"name": "Prof. Carlos"}
    actor_ref = MagicMock()
    actor_ref.get.return_value = actor_doc

    users = MagicMock()
    users.document.side_effect = lambda uid: (
        target_ref if uid == "aluno-1" else actor_ref
    )
    db.collection.return_value = users
    return db, target_ref


class TestAssignNickname:
    def test_sets_nickname_and_emits_fun_event(self):
        db, target_ref = _db()
        with patch("app.services.account_service.firestore") as fs, \
             patch("app.services.account_service.AccountHistoryService"):
            fs.client.return_value = db
            event = AccountService().assign_nickname(
                project_id="spartacus",
                target_uid="aluno-1",
                actor_uid="staff-1",
                nickname="Montanha",
            )

        updated = target_ref.update.call_args[0][0]
        assert updated["nickname"] == "Montanha"
        assert event is not None
        assert event.id == "account.nickname_assigned"
        p = event.payload.personalization()
        assert p["target_uid"] == "aluno-1"
        assert p["nickname"] == "Montanha"
        # Mensagem bem-humorada: cita o apelido e o autor, tom acolhedor
        assert "Montanha" in p["title"]
        assert "Montanha" in p["description"]
        assert "Prof. Carlos" in p["description"]
        assert "família" in p["description"]

    def test_empty_nickname_clears_without_event(self):
        db, target_ref = _db()
        with patch("app.services.account_service.firestore") as fs, \
             patch("app.services.account_service.AccountHistoryService"):
            fs.client.return_value = db
            event = AccountService().assign_nickname(
                project_id="spartacus",
                target_uid="aluno-1",
                actor_uid="staff-1",
                nickname="",
            )

        updated = target_ref.update.call_args[0][0]
        assert updated["nickname"] is None
        assert event is None
