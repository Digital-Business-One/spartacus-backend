"""
Tests for the orchestrator push channel — post.created → push_queue.

The orchestrator Cloud Function module is loaded via importlib (it lives
outside the `app` package). Firestore and firebase init are mocked.
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import firebase_admin


def _load_orchestrator():
    path = os.path.join(
        os.path.dirname(__file__), "..", "functions", "orchestrator", "main.py",
    )
    spec = importlib.util.spec_from_file_location("orchestrator_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    with patch.object(firebase_admin, "initialize_app", lambda *a, **kw: None):
        spec.loader.exec_module(mod)
    return mod


orch = _load_orchestrator()


def _make_db(member_uids):
    """Mock Firestore: memberships query returns docs, push_queue records adds."""
    db = MagicMock()
    docs = []
    for uid in member_uids:
        d = MagicMock()
        d.to_dict.return_value = {"userId": uid}
        docs.append(d)

    memberships = MagicMock()
    memberships.where.return_value.where.return_value.stream.return_value = docs
    push_queue = MagicMock()

    db.collection.side_effect = lambda name: {
        "memberships": memberships,
        "push_queue": push_queue,
    }[name]
    return db, push_queue


def _post_payload(author_uid="author-1"):
    return {
        "entity_id": "post-abc",
        "source_entity_type": "posts",
        "type": "post",
        "title": "Treino especial de sábado",
        "description": "Aulão aberto para todos",
        "author_uid": author_uid,
        "author_name": "Prof. João",
    }


class TestPostCreatedRule:
    def test_rule_declares_push_channel(self):
        rule = orch.EVENT_RULES["post.created"]
        assert "push" in rule["channels"]
        assert rule["push"]["target"] == "all_members"
        assert rule["push"]["exclude_author"] is True

    def test_push_templates_use_author_and_title(self):
        push = orch.EVENT_RULES["post.created"]["push"]
        payload = _post_payload()
        assert orch._safe_format(push["title_template"], payload) == "Prof. João"
        assert (
            orch._safe_format(push["body_template"], payload)
            == "Treino especial de sábado"
        )


class TestHandlePushAllMembers:
    def test_enqueues_for_all_members_except_author(self):
        db, push_queue = _make_db(["u1", "author-1", "u2"])
        event_data = {"eventId": "post.created", "projectId": "spartacus"}

        orch._handle_push(
            orch.EVENT_RULES["post.created"],
            event_data,
            _post_payload(author_uid="author-1"),
            "events/evt-1",
            db,
        )

        added = [c.args[0] for c in push_queue.add.call_args_list]
        assert {d["to_uid"] for d in added} == {"u1", "u2"}
        for d in added:
            assert d["title"] == "Prof. João"
            assert d["body"] == "Treino especial de sábado"
            assert d["status"] == "pending"
            assert d["source_event_ref"] == "events/evt-1"
            assert d["data"]["event_id"] == "post.created"
            assert d["data"]["entity_type"] == "posts"
            assert d["data"]["entity_id"] == "post-abc"

    def test_author_not_excluded_when_flag_absent(self):
        db, push_queue = _make_db(["u1", "author-1"])
        rule = {
            "push": {
                "target": "all_members",
                "title_template": "{author_name}",
                "body_template": "{title}",
            },
        }
        orch._handle_push(
            rule,
            {"eventId": "post.created", "projectId": "spartacus"},
            _post_payload(author_uid="author-1"),
            "events/evt-2",
            db,
        )
        added = [c.args[0] for c in push_queue.add.call_args_list]
        assert {d["to_uid"] for d in added} == {"u1", "author-1"}

    def test_skips_empty_uids(self):
        db, push_queue = _make_db(["u1", "", "u2"])
        orch._handle_push(
            orch.EVENT_RULES["post.created"],
            {"eventId": "post.created", "projectId": "spartacus"},
            _post_payload(),
            "events/evt-3",
            db,
        )
        added = [c.args[0] for c in push_queue.add.call_args_list]
        assert {d["to_uid"] for d in added} == {"u1", "u2"}


class TestResolveAllMemberUids:
    def test_deduplicates_and_drops_empty(self):
        db, _ = _make_db(["u1", "u1", "", "u2"])
        uids = orch._resolve_all_member_uids(db, "spartacus")
        assert uids == ["u1", "u2"]


def _membership(uid, roles):
    d = MagicMock()
    d.to_dict.return_value = {"userId": uid, "roles": roles, "status": "active"}
    return d


class TestModerationFanOut:
    """account.warned / account.suspended → student push + staff-except-author."""

    def _make_db(self, members):
        db = MagicMock()
        docs = [_membership(uid, roles) for uid, roles in members]
        memberships = MagicMock()
        memberships.where.return_value.where.return_value.stream.return_value = docs
        push_queue = MagicMock()
        users = MagicMock()
        # No guardians / adult student → users docs have no dependentUids
        users.document.return_value.get.return_value = MagicMock(
            exists=True, to_dict=MagicMock(return_value={}),
        )
        db.collection.side_effect = lambda name: {
            "memberships": memberships,
            "push_queue": push_queue,
            "users": users,
        }[name]
        return db, push_queue

    def test_warned_two_pushes_student_and_staff_except_author(self):
        db, push_queue = self._make_db([
            ("aluno-1", ["student"]),
            ("staff-author", ["teacher"]),
            ("staff-other", ["assistant"]),
            ("owner-1", ["owner"]),
        ])
        payload = {
            "target_uid": "aluno-1",
            "target_name": "Júnior Silva",
            "author_uid": "staff-author",
            "author_name": "Prof. Carlos",
            "reason": "Indisciplina",
            "source_entity_type": "users",
            "entity_id": "aluno-1",
        }
        orch._handle_push(
            orch.EVENT_RULES["account.warned"],
            {"eventId": "account.warned", "projectId": "spartacus"},
            payload,
            "events/evt-w",
            db,
        )
        added = [c.args[0] for c in push_queue.add.call_args_list]
        by_uid = {d["to_uid"]: d for d in added}

        # Student gets the personal message
        assert "aluno-1" in by_uid
        assert by_uid["aluno-1"]["title"] == "Você recebeu uma advertência"
        assert "Indisciplina" in by_uid["aluno-1"]["body"]

        # Staff (except the author) get the informational message
        assert "staff-other" in by_uid
        assert "owner-1" in by_uid
        assert "staff-author" not in by_uid  # autor excluído
        assert by_uid["staff-other"]["title"] == "Advertência registrada"
        assert "Júnior Silva" in by_uid["staff-other"]["body"]
        assert "Prof. Carlos" in by_uid["staff-other"]["body"]
