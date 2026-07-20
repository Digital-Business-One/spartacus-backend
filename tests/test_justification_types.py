"""Tests for the `justification_types` catalog (Task B2).

Per-project configurable catalog of absence-justification types, modeled on
the `graduation_systems` convention (doc id "{projectId}_{slug}"). Covers:

- Service: list (active-only vs all), upsert validation
  (requiresAttachment ⇒ allowsAttachment), deactivate (never hard-delete).
- Router: GET role-based filtering (student sees active only, staff sees
  all), PUT staff-only + validation, DELETE deactivates.
- Seed: the 4 confirmed types with correct flags/order.

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
(section "Tipos de justificativa").
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.justification_type import JustificationTypeUpsertRequest
from app.services.justification_type_service import JustificationTypeService

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_VERIFY = "app.security.middleware.verify_id_token"
_FS = "app.services.justification_type_service.firestore"

_HEADERS = {"Authorization": "Bearer tok", "X-Project-Id": _PID}
_STAFF_CLAIMS = {
    "uid": "staff1", "email": "s@t.com", "projects": {_PID: ["teacher"]},
}
_STUDENT_CLAIMS = {
    "uid": "student1", "email": "a@t.com", "projects": {_PID: ["student"]},
}


# ── Service: list_types ──────────────────────────────────────────────────


def _type_doc(slug, name, active=True, order=0, allows=True, requires=False):
    d = MagicMock()
    d.to_dict.return_value = {
        "projectId": _PID,
        "slug": slug,
        "name": name,
        "allowsAttachment": allows,
        "requiresAttachment": requires,
        "active": active,
        "order": order,
    }
    return d


class TestListTypes:
    def test_include_inactive_false_filters_active(self):
        docs = [
            _type_doc("saude", "Saúde", active=True, order=1),
            _type_doc("outro", "Outro", active=False, order=4),
        ]
        db = MagicMock()
        db.collection.return_value.where.return_value.stream.return_value = docs
        with patch(_FS) as fs:
            fs.client.return_value = db
            types = JustificationTypeService().list_types(
                _PID, include_inactive=False,
            )
        assert [t.slug for t in types] == ["saude"]

    def test_include_inactive_true_returns_all_sorted_by_order(self):
        docs = [
            _type_doc("outro", "Outro", active=True, order=4),
            _type_doc("saude", "Saúde", active=True, order=1),
        ]
        db = MagicMock()
        db.collection.return_value.where.return_value.stream.return_value = docs
        with patch(_FS) as fs:
            fs.client.return_value = db
            types = JustificationTypeService().list_types(
                _PID, include_inactive=True,
            )
        assert [t.slug for t in types] == ["saude", "outro"]


# ── Service: upsert ───────────────────────────────────────────────────────


class TestUpsert:
    def test_upsert_writes_doc(self):
        db = MagicMock()
        with patch(_FS) as fs:
            fs.client.return_value = db
            body = JustificationTypeUpsertRequest(
                name="Saúde", allows_attachment=True,
                requires_attachment=True, active=True, order=1,
            )
            result = JustificationTypeService().upsert(_PID, "saude", body)
        doc_ref = db.collection.return_value.document
        doc_ref.assert_called_with(f"{_PID}_saude")
        written = doc_ref.return_value.set.call_args[0][0]
        assert written["slug"] == "saude"
        assert written["projectId"] == _PID
        assert written["requiresAttachment"] is True
        assert result.name == "Saúde"

    def test_requires_attachment_without_allows_raises(self):
        body = JustificationTypeUpsertRequest(
            name="Saúde", allows_attachment=False,
            requires_attachment=True, active=True, order=1,
        )
        with pytest.raises(ValueError):
            JustificationTypeService().upsert(_PID, "saude", body)


# ── Service: deactivate ──────────────────────────────────────────────────


class TestDeactivate:
    def test_deactivate_updates_active_false_not_delete(self):
        doc = MagicMock(exists=True)
        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value = doc
        with patch(_FS) as fs:
            fs.client.return_value = db
            JustificationTypeService().deactivate(_PID, "outro")
        doc_ref = db.collection.return_value.document.return_value
        doc_ref.update.assert_called_once_with({"active": False})
        doc_ref.delete.assert_not_called()

    def test_deactivate_missing_raises_lookup_error(self):
        doc = MagicMock(exists=False)
        db = MagicMock()
        db.collection.return_value.document.return_value.get.return_value = doc
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(LookupError):
                JustificationTypeService().deactivate(_PID, "nope")


# ── Router: GET (role-based filtering) ───────────────────────────────────


def _get(claims):
    docs = [
        _type_doc("saude", "Saúde", active=True, order=1),
        _type_doc("outro", "Outro", active=False, order=4),
    ]
    db = MagicMock()
    db.collection.return_value.where.return_value.stream.return_value = docs
    with patch(_VERIFY, return_value=claims), patch(_FS) as fs:
        fs.client.return_value = db
        return client.get(
            f"/projects/{_PID}/justification-types", headers=_HEADERS,
        )


class TestGetRoleGate:
    def test_student_sees_only_active(self):
        r = _get(_STUDENT_CLAIMS)
        assert r.status_code == 200, r.text
        slugs = [t["slug"] for t in r.json()["types"]]
        assert slugs == ["saude"]

    def test_staff_sees_all(self):
        r = _get(_STAFF_CLAIMS)
        assert r.status_code == 200, r.text
        slugs = {t["slug"] for t in r.json()["types"]}
        assert slugs == {"saude", "outro"}


# ── Router: PUT (staff-only + validation) ────────────────────────────────


_BODY = {
    "name": "Saúde",
    "allowsAttachment": True,
    "requiresAttachment": True,
    "active": True,
    "order": 1,
}


def _put(claims, body):
    db = MagicMock()
    with patch(_VERIFY, return_value=claims), patch(_FS) as fs:
        fs.client.return_value = db
        return client.put(
            f"/projects/{_PID}/justification-types/saude",
            headers=_HEADERS, json=body,
        )


class TestPutRoleGate:
    def test_student_forbidden(self):
        r = _put(_STUDENT_CLAIMS, _BODY)
        assert r.status_code == 403

    def test_staff_allowed(self):
        r = _put(_STAFF_CLAIMS, _BODY)
        assert r.status_code == 200, r.text
        assert r.json()["slug"] == "saude"

    def test_requires_attachment_without_allows_is_422(self):
        bad = {**_BODY, "allowsAttachment": False, "requiresAttachment": True}
        r = _put(_STAFF_CLAIMS, bad)
        assert r.status_code == 422


# ── Router: DELETE (deactivates, never hard-deletes) ─────────────────────


def _delete(claims):
    doc = MagicMock(exists=True)
    db = MagicMock()
    db.collection.return_value.document.return_value.get.return_value = doc
    with patch(_VERIFY, return_value=claims), patch(_FS) as fs:
        fs.client.return_value = db
        r = client.delete(
            f"/projects/{_PID}/justification-types/outro", headers=_HEADERS,
        )
        return r, db


class TestDeleteRoleGate:
    def test_student_forbidden(self):
        r, _ = _delete(_STUDENT_CLAIMS)
        assert r.status_code == 403

    def test_staff_deactivates_not_deletes(self):
        r, db = _delete(_STAFF_CLAIMS)
        assert r.status_code == 200, r.text
        doc_ref = db.collection.return_value.document.return_value
        doc_ref.update.assert_called_once_with({"active": False})
        doc_ref.delete.assert_not_called()


# ── Seed ──────────────────────────────────────────────────────────────────


class TestSeed:
    def test_seed_has_4_types_with_correct_flags(self):
        from seeds.seed_justification_types import _types

        types = _types(_PID)
        assert len(types) == 4
        by_slug = {t["slug"]: t for t in types}
        assert set(by_slug) == {
            "saude", "viagem", "compromisso-escolar", "outro",
        }

        saude = by_slug["saude"]
        assert saude["requiresAttachment"] is True
        assert saude["allowsAttachment"] is True
        assert saude["name"] == "Saúde"

        for slug in ("viagem", "compromisso-escolar", "outro"):
            assert by_slug[slug]["requiresAttachment"] is False

        orders = sorted(t["order"] for t in types)
        assert orders == [1, 2, 3, 4]
        assert all(t["active"] is True for t in types)
        assert all(t["projectId"] == _PID for t in types)
