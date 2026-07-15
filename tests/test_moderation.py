from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.moderation import ModerationLevel
from app.security.context import AuthContext
from app.services.moderation_service import ModerationService

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_FS = "app.services.moderation_service.firestore"
_VERIFY = "app.security.middleware.verify_id_token"
_PID = "spartacus-artes-marciais"


def _claims(uid="staff1", roles=("owner",)):
    return {"uid": uid, "email": f"{uid}@t.com", "projects": {_PID: list(roles)}}


_VALID_CLAIMS = _claims()
_HEADERS = {"Authorization": "Bearer valid-token", "X-Project-Id": _PID}


def _ctx(uid="staff1", roles=("assistant",)):
    return AuthContext(user_id=uid, user_email=f"{uid}@t.com",
                       project_id=_PID, roles=list(roles))


def _db(mod_doc=None, target_roles=None, target_name="Alvo"):
    db = MagicMock()
    mod_ref = MagicMock()
    mod_snap = MagicMock(exists=mod_doc is not None)
    mod_snap.to_dict.return_value = mod_doc or {}
    mod_ref.get.return_value = mod_snap
    mod_col = MagicMock(); mod_col.document.return_value = mod_ref

    user_ref = MagicMock()
    user_snap = MagicMock(exists=True)
    user_snap.to_dict.return_value = {"name": target_name, "photoUrl": None}
    user_ref.get.return_value = user_snap
    users_col = MagicMock(); users_col.document.return_value = user_ref

    mem_ref = MagicMock()
    mem_snap = MagicMock(exists=target_roles is not None)
    mem_snap.to_dict.return_value = {"roles": target_roles or []}
    mem_ref.get.return_value = mem_snap
    mem_col = MagicMock(); mem_col.document.return_value = mem_ref

    db.collection.side_effect = lambda n: {
        "moderation": mod_col, "users": users_col, "memberships": mem_col,
    }.get(n, MagicMock())
    return db, mod_ref


def test_get_level_absent_is_none():
    db, _ = _db(mod_doc=None)
    with patch(_FS) as fs:
        fs.client.return_value = db
        assert ModerationService().get_level(_PID, "u9") == "none"


def test_teacher_cannot_app_ban():
    db, _ = _db(target_roles=["student"])
    with patch(_FS) as fs:
        fs.client.return_value = db
        with pytest.raises(PermissionError):
            ModerationService().apply(_PID, "u9", _ctx(roles=("teacher",)),
                                      ModerationLevel.APP_BANNED.value, "x")


def test_cannot_moderate_self():
    db, _ = _db(target_roles=["student"])
    with patch(_FS) as fs:
        fs.client.return_value = db
        with pytest.raises(PermissionError):
            ModerationService().apply(_PID, "staff1", _ctx(),
                                      ModerationLevel.COMMENT_BLOCKED.value, "x")


def test_non_owner_cannot_moderate_staff():
    db, _ = _db(target_roles=["teacher"])
    with patch(_FS) as fs:
        fs.client.return_value = db
        with pytest.raises(PermissionError):
            ModerationService().apply(_PID, "u9", _ctx(roles=("assistant",)),
                                      ModerationLevel.COMMENT_BLOCKED.value, "x")


def test_owner_can_moderate_staff_and_persists():
    db, mod_ref = _db(target_roles=["teacher"])
    with patch(_FS) as fs, patch("app.services.moderation_service.AccountHistoryService"):
        fs.client.return_value = db
        ev = ModerationService().apply(_PID, "u9", _ctx(uid="own", roles=("owner",)),
                                       ModerationLevel.COMMENT_BLOCKED.value, "spam")
    assert ev.id == "account.comment_blocked"
    assert mod_ref.set.called
    assert mod_ref.set.call_args[0][0]["level"] == "comment_blocked"


def test_apply_app_ban_emits_event():
    db, mod_ref = _db(target_roles=["student"])
    with patch(_FS) as fs, patch("app.services.moderation_service.AccountHistoryService"):
        fs.client.return_value = db
        ev = ModerationService().apply(_PID, "u9", _ctx(roles=("owner",)),
                                       ModerationLevel.APP_BANNED.value, "grave")
    assert ev.id == "account.app_banned"


