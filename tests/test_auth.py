"""Tests for GET /auth/me (Task 4: exposes appBanned + moderationReason)."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_VERIFY = "app.security.middleware.verify_id_token"
_AUTH_FS = "app.services.auth_service.firestore"
_MOD_FS = "app.services.moderation_service.firestore"
_PROJECT_ID = "spartacus-artes-marciais"

_CLAIMS = {
    "uid": "u1",
    "email": "u1@test.com",
    "projects": {_PROJECT_ID: ["student"]},
}
_HEADERS = {"Authorization": "Bearer valid-token", "X-Project-Id": _PROJECT_ID}


def _user_db(approval_status="approved", birth_date="01/01/1990"):
    """Firestore mock for AuthService().get_user_status(uid)."""
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = {
        "approvalStatus": approval_status,
        "birthDate": birth_date,
    }
    db = MagicMock()
    db.collection.return_value.document.return_value.get.return_value = doc
    return db


def _moderation_db(level="none", reason=None):
    """Firestore mock for ModerationService().get_status(project_id, uid) —
    a separate `firestore.client()` binding from the one used by
    AuthService, so it must be patched independently."""
    snap = MagicMock()
    snap.exists = level != "none"
    snap.to_dict.return_value = (
        {"level": level, "reason": reason} if snap.exists else {}
    )
    db = MagicMock()
    db.collection.return_value.document.return_value.get.return_value = snap
    return db


class TestMe:
    def test_normal_user_not_app_banned(self):
        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_AUTH_FS) as auth_fs,
            patch(_MOD_FS) as mod_fs,
        ):
            auth_fs.client.return_value = _user_db()
            mod_fs.client.return_value = _moderation_db(level="none")
            r = client.get("/auth/me", headers=_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["appBanned"] is False
        assert body["moderationReason"] is None

    def test_app_banned_user_returns_reason(self):
        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_AUTH_FS) as auth_fs,
            patch(_MOD_FS) as mod_fs,
        ):
            auth_fs.client.return_value = _user_db()
            mod_fs.client.return_value = _moderation_db(
                level="app_banned", reason="Comportamento inadequado"
            )
            r = client.get("/auth/me", headers=_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["appBanned"] is True
        assert body["moderationReason"] == "Comportamento inadequado"

    def test_comment_blocked_user_is_not_app_banned(self):
        """comment_blocked is a lesser moderation level — must not surface
        as appBanned, and must not leak a reason via moderationReason."""
        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_AUTH_FS) as auth_fs,
            patch(_MOD_FS) as mod_fs,
        ):
            auth_fs.client.return_value = _user_db()
            mod_fs.client.return_value = _moderation_db(
                level="comment_blocked", reason="spam"
            )
            r = client.get("/auth/me", headers=_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["appBanned"] is False
        assert body["moderationReason"] is None
