"""TDD tests for Task B3 — `POST /attendance/{doc_id}/justify`.

Wires `JustificationService.justify` (B1) + `JustificationTypeService` (B2)
behind validation: ownership (own record or dependent via `X-Acting-As`),
record must be `absent`, 7-day prazo from the aula date (`timestamp` on the
attendance doc), type must exist and be active, attachment required when
the type's `requiresAttachment=true`.

Uses a lightweight in-memory Firestore fake (mirrors `test_attendance_history.py`)
so the whole endpoint (router -> JustificationTypeService -> JustificationService)
is exercised end-to-end without emulators. Three `firestore.client()` import
bindings are patched to the same fake db: the router's own (ownership/prazo/
type checks) plus the two B1/B2 services it calls into.

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
(sections "Endpoints" -> POST /attendance/{doc_id}/justify,
"Tratamento de erros e casos de borda").
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_UID = "student1"
_OTHER = "other-student"
_GUARDIAN = "guardian1"
_DEP = "dep1"

_VERIFY = "app.security.middleware.verify_id_token"
_ROUTER_FS = "app.routers.attendance.firestore"
_JUSTIFY_FS = "app.services.justification_service.firestore"
_TYPES_FS = "app.services.justification_type_service.firestore"

_HEADERS = {"Authorization": "Bearer tok", "X-Project-Id": _PID}
_CLAIMS = {"uid": _UID, "email": "s@t.com", "projects": {_PID: ["student"]}}
_OTHER_CLAIMS = {
    "uid": _OTHER, "email": "o@t.com", "projects": {_PID: ["student"]},
}
_GUARDIAN_CLAIMS = {
    "uid": _GUARDIAN, "email": "g@t.com", "projects": {_PID: ["guardian"]},
}


# ── In-memory Firestore fake ────────────────────────────────────────────────


class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None
        self.reference = self

    def get(self):
        return self

    def to_dict(self):
        return self._data

    def update(self, patch_data):
        if self._data is None:
            self._data = {}
        self._data.update(patch_data)


class _FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, field, op, value):
        return _FakeQuery(
            [d for d in self._docs if d.to_dict().get(field) == value]
        )

    def stream(self):
        return list(self._docs)


class _FakeCollection:
    def __init__(self, docs_by_id):
        self._docs = docs_by_id

    def document(self, doc_id):
        return _FakeDoc(doc_id, self._docs.get(doc_id))

    def where(self, field, op, value):
        docs = [_FakeDoc(i, d) for i, d in self._docs.items()]
        return _FakeQuery(docs).where(field, op, value)


class _FakeDB:
    def __init__(self, collections):
        self._c = collections

    def collection(self, name):
        return _FakeCollection(self._c.get(name, {}))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(status="absent", user_id=_UID, timestamp=None) -> dict:
    return {
        "projectId": _PID,
        "userId": user_id,
        "status": status,
        "timestamp": timestamp or _now_iso(),
    }


def _type(
    slug="saude", name="Saúde", active=True, requires=False, allows=True,
) -> dict:
    return {
        "projectId": _PID,
        "slug": slug,
        "name": name,
        "allowsAttachment": allows,
        "requiresAttachment": requires,
        "active": active,
        "order": 1,
    }


def _post(doc_id, body, claims, collections, acting_as=None):
    db = _FakeDB(collections)
    headers = dict(_HEADERS)
    if acting_as:
        headers["X-Acting-As"] = acting_as
    with patch(_VERIFY, return_value=claims), \
         patch(_ROUTER_FS) as fs1, \
         patch(_JUSTIFY_FS) as fs2, \
         patch(_TYPES_FS) as fs3:
        fs1.client.return_value = db
        fs2.client.return_value = db
        fs3.client.return_value = db
        return client.post(
            f"/attendance/{doc_id}/justify", headers=headers, json=body,
        )


class TestJustifyAttendanceEndpoint:
    def test_own_record_success(self):
        collections = {
            "attendance": {"att-1": _record()},
            "justification_types": {f"{_PID}_saude": _type()},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "Estava resfriado"},
            _CLAIMS, collections,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "absent_justification_pending"
        just = body["justification"]
        assert just["typeId"] == "saude"
        assert just["typeName"] == "Saúde"
        assert just["text"] == "Estava resfriado"
        assert just["submittedBy"] == _UID
        # underlying doc actually transitioned
        assert (
            collections["attendance"]["att-1"]["status"]
            == "absent_justification_pending"
        )

    def test_third_party_forbidden(self):
        collections = {
            "attendance": {"att-1": _record(user_id=_UID)},
            "justification_types": {f"{_PID}_saude": _type()},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "x"},
            _OTHER_CLAIMS, collections,
        )
        assert r.status_code == 403

    def test_missing_record_404(self):
        collections = {
            "attendance": {},
            "justification_types": {f"{_PID}_saude": _type()},
        }
        r = _post(
            "att-missing", {"typeId": "saude", "text": "x"},
            _CLAIMS, collections,
        )
        assert r.status_code == 404

    def test_non_absent_status_raises_409(self):
        collections = {
            "attendance": {"att-1": _record(status="confirmed")},
            "justification_types": {f"{_PID}_saude": _type()},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "x"}, _CLAIMS, collections,
        )
        assert r.status_code == 409

    def test_past_prazo_raises_422(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        collections = {
            "attendance": {"att-1": _record(timestamp=old)},
            "justification_types": {f"{_PID}_saude": _type()},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "x"}, _CLAIMS, collections,
        )
        assert r.status_code == 422
        assert "prazo" in r.json()["detail"].lower()

    def test_within_prazo_boundary_succeeds(self):
        almost = (
            datetime.now(timezone.utc) - timedelta(days=6, hours=23)
        ).isoformat()
        collections = {
            "attendance": {"att-1": _record(timestamp=almost)},
            "justification_types": {f"{_PID}_saude": _type()},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "x"}, _CLAIMS, collections,
        )
        assert r.status_code == 200, r.text

    def test_inactive_type_raises_422(self):
        collections = {
            "attendance": {"att-1": _record()},
            "justification_types": {
                f"{_PID}_saude": _type(active=False),
            },
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "x"}, _CLAIMS, collections,
        )
        assert r.status_code == 422

    def test_unknown_type_raises_422(self):
        collections = {
            "attendance": {"att-1": _record()},
            "justification_types": {f"{_PID}_saude": _type()},
        }
        r = _post(
            "att-1", {"typeId": "nao-existe", "text": "x"},
            _CLAIMS, collections,
        )
        assert r.status_code == 422

    def test_requires_attachment_without_attachment_raises_422(self):
        collections = {
            "attendance": {"att-1": _record()},
            "justification_types": {
                f"{_PID}_saude": _type(requires=True, allows=True),
            },
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "x"}, _CLAIMS, collections,
        )
        assert r.status_code == 422

    def test_requires_attachment_with_attachment_succeeds(self):
        collections = {
            "attendance": {"att-1": _record()},
            "justification_types": {
                f"{_PID}_saude": _type(requires=True, allows=True),
            },
        }
        body = {
            "typeId": "saude",
            "text": "x",
            "attachment": {
                "url": "https://x/f.jpg", "name": "f.jpg", "size": 1234,
            },
        }
        r = _post("att-1", body, _CLAIMS, collections)
        assert r.status_code == 200, r.text
        assert r.json()["justification"]["attachment"]["name"] == "f.jpg"

    def test_acting_as_guardian_success(self):
        collections = {
            "attendance": {"att-1": _record(user_id=_DEP)},
            "justification_types": {f"{_PID}_saude": _type()},
            "users": {_DEP: {"guardianUid": _GUARDIAN}},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "Viagem em família"},
            _GUARDIAN_CLAIMS, collections, acting_as=_DEP,
        )
        assert r.status_code == 200, r.text
        assert r.json()["justification"]["submittedBy"] == _GUARDIAN

    def test_acting_as_unrelated_guardian_forbidden(self):
        collections = {
            "attendance": {"att-1": _record(user_id=_DEP)},
            "justification_types": {f"{_PID}_saude": _type()},
            "users": {_DEP: {"guardianUid": "someone-else"}},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "x"},
            _GUARDIAN_CLAIMS, collections, acting_as=_DEP,
        )
        assert r.status_code == 403

    def test_acting_as_target_missing_returns_404(self):
        # Fix 1 (B3 review): acting-as target user doc doesn't exist at all
        # -> 404 (mirrors AttendanceService._assert_guardian_of /
        # MedicalHistoryService._assert_guardian_of), not a blanket 403.
        collections = {
            "attendance": {"att-1": _record(user_id=_DEP)},
            "justification_types": {f"{_PID}_saude": _type()},
            "users": {},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "x"},
            _GUARDIAN_CLAIMS, collections, acting_as=_DEP,
        )
        assert r.status_code == 404

    def test_blank_text_returns_422(self):
        # Fix 2 (B3 review): design spec marks `text` as obrigatório —
        # whitespace-only text must be rejected before the service layer.
        collections = {
            "attendance": {"att-1": _record()},
            "justification_types": {f"{_PID}_saude": _type()},
        }
        r = _post(
            "att-1", {"typeId": "saude", "text": "   "}, _CLAIMS, collections,
        )
        assert r.status_code == 422
