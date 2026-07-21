"""TDD tests for Task B5 — staff approve/reject endpoints.

`PATCH /attendance/{doc_id}/justification/approve` and
`PATCH /attendance/{doc_id}/justification/reject` wire the router (staff
role gate, same as `GET /attendance/dashboard|analytics/{classId}`) to
`JustificationService.approve`/`.reject` (B1), which already validate the
origin status (`absent_justification_pending` -> 409 otherwise) and, for
reject, the non-empty `reason` (-> 422). This suite exercises the HTTP
wiring end-to-end: role gate, happy path, and origin-status 409 — the
transition matrix itself is covered by `test_justification_service.py`.

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
(sections "Endpoints" -> PATCH .../justification/approve|reject,
"Fluxo do staff").
"""

from unittest.mock import patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_STAFF = "staff1"
_STUDENT = "student1"

_VERIFY = "app.security.middleware.verify_id_token"
_ROUTER_FS = "app.routers.attendance.firestore"
_JUSTIFY_FS = "app.services.justification_service.firestore"

_HEADERS = {"Authorization": "Bearer tok", "X-Project-Id": _PID}
_STAFF_CLAIMS = {
    "uid": _STAFF, "email": "s@t.com", "projects": {_PID: ["teacher"]},
}
_STUDENT_CLAIMS = {
    "uid": _STUDENT, "email": "a@t.com", "projects": {_PID: ["student"]},
}


# ── In-memory Firestore fake (mirrors test_justify_attendance_endpoint.py) ──


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


class _FakeCollection:
    def __init__(self, docs_by_id):
        self._docs = docs_by_id

    def document(self, doc_id):
        return _FakeDoc(doc_id, self._docs.get(doc_id))


class _FakeDB:
    def __init__(self, collections):
        self._c = collections

    def collection(self, name):
        return _FakeCollection(self._c.get(name, {}))


def _pending_record(**overrides) -> dict:
    base = {
        "projectId": _PID,
        "userId": _STUDENT,
        "status": "absent_justification_pending",
        "timestamp": "2026-07-10T10:00:00-04:00",
        "justification": {
            "typeId": "saude",
            "typeName": "Saúde",
            "text": "Estava doente",
            "attachment": None,
            "submittedAt": "2026-07-10T12:00:00-04:00",
            "submittedBy": _STUDENT,
            "reviewedAt": None,
            "reviewedBy": None,
            "reviewedByName": None,
            "rejectReason": None,
        },
    }
    base.update(overrides)
    return base


def _patch(path: str, claims: dict, collections: dict, body: dict | None = None):
    db = _FakeDB(collections)
    with (
        patch(_VERIFY, return_value=claims),
        patch(_ROUTER_FS) as fs1,
        patch(_JUSTIFY_FS) as fs2,
    ):
        fs1.client.return_value = db
        fs2.client.return_value = db
        return client.patch(
            f"/attendance/{path}", headers=_HEADERS, json=body,
        )


class TestApprove:
    def test_staff_can_approve_pending(self):
        collections = {
            "attendance": {"att-1": _pending_record()},
            "users": {_STAFF: {"name": "Staff Um"}},
        }
        r = _patch(
            "att-1/justification/approve", _STAFF_CLAIMS, collections,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "absent_justified"
        assert body["justification"]["reviewedBy"] == _STAFF
        assert body["justification"]["reviewedByName"] == "Staff Um"
        assert (
            collections["attendance"]["att-1"]["status"] == "absent_justified"
        )

    def test_student_forbidden(self):
        collections = {
            "attendance": {"att-1": _pending_record()},
            "users": {_STAFF: {"name": "Staff Um"}},
        }
        r = _patch(
            "att-1/justification/approve", _STUDENT_CLAIMS, collections,
        )
        assert r.status_code == 403

    def test_already_resolved_raises_409(self):
        collections = {
            "attendance": {
                "att-1": _pending_record(status="absent_justified"),
            },
            "users": {_STAFF: {"name": "Staff Um"}},
        }
        r = _patch(
            "att-1/justification/approve", _STAFF_CLAIMS, collections,
        )
        assert r.status_code == 409

    def test_missing_record_404(self):
        collections = {"attendance": {}, "users": {}}
        r = _patch(
            "att-missing/justification/approve", _STAFF_CLAIMS, collections,
        )
        assert r.status_code == 404


class TestReject:
    def test_staff_can_reject_pending_with_reason(self):
        collections = {
            "attendance": {"att-1": _pending_record()},
            "users": {_STAFF: {"name": "Staff Um"}},
        }
        r = _patch(
            "att-1/justification/reject",
            _STAFF_CLAIMS,
            collections,
            body={"reason": "Sem comprovação"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "absent"
        assert body["justification"]["rejectReason"] == "Sem comprovação"
        assert body["justification"]["reviewedBy"] == _STAFF
        assert collections["attendance"]["att-1"]["status"] == "absent"

    def test_student_forbidden(self):
        collections = {
            "attendance": {"att-1": _pending_record()},
            "users": {_STAFF: {"name": "Staff Um"}},
        }
        r = _patch(
            "att-1/justification/reject",
            _STUDENT_CLAIMS,
            collections,
            body={"reason": "x"},
        )
        assert r.status_code == 403

    def test_missing_reason_raises_422(self):
        collections = {
            "attendance": {"att-1": _pending_record()},
            "users": {_STAFF: {"name": "Staff Um"}},
        }
        r = _patch(
            "att-1/justification/reject",
            _STAFF_CLAIMS,
            collections,
            body={"reason": ""},
        )
        assert r.status_code == 422

    def test_reason_field_absent_raises_422(self):
        collections = {
            "attendance": {"att-1": _pending_record()},
            "users": {_STAFF: {"name": "Staff Um"}},
        }
        r = _patch(
            "att-1/justification/reject", _STAFF_CLAIMS, collections, body={},
        )
        assert r.status_code == 422

    def test_already_resolved_raises_409(self):
        collections = {
            "attendance": {"att-1": _pending_record(status="absent")},
            "users": {_STAFF: {"name": "Staff Um"}},
        }
        r = _patch(
            "att-1/justification/reject",
            _STAFF_CLAIMS,
            collections,
            body={"reason": "Sem comprovação"},
        )
        assert r.status_code == 409
