import io
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PROJECT_ID = "spartacus"
_USER_ID = "user123"
_DEP_UID = "user123_dep_1"

_HEADERS = {
    "Authorization": "Bearer valid-token",
    "X-Project-Id": _PROJECT_ID,
}

_VALID_CLAIMS = {
    "uid": _USER_ID,
    "email": "joao@example.com",
    "projects": {_PROJECT_ID: ["student"]},
}

_GUARDIAN_CLAIMS = {
    "uid": _USER_ID,
    "email": "joao@example.com",
    "projects": {_PROJECT_ID: ["guardian"]},
}

_VERIFY = "app.security.middleware.verify_id_token"
_FS = "app.services.profile_service.firestore"


def _user_doc(overrides=None):
    base = {
        "name": "João Silva",
        "email": "joao@example.com",
        "birthDate": "01/01/1990",
        "gender": "male",
        "phone": "65999990000",
        "whatsapp": "65999990000",
        "taxId": "12345678900",
        "photoUrl": None,
        "approvalStatus": "approved",
        "isDependent": False,
        "classIds": ["spartacus_jiu-jitsu-kids"],
        "address": {
            "postalCode": "78350-000",
            "street": "Rua Rotary Internacional",
            "number": "270",
            "complement": None,
            "neighborhood": "Centro",
            "city": "Brasnorte",
            "state": "MT",
        },
        "graduation": None,
        "competition": None,
        "createdAt": "2026-01-01T00:00:00Z",
    }
    if overrides:
        base.update(overrides)
    return base


def _mock_firestore(user_data=None, membership_roles=None):
    """Build a Firestore mock for profile endpoints."""
    if user_data is None:
        user_data = _user_doc()
    if membership_roles is None:
        membership_roles = ["student"]

    mock_db = MagicMock()

    mock_user_doc = MagicMock()
    mock_user_doc.exists = True
    mock_user_doc.to_dict.return_value = user_data
    mock_user_doc.id = _USER_ID

    mock_mem_doc = MagicMock()
    mock_mem_doc.exists = True
    mock_mem_doc.to_dict.return_value = {
        "projectId": _PROJECT_ID,
        "userId": _USER_ID,
        "roles": membership_roles,
        "status": "active",
    }

    mock_class_doc = MagicMock()
    mock_class_doc.exists = True
    mock_class_doc.id = "spartacus_jiu-jitsu-kids"
    mock_class_doc.to_dict.return_value = {"name": "Jiu-Jitsu Kids", "active": True}

    mock_proj_doc = MagicMock()
    mock_proj_doc.exists = False

    def collection_side(name):
        col = MagicMock()
        if name == "users":
            ref = MagicMock()
            ref.get.return_value = mock_user_doc
            ref.update.return_value = None
            ref.set.return_value = None
            col.document.return_value = ref
        elif name == "memberships":
            ref = MagicMock()
            ref.get.return_value = mock_mem_doc
            ref.update.return_value = None
            ref.set.return_value = None
            col.document.return_value = ref
        elif name == "classes":
            ref = MagicMock()
            ref.get.return_value = mock_class_doc
            col.document.return_value = ref
        elif name == "projects":
            ref = MagicMock()
            ref.get.return_value = mock_proj_doc
            col.document.return_value = ref
        return col

    mock_db.collection.side_effect = collection_side
    mock_db.get_all.return_value = [mock_class_doc]

    return mock_db


# ── GET /users/me/profile ────────────────────────────────────────────────────


class TestGetProfile:
    def test_returns_profile(self):
        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = _mock_firestore()
            resp = client.get("/users/me/profile", headers=_HEADERS)

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "João Silva"
        assert data["email"] == "joao@example.com"
        assert data["approvalStatus"] == "approved"
        assert isinstance(data["completionPercent"], int)
        assert data["classNames"] == ["Jiu-Jitsu Kids"]

    def test_404_when_user_not_found(self):
        mock_db = MagicMock()
        mock_not_found = MagicMock()
        mock_not_found.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = (
            mock_not_found
        )

        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = mock_db
            resp = client.get("/users/me/profile", headers=_HEADERS)

        assert resp.status_code == 404


# ── PATCH /users/me/profile ──────────────────────────────────────────────────


