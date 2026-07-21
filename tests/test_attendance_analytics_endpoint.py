"""TDD tests for Task A7 — `GET /attendance/analytics/{classId}` (new, staff).

Aggregated view of ONE turma across all its students: roll-up
(`averagePercent`, `totals`, `byBelt[]`) + a per-student `students[]` list.
Reuses A6's per-turma/per-student counting window (`_turma_window`,
`_parse_local_dt`) and the pure `aggregate_attendance` engine — this suite
does not re-verify the window/percent math itself (covered by
`test_attendance_history.py` / `test_attendance_analytics.py`), only the
new endpoint's role gate, engine gate, month param and payload shape.

See: docs/superpowers/specs/2026-07-18-frequencia-analitica-design.md
(section "Endpoints" -> GET /attendance/analytics/{classId}).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_CLASS_ID = "t1"
_VERIFY = "app.security.middleware.verify_id_token"
_FS = "app.services.attendance_service.firestore"
_TZ_OFFSET = timezone(timedelta(hours=-4))

_HEADERS = {"Authorization": "Bearer tok", "X-Project-Id": _PID}
_STAFF_CLAIMS = {
    "uid": "staff1", "email": "s@t.com", "projects": {_PID: ["teacher"]},
}
_STUDENT_CLAIMS = {
    "uid": "student1", "email": "a@t.com", "projects": {_PID: ["student"]},
}


# ── In-memory Firestore fake (same shape as test_attendance_history.py) ─────


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


class _FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, field, op, value):
        if op == "array_contains":
            return _FakeQuery(
                [
                    d for d in self._docs
                    if value in (d.to_dict().get(field) or [])
                ]
            )
        return _FakeQuery(
            [d for d in self._docs if d.to_dict().get(field) == value]
        )

    def limit(self, n):
        return _FakeQuery(self._docs[:n])

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

    def get_all(self, refs):
        return list(refs)


def _class(engine=True, start_date="2026-07-01", modality_id="mod1"):
    return {
        "projectId": _PID,
        "name": "Turma A",
        "attendanceEngineEnabled": engine,
        "attendanceStartDate": start_date,
        "modalityId": modality_id,
    }


def _modality(slug="jiu-jitsu"):
    return {"slug": slug, "name": "Jiu-Jitsu"}


def _user(
    name="Aluno",
    created_at="2026-07-01T00:00:00-04:00",
    graduation=None,
    photo_url=None,
    class_ids=(_CLASS_ID,),
):
    return {
        "name": name,
        "createdAt": created_at,
        "graduation": graduation,
        "photoUrl": photo_url,
        "classIds": list(class_ids),
    }


def _att(
    doc_id,
    user_id,
    status="confirmed",
    timestamp="2026-07-16T10:00:00-04:00",
    turma_id=_CLASS_ID,
    modality_slug="jiu-jitsu",
    graduation_snapshot=None,
):
    return doc_id, {
        "projectId": _PID,
        "userId": user_id,
        "turmaId": turma_id,
        "status": status,
        "timestamp": timestamp,
        "modalitySlug": modality_slug,
        "graduationSnapshot": graduation_snapshot,
    }


def _build_db(attendance, classes=None, users=None, modalities=None):
    return _FakeDB(
        {
            "users": users or {},
            "classes": classes if classes is not None else {_CLASS_ID: _class()},
            "modalities": (
                modalities if modalities is not None else {"mod1": _modality()}
            ),
            "attendance": dict(attendance),
        }
    )


def _get(fake_db, claims=None, params=None, class_id=_CLASS_ID):
    with (
        patch(_VERIFY, return_value=claims or _STAFF_CLAIMS),
        patch(_FS) as fs,
    ):
        fs.client.return_value = fake_db
        return client.get(
            f"/attendance/analytics/{class_id}",
            headers=_HEADERS,
            params=params or {},
        )


# ── Role gate ─────────────────────────────────────────────────────────────


class TestRoleGate:
    def test_student_forbidden(self):
        db = _build_db([_att("a1", "u1")], users={"u1": _user()})
        r = _get(db, claims=_STUDENT_CLAIMS, params={"month": "2026-07"})
        assert r.status_code == 403

    def test_teacher_allowed(self):
        db = _build_db([_att("a1", "u1")], users={"u1": _user()})
        r = _get(db, params={"month": "2026-07"})
        assert r.status_code == 200, r.text


# ── Engine gate ───────────────────────────────────────────────────────────


class TestEngineGate:
    def test_engine_off_returns_409(self):
        db = _build_db(
            [_att("a1", "u1")],
            classes={_CLASS_ID: _class(engine=False)},
            users={"u1": _user()},
        )
        r = _get(db, params={"month": "2026-07"})
        assert r.status_code == 409
        assert r.json()["detail"]

    def test_engine_on_without_start_date_returns_409(self):
        db = _build_db(
            [_att("a1", "u1")],
            classes={_CLASS_ID: _class(engine=True, start_date=None)},
            users={"u1": _user()},
        )
        r = _get(db, params={"month": "2026-07"})
        assert r.status_code == 409

    def test_unknown_class_returns_404(self):
        db = _build_db([], classes={})
        r = _get(db, params={"month": "2026-07"})
        assert r.status_code == 404


# ── Month param ───────────────────────────────────────────────────────────


class TestMonthParam:
    def test_explicit_month_filters_records(self):
        db = _build_db(
            [
                _att("a1", "u1", timestamp="2026-07-16T10:00:00-04:00"),
                _att("a2", "u1", timestamp="2026-08-01T10:00:00-04:00"),
            ],
            users={"u1": _user()},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["totals"]["confirmed"] == 1

    def test_default_month_is_current_month(self):
        now = datetime.now(_TZ_OFFSET)
        ts = now.replace(
            hour=10, minute=0, second=0, microsecond=0,
        ).isoformat()
        db = _build_db(
            [_att("a1", "u1", timestamp=ts)],
            classes={_CLASS_ID: _class(start_date="2020-01-01")},
            users={"u1": _user(created_at="2020-01-01T00:00:00-04:00")},
        )
        # No `month` param — should default to the current month and
        # therefore include this record.
        body = _get(db).json()
        assert body["totals"]["confirmed"] == 1


# ── Window (per-student, reused from A6) ───────────────────────────────────


class TestWindow:
    def test_pre_window_record_excluded(self):
        db = _build_db(
            [_att("a1", "u1", timestamp="2026-06-10T10:00:00-04:00")],
            classes={_CLASS_ID: _class(start_date="2026-07-01")},
            users={"u1": _user(created_at="2026-07-01T00:00:00-04:00")},
        )
        body = _get(db, params={"month": "2026-06"}).json()
        assert body["totals"]["confirmed"] == 0
        # u1 is still enrolled (roster) — shown as inactive, not omitted.
        assert len(body["students"]) == 1
        assert body["students"][0]["percent"] is None

    def test_window_uses_max_of_user_created_at_and_start_date(self):
        db = _build_db(
            [_att("a1", "u1", timestamp="2026-07-05T10:00:00-04:00")],
            classes={_CLASS_ID: _class(start_date="2026-07-01")},
            users={"u1": _user(created_at="2026-07-10T00:00:00-04:00")},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["totals"]["confirmed"] == 0


# ── Payload shape ────────────────────────────────────────────────────────


class TestPayload:
    def test_empty_turma_consistent_payload(self):
        db = _build_db([])
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["averagePercent"] is None
        assert body["totals"] == {
            "confirmed": 0,
            "absent": 0,
            "absentJustified": 0,
            "justificationPending": 0,
            "awaitingConfirmation": 0,
        }
        assert body["byBelt"] == []
        assert body["students"] == []

    def test_totals_pooled_across_students(self):
        db = _build_db(
            [
                _att("a1", "u1", status="confirmed"),
                _att("a2", "u2", status="absent"),
            ],
            users={"u1": _user(), "u2": _user(name="Aluno 2")},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["totals"]["confirmed"] == 1
        assert body["totals"]["absent"] == 1

    def test_average_percent_is_mean_of_student_percents(self):
        db = _build_db(
            [
                _att("a1", "u1", status="confirmed"),
                _att("a2", "u2", status="absent"),
            ],
            users={"u1": _user(), "u2": _user(name="Aluno 2")},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        # u1: percent 1.0 (1 confirmed / 1); u2: percent 0.0 (0/1) -> mean 0.5
        assert body["averagePercent"] == 0.5

    def test_students_shape_and_has_pending_justification(self):
        db = _build_db(
            [
                _att(
                    "a1", "u1",
                    status="absent_justification_pending",
                ),
            ],
            users={"u1": _user(name="Fulano", photo_url="http://x/p.jpg")},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert len(body["students"]) == 1
        s = body["students"][0]
        assert s["userId"] == "u1"
        assert s["name"] == "Fulano"
        assert s["photoUrl"] == "http://x/p.jpg"
        assert s["hasPendingJustification"] is True
        assert s["counts"]["justificationPending"] == 1
        assert s["percent"] == 0.0

    def test_enrolled_student_with_no_activity_shown_as_inactive(self):
        # u1 is on the turma roster (classIds contains the turma) but has
        # no countable record in the selected month — must still appear,
        # with percent=None and zero counts, not be dropped.
        db = _build_db(
            [_att("a1", "u1", timestamp="2026-08-01T10:00:00-04:00")],
            users={"u1": _user()},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert len(body["students"]) == 1
        s = body["students"][0]
        assert s["userId"] == "u1"
        assert s["percent"] is None
        assert s["counts"] == {
            "confirmed": 0,
            "absent": 0,
            "absentJustified": 0,
            "justificationPending": 0,
            "awaitingConfirmation": 0,
        }

    def test_enrolled_student_with_zero_attendance_records_shown(self):
        # u2 is enrolled (roster) but has NO attendance doc at all for this
        # turma — must still appear as an inactive student.
        db = _build_db(
            [_att("a1", "u1", status="confirmed")],
            users={"u1": _user(), "u2": _user(name="Aluno 2")},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        user_ids = {s["userId"] for s in body["students"]}
        assert user_ids == {"u1", "u2"}
        u2 = next(s for s in body["students"] if s["userId"] == "u2")
        assert u2["percent"] is None
        assert u2["counts"]["confirmed"] == 0

    def test_non_enrolled_student_with_records_not_shown(self):
        # u1 has attendance records for this turma but is NOT on the
        # current roster (e.g. removed from the turma later) — excluded
        # from `students[]`, matching "the turma roster" as the universe.
        db = _build_db(
            [_att("a1", "u1", status="confirmed")],
            users={"u1": _user(class_ids=())},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["students"] == []


# ── byBelt ───────────────────────────────────────────────────────────────


class TestByBelt:
    def test_grouped_by_current_graduation(self):
        grad = {"jiu-jitsu": {"belt": "blue", "degree": 2, "status": "approved"}}
        db = _build_db(
            [
                _att("a1", "u1", status="confirmed"),
                _att("a2", "u2", status="absent"),
            ],
            users={
                "u1": _user(graduation=grad),
                "u2": _user(name="Aluno 2", graduation=grad),
            },
        )
        by_belt = _get(db, params={"month": "2026-07"}).json()["byBelt"]
        assert len(by_belt) == 1
        b = by_belt[0]
        assert b["key"] == "blue:2"
        assert b["belt"] == "blue"
        assert b["degree"] == 2
        assert b["studentCount"] == 2
        assert b["averagePercent"] == 0.5

    def test_no_graduation_bucket(self):
        db = _build_db(
            [_att("a1", "u1", status="confirmed")],
            users={"u1": _user(graduation=None)},
        )
        by_belt = _get(db, params={"month": "2026-07"}).json()["byBelt"]
        assert len(by_belt) == 1
        assert by_belt[0]["key"] == "no_graduation"
        assert by_belt[0]["studentCount"] == 1


# ── pendingJustifications (Task B5) ─────────────────────────────────────────


def _pending_justification_doc(
    user_id="u1",
    timestamp="2026-07-16T10:00:00-04:00",
    type_id="saude",
    type_name="Saúde",
    text="Estava doente",
    attachment=None,
):
    return {
        "projectId": _PID,
        "userId": user_id,
        "turmaId": _CLASS_ID,
        "status": "absent_justification_pending",
        "timestamp": timestamp,
        "modalitySlug": "jiu-jitsu",
        "graduationSnapshot": None,
        "justification": {
            "typeId": type_id,
            "typeName": type_name,
            "text": text,
            "attachment": attachment,
            "submittedAt": "2026-07-16T12:00:00-04:00",
            "submittedBy": user_id,
            "reviewedAt": None,
            "reviewedBy": None,
            "reviewedByName": None,
            "rejectReason": None,
        },
    }


class TestPendingJustifications:
    def test_included_with_expected_fields(self):
        db = _build_db(
            [("a1", _pending_justification_doc())],
            users={"u1": _user(name="Fulano")},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert len(body["pendingJustifications"]) == 1
        pj = body["pendingJustifications"][0]
        assert pj["attendanceId"] == "a1"
        assert pj["userId"] == "u1"
        assert pj["studentName"] == "Fulano"
        assert pj["aulaDate"]
        assert pj["typeId"] == "saude"
        assert pj["typeName"] == "Saúde"
        assert pj["text"] == "Estava doente"
        assert pj["attachment"] is None

    def test_includes_attachment_when_present(self):
        attachment = {"url": "https://x/f.jpg", "name": "f.jpg", "size": 123}
        db = _build_db(
            [("a1", _pending_justification_doc(attachment=attachment))],
            users={"u1": _user(name="Fulano")},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        pj = body["pendingJustifications"][0]
        assert pj["attachment"] == attachment

    def test_confirmed_records_not_included(self):
        db = _build_db(
            [_att("a1", "u1", status="confirmed")],
            users={"u1": _user()},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["pendingJustifications"] == []

    def test_filtered_by_month(self):
        db = _build_db(
            [(
                "a1",
                _pending_justification_doc(timestamp="2026-08-01T10:00:00-04:00"),
            )],
            users={"u1": _user()},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["pendingJustifications"] == []

    def test_non_roster_student_excluded(self):
        db = _build_db(
            [("a1", _pending_justification_doc())],
            users={"u1": _user(class_ids=())},
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["pendingJustifications"] == []

    def test_empty_turma_has_empty_pending_justifications(self):
        db = _build_db([])
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["pendingJustifications"] == []
