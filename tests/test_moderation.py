from unittest.mock import MagicMock, patch

import pytest

from app.models.moderation import ModerationLevel
from app.security.context import AuthContext
from app.services.moderation_service import ModerationService

_FS = "app.services.moderation_service.firestore"
_PID = "spartacus-artes-marciais"


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