class TestUpdateProfile:
    def test_updates_name(self):
        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = _mock_firestore()
            resp = client.patch(
                "/users/me/profile",
                headers=_HEADERS,
                json={"name": "João Gabriel"},
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_rejects_short_name(self):
        with patch(_VERIFY, return_value=_VALID_CLAIMS,
        ):
            resp = client.patch(
                "/users/me/profile",
                headers=_HEADERS,
                json={"name": "ab"},
            )
        assert resp.status_code == 422

    def test_rejects_invalid_phone(self):
        with patch(_VERIFY, return_value=_VALID_CLAIMS,
        ):
            resp = client.patch(
                "/users/me/profile",
                headers=_HEADERS,
                json={"phone": "123"},
            )
        assert resp.status_code == 422


# ── PATCH /users/me/address ──────────────────────────────────────────────────


class TestUpdateAddress:
    def test_updates_address(self):
        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = _mock_firestore()
            resp = client.patch(
                "/users/me/address",
                headers=_HEADERS,
                json={
                    "postalCode": "78350-000",
                    "street": "Rua Nova",
                    "number": "100",
                    "neighborhood": "Centro",
                    "city": "Brasnorte",
                    "state": "MT",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_rejects_invalid_state(self):
        with patch(_VERIFY, return_value=_VALID_CLAIMS,
        ):
            resp = client.patch(
                "/users/me/address",
                headers=_HEADERS,
                json={
                    "postalCode": "78350-000",
                    "street": "Rua Nova",
                    "number": "100",
                    "neighborhood": "Centro",
                    "city": "Brasnorte",
                    "state": "Mato Grosso",
                },
            )
        assert resp.status_code == 422


# ── POST /users/me/dependents ────────────────────────────────────────────────


class TestCreateDependent:
    def test_creates_dependent(self):
        with (
            patch(_VERIFY, return_value=_GUARDIAN_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = _mock_firestore(membership_roles=["guardian"])
            resp = client.post(
                "/users/me/dependents",
                headers=_HEADERS,
                json={
                    "name": "Maria Silva",
                    "birthDate": "15/06/2018",
                    "gender": "female",
                },
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Maria Silva"
        assert data["approvalStatus"] == "incomplete"
        assert data["registrationComplete"] is False

    def test_rejects_adult_dependent(self):
        with patch(_VERIFY, return_value=_GUARDIAN_CLAIMS,
        ):
            resp = client.post(
                "/users/me/dependents",
                headers=_HEADERS,
                json={
                    "name": "Adulto Silva",
                    "birthDate": "01/01/2000",
                    "gender": "male",
                },
            )
        assert resp.status_code == 422


# ── GET /users/me/dependents ─────────────────────────────────────────────────


class TestListDependents:
    def test_lists_dependents(self):
        mock_db = MagicMock()

        dep_doc = MagicMock()
        dep_doc.id = _DEP_UID
        dep_doc.to_dict.return_value = {
            "name": "Maria Silva",
            "birthDate": "15/06/2018",
            "gender": "female",
            "guardianUid": _USER_ID,
            "isDependent": True,
            "approvalStatus": "incomplete",
            "classIds": [],
        }

        users_col = MagicMock()
        users_col.where.return_value.where.return_value.stream.return_value = [dep_doc]

        mem_doc = MagicMock()
        mem_doc.exists = True
        mem_doc.to_dict.return_value = {
            "roles": ["student"], "status": "pending_approval",
        }
        memberships_col = MagicMock()
        memberships_col.document.return_value.get.return_value = mem_doc

        def col_side(name):
            if name == "users":
                return users_col
            if name == "memberships":
                return memberships_col
            return MagicMock()

        mock_db.collection.side_effect = col_side
        mock_db.get_all.return_value = []

        with (
            patch(_VERIFY, return_value=_GUARDIAN_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = mock_db
            resp = client.get("/users/me/dependents", headers=_HEADERS)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Maria Silva"
        assert data[0]["registrationComplete"] is False


# ── PATCH /users/me/graduation ───────────────────────────────────────────────


class TestUpdateGraduation:
    def test_updates_graduation(self):
        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = _mock_firestore()
            resp = client.patch(
                "/users/me/graduation",
                headers=_HEADERS,
                json={
                    "graduation": {
                        "jiu-jitsu": {"belt": "blue", "degree": 2},
                        "muay-thai": {"belt": "green", "degree": 1, "prajied": 3},
                    }
                },
            )

        assert resp.status_code == 200


# ── PATCH /users/me/competition ──────────────────────────────────────────────


class TestUpdateCompetition:
    def test_updates_competition(self):
        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = _mock_firestore()
            resp = client.patch(
                "/users/me/competition",
                headers=_HEADERS,
                json={"weightKg": 78.5, "targetCategories": ["meio-pesado"]},
            )

        assert resp.status_code == 200

    def test_rejects_negative_weight(self):
        with patch(_VERIFY, return_value=_VALID_CLAIMS,
        ):
            resp = client.patch(
                "/users/me/competition",
                headers=_HEADERS,
                json={"weightKg": -5},
            )
        assert resp.status_code == 422


# ── PATCH /users/me/classes ──────────────────────────────────────────────────


class TestUpdateClasses:
    def test_updates_classes(self):
        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(_FS) as mock_fs,
        ):
            mock_fs.client.return_value = _mock_firestore()
            resp = client.patch(
                "/users/me/classes",
                headers=_HEADERS,
                json={"classIds": ["spartacus_jiu-jitsu-kids"]},
            )

        assert resp.status_code == 200


# ── POST /users/me/photo ──────────────────────────────────────────────────────

_STORAGE = "app.services.storage_service"
_ROUTER_FS = "app.routers.profile.firestore"


def _make_test_image(
    width=400, height=400, fmt="JPEG",
):
    from PIL import Image

    img = Image.new("RGB", (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf


class TestUploadPhoto:
    def test_uploads_valid_jpeg(self):
        img_buf = _make_test_image()

        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_bucket.name = "test-bucket"

        mock_db = MagicMock()

        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(
                f"{_STORAGE}._get_bucket",
                return_value=mock_bucket,
            ),
            patch(_ROUTER_FS) as mock_fs,
        ):
            mock_fs.client.return_value = mock_db
            resp = client.post(
                "/users/me/photo",
                headers=_HEADERS,
                files={
                    "file": ("avatar.jpg", img_buf, "image/jpeg"),
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "uploaded"
        assert "photo_url" in data
        mock_blob.upload_from_file.assert_called_once()

    def test_rejects_invalid_content_type(self):
        with patch(_VERIFY, return_value=_VALID_CLAIMS,
        ):
            resp = client.post(
                "/users/me/photo",
                headers=_HEADERS,
                files={
                    "file": (
                        "doc.pdf", b"fake", "application/pdf",
                    ),
                },
            )
        assert resp.status_code == 400

    def test_rejects_oversized_file(self):
        # 6 MB of data
        big_data = b"x" * (6 * 1024 * 1024)
        with patch(_VERIFY, return_value=_VALID_CLAIMS,
        ):
            resp = client.post(
                "/users/me/photo",
                headers=_HEADERS,
                files={
                    "file": (
                        "big.jpg", big_data, "image/jpeg",
                    ),
                },
            )
        assert resp.status_code == 400

    def test_rejects_small_image(self):
        img_buf = _make_test_image(width=100, height=100)
        with patch(_VERIFY, return_value=_VALID_CLAIMS,
        ):
            resp = client.post(
                "/users/me/photo",
                headers=_HEADERS,
                files={
                    "file": (
                        "tiny.jpg", img_buf, "image/jpeg",
                    ),
                },
            )
        assert resp.status_code == 400


class TestDeletePhoto:
    def test_deletes_photo(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_bucket.blob.return_value = mock_blob

        mock_db = MagicMock()

        with (
            patch(_VERIFY, return_value=_VALID_CLAIMS,
            ),
            patch(
                f"{_STORAGE}._get_bucket",
                return_value=mock_bucket,
            ),
            patch(_ROUTER_FS) as mock_fs,
        ):
            mock_fs.client.return_value = mock_db
            resp = client.delete(
                "/users/me/photo", headers=_HEADERS,
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        mock_blob.delete.assert_called_once()


# ── State machine: incomplete state ──────────────────────────────────────────


class TestIncompleteState:
    def test_incomplete_status_exists(self):
        from app.domain.account_states import AccountStatus

        assert AccountStatus.INCOMPLETE == "incomplete"

    def test_transition_incomplete_to_pending(self):
        from app.domain.account_states import find_transition

        t = find_transition("incomplete", "complete_registration", ["student"])
        assert t is not None
        assert t.target == "pending_approval"

    def test_no_team_actions_from_incomplete(self):
        from app.domain.account_states import get_available_actions

        actions = get_available_actions(
            "incomplete", ["student"], executed_by_filter="team",
        )
        assert len(actions) == 0