def test_apply_app_ban_by_assistant_succeeds():
    db, mod_ref = _db(target_roles=["student"])
    with patch(_FS) as fs, patch("app.services.moderation_service.AccountHistoryService"):
        fs.client.return_value = db
        ev = ModerationService().apply(_PID, "u9", _ctx(roles=("assistant",)),
                                       ModerationLevel.APP_BANNED.value, "grave")
    assert ev.id == "account.app_banned"
    assert mod_ref.set.called
    assert mod_ref.set.call_args[0][0]["level"] == "app_banned"


def test_lift_deletes_doc_and_emits():
    db, mod_ref = _db(mod_doc={"level": "app_banned"}, target_roles=["student"])
    with patch(_FS) as fs, patch("app.services.moderation_service.AccountHistoryService"):
        fs.client.return_value = db
        ev = ModerationService().lift(_PID, "u9", _ctx(roles=("owner",)))
    assert ev.id == "account.moderation_lifted"
    assert mod_ref.delete.called


def test_lift_app_ban_requires_owner_or_assistant():
    db, _ = _db(mod_doc={"level": "app_banned"}, target_roles=["student"])
    with patch(_FS) as fs:
        fs.client.return_value = db
        with pytest.raises(PermissionError):
            ModerationService().lift(_PID, "u9", _ctx(roles=("teacher",)))


class TestModerationRoutes:
    """TestClient-level tests for the /moderation REST endpoints (Task 3)."""

    def test_owner_post_201_and_publish_called(self):
        db, mod_ref = _db(target_roles=["student"])
        with patch(_VERIFY, return_value=_claims(roles=("owner",))), \
             patch(_FS) as fs, \
             patch("app.routers.moderation.publisher") as pub, \
             patch("app.services.moderation_service.AccountHistoryService"):
            fs.client.return_value = db
            r = client.post(
                "/moderation/u9", headers=_HEADERS,
                json={"level": "comment_blocked", "reason": "spam"},
            )
        assert r.status_code == 201
        pub.publish.assert_called_once()
        assert mod_ref.set.call_args[0][0]["level"] == "comment_blocked"

    def test_teacher_post_app_banned_403(self):
        db, _ = _db(target_roles=["student"])
        with patch(_VERIFY, return_value=_claims(uid="staff1", roles=("teacher",))), \
             patch(_FS) as fs:
            fs.client.return_value = db
            r = client.post(
                "/moderation/u9", headers=_HEADERS,
                json={"level": "app_banned", "reason": "grave"},
            )
        assert r.status_code == 403

    def test_owner_delete_204(self):
        db, mod_ref = _db(
            mod_doc={"level": "comment_blocked"}, target_roles=["student"]
        )
        with patch(_VERIFY, return_value=_claims(roles=("owner",))), \
             patch(_FS) as fs, \
             patch("app.routers.moderation.publisher") as pub, \
             patch("app.services.moderation_service.AccountHistoryService"):
            fs.client.return_value = db
            r = client.delete("/moderation/u9", headers=_HEADERS)
        assert r.status_code == 204
        assert mod_ref.delete.called
        pub.publish.assert_called_once()

    def test_staff_get_users_200_with_items(self):
        db = MagicMock()

        m = MagicMock()
        m.to_dict.return_value = {"userId": "u1", "roles": ["student"]}
        mships_query = MagicMock()
        mships_query.where.return_value = mships_query
        mships_query.stream.return_value = [m]
        mem_col = MagicMock()
        mem_col.where.return_value = mships_query

        user_ref = MagicMock()
        user_snap = MagicMock(exists=True)
        user_snap.to_dict.return_value = {"name": "Aluno", "photoUrl": None}
        user_ref.get.return_value = user_snap
        users_col = MagicMock()
        users_col.document.return_value = user_ref

        mod_ref = MagicMock()
        mod_ref.get.return_value = MagicMock(exists=False)
        mod_col = MagicMock()
        mod_col.document.return_value = mod_ref

        db.collection.side_effect = lambda n: {
            "memberships": mem_col, "users": users_col, "moderation": mod_col,
        }.get(n, MagicMock())

        with patch(_VERIFY, return_value=_claims(roles=("owner",))), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.get("/moderation/users", headers=_HEADERS)
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["uid"] == "u1"
        assert items[0]["level"] == "none"

    def test_non_staff_post_403(self):
        with patch(_VERIFY, return_value=_claims(uid="student1", roles=("student",))):
            r = client.post(
                "/moderation/u9", headers=_HEADERS,
                json={"level": "comment_blocked", "reason": "x"},
            )
        assert r.status_code == 403


