from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_UID = "user123"
_VERIFY = "app.security.middleware.verify_id_token"
_FS = "app.services.donation_service.firestore"

_HEADERS = {
    "Authorization": "Bearer tok",
    "X-Project-Id": _PID,
}
_CLAIMS = {
    "uid": _UID,
    "email": "t@t.com",
    "projects": {_PID: ["student"]},
}
_ADMIN_CLAIMS = {
    "uid": _UID,
    "email": "t@t.com",
    "projects": {_PID: ["owner"]},
}


def _mock_no_existing():
    """DB with no existing donation this month."""
    mock_db = MagicMock()
    doacoes = MagicMock()
    q = doacoes.where.return_value
    q = q.where.return_value.where.return_value
    q.limit.return_value.stream.return_value = []
    mock_ref = MagicMock()
    mock_ref.id = "don_123"
    doacoes.add.return_value = (None, mock_ref)
    mock_db.collection.return_value = doacoes
    return mock_db


def _mock_existing():
    """DB with existing donation this month."""
    mock_db = MagicMock()
    doacoes = MagicMock()
    existing = MagicMock()
    existing.id = "don_existing"
    existing.to_dict.return_value = {
        "item": "coffee",
        "itemDescription": None,
        "month": "2026-03",
        "status": "pledged",
        "createdAt": "2026-03-15T10:00:00Z",
    }
    q = doacoes.where.return_value
    q = q.where.return_value.where.return_value
    q.limit.return_value.stream.return_value = [existing]
    mock_db.collection.return_value = doacoes
    return mock_db


class TestCreateDonation:
    def test_creates_donation(self):
        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = _mock_no_existing()
            r = client.post(
                "/donations",
                headers=_HEADERS,
                json={"item": "coffee"},
            )
        assert r.status_code == 201
        d = r.json()
        assert d["item"] == "coffee"
        assert d["status"] == "pledged"
        assert d["itemLabel"] == "1 pacote de café"

    def test_duplicate_returns_409(self):
        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = _mock_existing()
            r = client.post(
                "/donations",
                headers=_HEADERS,
                json={"item": "cookies"},
            )
        assert r.status_code == 409

    def test_other_requires_description(self):
        with patch(_VERIFY, return_value=_CLAIMS):
            r = client.post(
                "/donations",
                headers=_HEADERS,
                json={"item": "other"},
            )
        assert r.status_code == 422

    def test_other_with_description_ok(self):
        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = _mock_no_existing()
            r = client.post(
                "/donations",
                headers=_HEADERS,
                json={
                    "item": "other",
                    "itemDescription": "Materiais esportivos",
                },
            )
        assert r.status_code == 201


class TestGetCurrentDonation:
    def test_returns_current(self):
        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = _mock_existing()
            r = client.get(
                "/donations/current", headers=_HEADERS,
            )
        assert r.status_code == 200
        assert r.json()["item"] == "coffee"

    def test_no_donation_returns_404(self):
        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = _mock_no_existing()
            r = client.get(
                "/donations/current", headers=_HEADERS,
            )
        assert r.status_code == 404


class TestDonationConfig:
    def test_get_default_config(self):
        mock_db = MagicMock()
        proj = MagicMock()
        proj.exists = True
        proj.to_dict.return_value = {}
        mock_db.collection.return_value.document.return_value.get.return_value = proj

        with (
            patch(_VERIFY, return_value=_CLAIMS),
            patch(_FS) as fs,
        ):
            fs.client.return_value = mock_db
            r = client.get(
                f"/projects/{_PID}/donation-config",
                headers=_HEADERS,
            )
        assert r.status_code == 200
        d = r.json()
        assert len(d["items"]) == 5
        assert d["items"][0]["code"] == "food_1kg"
