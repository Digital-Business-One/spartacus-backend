"""Tests for RFC-12 endpoints — paginated /accounts and admin /accounts/{uid}/*."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_ADMIN_UID = "admin1"
_TARGET_UID = "user42"
_VERIFY = "app.security.middleware.verify_id_token"
_FS_ACCOUNT = "app.services.account_service.firestore"

_HEADERS = {
    "Authorization": "Bearer tok",
    "X-Project-Id": _PID,
}
_ADMIN_CLAIMS = {
    "uid": _ADMIN_UID,
    "email": "admin@t.com",
    "projects": {_PID: ["owner"]},
}
_NON_ADMIN_CLAIMS = {
    "uid": _TARGET_UID,
    "email": "u@t.com",
    "projects": {_PID: ["student"]},
}


def _membership_doc(uid: str, roles: list[str]):
    m = MagicMock()
    m.to_dict.return_value = {
        "projectId": _PID,
        "userId": uid,
        "roles": roles,
        "status": "active",
    }
    return m


def _user_doc(
    uid: str,
    name: str = "João Silva",
    email: str = "joao@t.com",
    status: str = "approved",
    birth_date: str = "10/03/2014",
    photo_url: str | None = None,
    graduation: dict | None = None,
    auth_provider: str | None = "password",
    approved_by: str | None = None,
):
    doc = MagicMock()
    doc.exists = True
    doc.id = uid
    doc.to_dict.return_value = {
        "name": name,
        "email": email,
        "approvalStatus": status,
        "birthDate": birth_date,
        "gender": "male",
        "phone": "65987654321",
        "whatsapp": "65987654321",
        "taxId": None,
        "photoUrl": photo_url,
        "isDependent": False,
        "guardianUid": None,
        "classIds": [],
        "graduation": graduation,
        "authProvider": auth_provider,
        "approvedBy": approved_by,
        "approvedAt": "2026-03-15T10:00:00+00:00" if approved_by else None,
        "createdAt": "2026-01-15T09:00:00+00:00",
        "updatedAt": "2026-03-15T10:00:00+00:00",
        "lastUpdatedBy": approved_by,
    }
    return doc


class TestListAccountsPaginated:
    def test_returns_envelope_with_pagination(self):
        mock_db = MagicMock()
        memberships = [
            _membership_doc(f"u{i}", ["student"])
            for i in range(25)
        ]
        col = mock_db.collection.return_value
        col.where.return_value.stream.return_value = memberships

        users = [_user_doc(f"u{i}", name=f"User {i:02d}") for i in range(25)]
        for u in users:
            u.id = u.to_dict.return_value.get("__id__", "")
        # Mock get_all to return user docs in order
        mock_db.get_all.return_value = users

        with (
            patch(_VERIFY, return_value=_ADMIN_CLAIMS),
            patch(_FS_ACCOUNT) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.get(
                "/accounts?page=1&pageSize=10",
                headers=_HEADERS,
            )

        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "pageSize" in data
        assert "totalPages" in data
        assert data["total"] == 25
        assert data["page"] == 1
        assert data["pageSize"] == 10
        assert data["totalPages"] == 3
        assert len(data["items"]) == 10

    def test_page_size_capped_at_50(self):
        with patch(_VERIFY, return_value=_ADMIN_CLAIMS):
            r = client.get(
                "/accounts?page=1&pageSize=200",
                headers=_HEADERS,
            )
        assert r.status_code == 422  # validation error from FastAPI

    def test_requires_owner_or_assistant(self):
        with patch(_VERIFY, return_value=_NON_ADMIN_CLAIMS):
            r = client.get("/accounts", headers=_HEADERS)
        assert r.status_code == 403


class TestGetAccountDetail:
    def test_returns_detail_with_audit_fields(self):
        mock_db = MagicMock()
        # Mock user_doc fetch (target)
        target_doc = _user_doc(
            _TARGET_UID,
            graduation={"jiu-jitsu": {"belt": "blue", "degree": 2}},
            approved_by=_ADMIN_UID,
        )
        # Mock membership for roles
        member_doc = MagicMock()
        member_doc.exists = True
        member_doc.to_dict.return_value = {
            "projectId": _PID,
            "userId": _TARGET_UID,
            "roles": ["student"],
            "status": "active",
        }
        # Mock approver name resolution
        approver_doc = _user_doc(_ADMIN_UID, name="Ana Souza")

        # Sequential collection().document().get() calls:
        #   1. user (target) — get_account_detail line 1
        #   2. membership doc (roles)
        #   3. approver lookup (last_updated_by)
        #   4. approver lookup (approved_by) — same uid so cached
        # Simpler: have document().get() return target, then approver as needed
        get_results = iter([target_doc, member_doc, approver_doc, approver_doc])
        mock_db.collection.return_value.document.return_value.get.side_effect = (
            lambda: next(get_results)
        )

        with (
            patch(_VERIFY, return_value=_ADMIN_CLAIMS),
            patch(_FS_ACCOUNT) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.get(
                f"/accounts/{_TARGET_UID}",
                headers=_HEADERS,
            )

        assert r.status_code == 200
        data = r.json()
        assert data["uid"] == _TARGET_UID
        assert data["name"] == "João Silva"
        assert data["status"] == "approved"
        assert data["authProvider"] == "password"
        assert data["graduation"] == {
            "jiu-jitsu": {
                "belt": "blue", "degree": 2, "prajied": None,
                "status": "approved", "lockedByStudent": False,
                "gradedBy": None, "gradedByName": None, "gradedAt": None,
            }
        }
        assert data["ageCategory"] == "child"  # 12 anos

    def test_404_when_account_not_found(self):
        mock_db = MagicMock()
        not_found = MagicMock()
        not_found.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = (
            not_found
        )

        with (
            patch(_VERIFY, return_value=_ADMIN_CLAIMS),
            patch(_FS_ACCOUNT) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.get(
                f"/accounts/{_TARGET_UID}",
                headers=_HEADERS,
            )
        assert r.status_code == 404


class TestSuspendVisualName:
    """Suspender = expel (CNV). Verify the action 'expel' still works."""

    def test_expel_transition_works(self):
        mock_db = MagicMock()
        # User in APPROVED state
        target_doc = _user_doc(_TARGET_UID, status="approved")
        member_doc = MagicMock()
        member_doc.exists = True
        member_doc.to_dict.return_value = {
            "projectId": _PID,
            "userId": _TARGET_UID,
            "roles": ["student"],
            "status": "active",
        }
        # actor lookup (for actor_name)
        actor_doc = _user_doc(_ADMIN_UID, name="Ana Souza")

        get_iter = iter([target_doc, member_doc, actor_doc, member_doc])
        mock_db.collection.return_value.document.return_value.get.side_effect = (
            lambda: next(get_iter)
        )

        with (
            patch(_VERIFY, return_value=_ADMIN_CLAIMS),
            patch(_FS_ACCOUNT) as fs,
            patch("app.routers.accounts.publisher"),
        ):
            fs.client.return_value = mock_db
            r = client.post(
                f"/accounts/{_TARGET_UID}/transitions",
                headers=_HEADERS,
                json={"action": "expel"},
            )

        # Should succeed (state machine has approved→expelled)
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "expel"
        assert data["new_status"] == "expelled"