class TestModerationServiceListUsersScoping:
    """ModerationService.list_users — multi-tenant + filter scoping (Task 3 gap)."""

    @staticmethod
    def _list_db(memberships, users, moderations):
        """
        memberships: list of dicts returned by each membership doc's to_dict()
        users: dict uid -> user dict (to_dict() output); a missing uid means
               the users doc does not exist and is skipped by list_users
        moderations: dict uid -> moderation level string; a missing uid means
                     no moderation doc exists (get_level falls back to "none")
        """
        db = MagicMock()

        mem_col = MagicMock()
        first_where = MagicMock()
        inner_where = MagicMock()
        docs = [MagicMock(to_dict=MagicMock(return_value=m)) for m in memberships]
        inner_where.stream.return_value = docs
        first_where.where.return_value = inner_where
        mem_col.where.return_value = first_where

        users_col = MagicMock()

        def _user_doc(uid):
            ref = MagicMock()
            if uid in users:
                snap = MagicMock(exists=True)
                snap.to_dict.return_value = users[uid]
            else:
                snap = MagicMock(exists=False)
                snap.to_dict.return_value = {}
            ref.get.return_value = snap
            return ref

        users_col.document.side_effect = _user_doc

        mod_col = MagicMock()

        def _mod_doc(doc_id):
            uid = doc_id.split("_", 1)[1]
            ref = MagicMock()
            if uid in moderations:
                snap = MagicMock(exists=True)
                snap.to_dict.return_value = {"level": moderations[uid]}
            else:
                snap = MagicMock(exists=False)
                snap.to_dict.return_value = {}
            ref.get.return_value = snap
            return ref

        mod_col.document.side_effect = _mod_doc

        db.collection.side_effect = lambda n: {
            "memberships": mem_col, "users": users_col, "moderation": mod_col,
        }.get(n, MagicMock())
        return db, mem_col

    def test_query_scopes_to_project_and_active_status(self):
        """Non-active memberships never reach list_users: the exclusion is
        enforced by the Firestore query itself (.where projectId + status
        'active'), so only docs the (mocked) query returns are considered.
        Prove the query is built with the right filters and that a member
        whose membership isn't in that filtered set is absent from output.
        """
        memberships = [{"userId": "u1", "roles": ["student"]}]
        users = {
            "u1": {"name": "Ativo", "photoUrl": None},
            # u2 exists as a user but has no *active* membership in this
            # project — it was excluded upstream by the Firestore filter,
            # so it is deliberately absent from `memberships` above.
            "u2": {"name": "Inativo", "photoUrl": None},
        }
        db, mem_col = self._list_db(memberships, users, {})
        with patch(_FS) as fs:
            fs.client.return_value = db
            page = ModerationService().list_users(_ctx(), q="", status="")

        uids = {i.uid for i in page.items}
        assert uids == {"u1"}
        mem_col.where.assert_called_once_with("projectId", "==", _PID)
        mem_col.where.return_value.where.assert_called_once_with(
            "status", "==", "active"
        )

    def test_q_filters_by_name_substring(self):
        memberships = [
            {"userId": "u1", "roles": ["student"]},
            {"userId": "u2", "roles": ["student"]},
        ]
        users = {
            "u1": {"name": "Ana Silva", "photoUrl": None},
            "u2": {"name": "Beto Souza", "photoUrl": None},
        }
        db, _ = self._list_db(memberships, users, {})
        with patch(_FS) as fs:
            fs.client.return_value = db
            page = ModerationService().list_users(_ctx(), q="silva", status="")

        assert {i.uid for i in page.items} == {"u1"}

    def test_status_filters_by_moderation_level(self):
        memberships = [
            {"userId": "u1", "roles": ["student"]},
            {"userId": "u2", "roles": ["student"]},
        ]
        users = {
            "u1": {"name": "Bloqueado", "photoUrl": None},
            "u2": {"name": "Normal", "photoUrl": None},
        }
        # u1 has an explicit "comment_blocked" moderation doc; u2 has none,
        # so get_level() falls back to "none" for u2.
        moderations = {"u1": "comment_blocked"}
        db, _ = self._list_db(memberships, users, moderations)

        with patch(_FS) as fs:
            fs.client.return_value = db
            blocked_page = ModerationService().list_users(
                _ctx(), q="", status="comment_blocked"
            )
            none_page = ModerationService().list_users(_ctx(), q="", status="none")

        assert {i.uid for i in blocked_page.items} == {"u1"}
        assert {i.uid for i in none_page.items} == {"u2"}
