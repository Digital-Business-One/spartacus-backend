"""TDD tests for Task A6 — `GET /attendance/history` extended.

Covers: per-turma counting window (engine gate + max(createdAt, startDate)),
the new conservative % formula, byGraduation breakdown, the month/modality/
belt/degree filters, retrocompatibility of the legacy payload, and the
guardian `X-Acting-As` path.

Uses a lightweight in-memory Firestore fake so the whole endpoint (router →
service → pure aggregation) is exercised end-to-end without emulators.

See: docs/superpowers/specs/2026-07-18-frequencia-analitica-design.md
(sections "Endpoints" → GET /attendance/history, "Janela de contagem",
"Fórmula do %").
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_UID = "student1"
_GUARDIAN = "guardian1"
_DEP = "dep1"
_VERIFY = "app.security.middleware.verify_id_token"
_FS = "app.services.attendance_service.firestore"

_TZ = timezone(timedelta(hours=-4))  # Brasnorte-MT = UTC-4, mirrors service

_HEADERS = {"Authorization": "Bearer tok", "X-Project-Id": _PID}
_CLAIMS = {"uid": _UID, "email": "s@t.com", "projects": {_PID: ["student"]}}
_GUARDIAN_CLAIMS = {
    "uid": _GUARDIAN,
    "email": "g@t.com",
    "projects": {_PID: ["guardian"]},
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


class _FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, field, op, value):
        if op == ">=":
            return _FakeQuery(
                [
                    d for d in self._docs
                    if (d.to_dict().get(field) or "") >= value
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


def _att(
    doc_id,
    status="confirmed",
    timestamp="2026-07-16T10:00:00-04:00",
    turma_id="t1",
    modality_slug="jiu-jitsu",
    graduation_snapshot=None,
    user_id=_UID,
):
    return doc_id, {
        "projectId": _PID,
        "userId": user_id,
        "turmaId": turma_id,
        "turmaName": "Turma A",
        "status": status,
        "timestamp": timestamp,
        "modalitySlug": modality_slug,
        "graduationSnapshot": graduation_snapshot,
    }


def _class(
    engine=True, start_date="2026-07-15", modality="Jiu-Jitsu",
):
    return {
        "name": "Turma A",
        "modality": modality,
        "attendanceEngineEnabled": engine,
        "attendanceStartDate": start_date,
        "schedule": [],
    }


def _months_5_back_first_day():
    """Mirror `AttendanceService._last_n_months`'s earliest boundary.

    The default (no year/months params) history window is the last 6
    calendar months ending at "today" — i.e. today's month plus the 5
    preceding ones. This returns the first day of the earliest of those
    months, matching what used to be `legacy_lower_bound` in the service.
    """
    today = datetime.now(_TZ).date()
    y, m = today.year, today.month
    for _ in range(5):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return datetime(y, m, 1, 10, 0, tzinfo=_TZ)


def _build_db(
    attendance,
    classes=None,
    user_created_at="2026-07-15T00:00:00-04:00",
    class_ids=("t1",),
    extra_users=None,
):
    users = {
        _UID: {
            "name": "Aluno",
            "classIds": list(class_ids),
            "createdAt": user_created_at,
        }
    }
    if extra_users:
        users.update(extra_users)
    return _FakeDB(
        {
            "users": users,
            "classes": classes or {"t1": _class()},
            "modalities": {},
            "attendance": dict(attendance),
        }
    )


def _get(fake_db, claims=None, params=None, headers=None):
    with (
        patch(_VERIFY, return_value=claims or _CLAIMS),
        patch(_FS) as fs,
    ):
        fs.client.return_value = fake_db
        return client.get(
            "/attendance/history",
            headers=headers or _HEADERS,
            params=params or {},
        )


# ── Per-turma window ────────────────────────────────────────────────────────


class TestWindow:
    def test_pre_window_record_excluded(self):
        db = _build_db(
            [
                _att("a1", timestamp="2026-07-10T10:00:00-04:00"),
                _att("a2", timestamp="2026-07-16T10:00:00-04:00"),
            ]
        )
        r = _get(db)
        assert r.status_code == 200, r.text
        assert r.json()["counts"]["confirmed"] == 1

    def test_engine_off_turma_records_excluded(self):
        db = _build_db(
            [_att("a1")],
            classes={"t1": _class(engine=False)},
        )
        body = _get(db).json()
        assert body["counts"]["confirmed"] == 0
        assert body["percent"] is None

    def test_enabled_without_start_date_excluded(self):
        db = _build_db(
            [_att("a1")],
            classes={"t1": _class(engine=True, start_date=None)},
        )
        assert _get(db).json()["counts"]["confirmed"] == 0

    def test_window_uses_max_of_created_at_and_start_date(self):
        # startDate is early; createdAt (07-15) is the binding edge.
        db = _build_db(
            [
                _att("a1", timestamp="2026-07-10T10:00:00-04:00"),
                _att("a2", timestamp="2026-07-16T10:00:00-04:00"),
            ],
            classes={"t1": _class(start_date="2026-07-01")},
            user_created_at="2026-07-15T00:00:00-04:00",
        )
        assert _get(db).json()["counts"]["confirmed"] == 1

    def test_record_from_unknown_turma_excluded(self):
        db = _build_db(
            [_att("a1", turma_id="ghost")],
            classes={"t1": _class()},
        )
        assert _get(db).json()["counts"]["confirmed"] == 0

    def test_multi_turma_only_engine_on_turma_windowed_and_counted(self):
        # Single user, two turmas in the same request: t1 (engine OFF) and
        # t2 (engine ON, own attendanceStartDate). t1's records must be
        # fully excluded (engine off, regardless of timestamp); t2's
        # records must be windowed by t2's own start date, independently.
        db = _build_db(
            [
                _att(
                    "a1", turma_id="t1", status="confirmed",
                    timestamp="2026-07-16T10:00:00-04:00",
                ),
                _att(
                    "b1", turma_id="t2", status="confirmed",
                    timestamp="2026-07-05T10:00:00-04:00",  # before t2 window
                ),
                _att(
                    "b2", turma_id="t2", status="confirmed",
                    timestamp="2026-07-16T10:00:00-04:00",  # after t2 window
                ),
                _att(
                    "b3", turma_id="t2", status="absent",
                    timestamp="2026-07-20T10:00:00-04:00",  # after t2 window
                ),
            ],
            classes={
                "t1": _class(engine=False),
                "t2": _class(engine=True, start_date="2026-07-10"),
            },
            user_created_at="2026-01-01T00:00:00-04:00",
            class_ids=("t1", "t2"),
        )
        body = _get(db).json()
        # t1 (engine off) fully excluded; t2 windowed to b2 (confirmed) and
        # b3 (absent) only — b1 dropped as pre-window, a1 dropped entirely.
        assert body["counts"]["confirmed"] == 1
        assert body["counts"]["absent"] == 1
        assert body["percent"] == 0.5


class TestQueryLowerBoundRegression:
    """Regression: query lower bound must not depend on CURRENT enrollment.

    A previous version bounded the Firestore query at
    `min(legacy_lower_bound, *turma_windows)`, where `turma_windows` was
    computed only over the user's CURRENT `classIds`. Since `classIds` is
    mutable (users leave turmas), an in-window record belonging to a turma
    the user has since LEFT — whose own effective window starts earlier
    than both `legacy_lower_bound` and the current-enrollment turma(s)'
    windows — was silently dropped by the query itself, before it ever
    reached the (correct) per-turma windowing in `_compute_analytics`.

    The fix bounds the query at `user.createdAt` alone, which is always
    <= every effective per-turma window (`max(user.createdAt,
    turma.attendanceStartDate)`), so it can never drop a record that the
    in-memory per-turma filter would otherwise keep.
    """

    def test_left_turma_in_window_record_not_dropped_by_query_bound(self):
        legacy_bound = _months_5_back_first_day()

        # t1: turma the user has LEFT — engine on, own (early) start date.
        t1_start = legacy_bound - timedelta(days=60)
        # user created even earlier, so t1's effective window == t1_start.
        user_created = t1_start - timedelta(days=30)
        # record sits inside t1's window (after t1_start) but BEFORE the
        # old `legacy_lower_bound`-derived query cutoff — exactly the
        # record the old query bound silently excluded.
        record_ts = legacy_bound - timedelta(days=10)

        # t2: turma the user is CURRENTLY enrolled in — started recently,
        # well after `legacy_bound`. Under the old code, current-enrollment
        # turma_windows would only include t2's (recent) window, and
        # min(legacy_bound, t2_window) == legacy_bound — later than
        # `record_ts`, so the query dropped it.
        t2_start = datetime.now(_TZ) - timedelta(days=3)

        db = _build_db(
            [
                _att(
                    "a1",
                    turma_id="t1",
                    status="confirmed",
                    timestamp=record_ts.isoformat(),
                ),
            ],
            classes={
                "t1": _class(engine=True, start_date=t1_start.date().isoformat()),
                "t2": _class(engine=True, start_date=t2_start.date().isoformat()),
            },
            user_created_at=user_created.isoformat(),
            class_ids=("t2",),  # user left t1; only enrolled in t2 now
        )
        body = _get(db).json()
        assert body["counts"]["confirmed"] >= 1, body


# ── New % formula ───────────────────────────────────────────────────────────


class TestPercent:
    def test_pending_counts_as_absence_in_denominator(self):
        db = _build_db(
            [
                _att("a1", status="confirmed"),
                _att("a2", status="absent_justification_pending"),
            ]
        )
        body = _get(db).json()
        assert body["counts"]["justificationPending"] == 1
        assert body["percent"] == 0.5

    def test_justified_is_neutral(self):
        db = _build_db(
            [
                _att("a1", status="confirmed"),
                _att("a2", status="absent_justified"),
            ]
        )
        body = _get(db).json()
        assert body["counts"]["absentJustified"] == 1
        assert body["percent"] == 1.0

    def test_all_five_categories_present(self):
        db = _build_db(
            [
                _att("a1", status="confirmed"),
                _att("a2", status="absent"),
                _att("a3", status="absent_justified"),
                _att("a4", status="absent_justification_pending"),
                _att("a5", status="registered"),
            ]
        )
        counts = _get(db).json()["counts"]
        assert counts == {
            "confirmed": 1,
            "absent": 1,
            "absentJustified": 1,
            "justificationPending": 1,
            "awaitingConfirmation": 1,
        }


# ── byGraduation ────────────────────────────────────────────────────────────


class TestByGraduation:
    def test_present_and_grouped(self):
        snap = {"belt": "blue", "degree": 2, "status": "approved"}
        db = _build_db(
            [
                _att("a1", status="confirmed", graduation_snapshot=snap),
                _att("a2", status="absent", graduation_snapshot=snap),
            ]
        )
        by_grad = _get(db).json()["byGraduation"]
        assert len(by_grad) == 1
        g = by_grad[0]
        assert g["key"] == "blue:2"
        assert g["belt"] == "blue"
        assert g["degree"] == 2
        assert g["percent"] == 0.5

    def test_null_snapshot_bucket(self):
        db = _build_db([_att("a1", graduation_snapshot=None)])
        keys = {g["key"] for g in _get(db).json()["byGraduation"]}
        assert keys == {"no_graduation"}


# ── Filters ─────────────────────────────────────────────────────────────────


class TestFilters:
    def test_month_filter(self):
        db = _build_db(
            [
                _att("a1", timestamp="2026-07-16T10:00:00-04:00"),
                _att("a2", timestamp="2026-08-01T10:00:00-04:00"),
            ]
        )
        body = _get(db, params={"month": "2026-07"}).json()
        assert body["counts"]["confirmed"] == 1

    def test_modality_filter(self):
        db = _build_db(
            [
                _att("a1", modality_slug="jiu-jitsu"),
                _att("a2", modality_slug="muay-thai"),
            ]
        )
        body = _get(db, params={"modality": "jiu-jitsu"}).json()
        assert body["counts"]["confirmed"] == 1

    def test_belt_filter(self):
        db = _build_db(
            [
                _att(
                    "a1",
                    graduation_snapshot={
                        "belt": "blue", "degree": 2, "status": "approved",
                    },
                ),
                _att(
                    "a2",
                    graduation_snapshot={
                        "belt": "purple", "degree": 1, "status": "approved",
                    },
                ),
            ]
        )
        body = _get(db, params={"belt": "blue"}).json()
        assert body["counts"]["confirmed"] == 1

    def test_degree_filter(self):
        db = _build_db(
            [
                _att(
                    "a1",
                    graduation_snapshot={
                        "belt": "blue", "degree": 2, "status": "approved",
                    },
                ),
                _att(
                    "a2",
                    graduation_snapshot={
                        "belt": "blue", "degree": 3, "status": "approved",
                    },
                ),
            ]
        )
        body = _get(db, params={"degree": 2}).json()
        assert body["counts"]["confirmed"] == 1


# ── Retrocompat + acting-as ─────────────────────────────────────────────────


class TestRetrocompat:
    def test_legacy_fields_preserved(self):
        db = _build_db([_att("a1")])
        body = _get(db).json()
        assert "streakDays" in body
        assert "overallPercent" in body
        assert isinstance(body["months"], list)


class TestActingAs:
    def test_guardian_reads_dependent_history(self):
        dep_user = {
            _DEP: {
                "name": "Dependente",
                "classIds": ["t1"],
                "createdAt": "2026-07-15T00:00:00-04:00",
                "guardianUid": _GUARDIAN,
            }
        }
        db = _build_db(
            [_att("a1", user_id=_DEP)],
            extra_users=dep_user,
        )
        r = _get(
            db,
            claims=_GUARDIAN_CLAIMS,
            headers={**_HEADERS, "X-Acting-As": _DEP},
        )
        assert r.status_code == 200, r.text
        assert r.json()["counts"]["confirmed"] == 1

    def test_guardian_of_wrong_dependent_forbidden(self):
        dep_user = {
            _DEP: {
                "name": "Dependente",
                "classIds": ["t1"],
                "createdAt": "2026-07-15T00:00:00-04:00",
                "guardianUid": "someone-else",
            }
        }
        db = _build_db([_att("a1", user_id=_DEP)], extra_users=dep_user)
        r = _get(
            db,
            claims=_GUARDIAN_CLAIMS,
            headers={**_HEADERS, "X-Acting-As": _DEP},
        )
        assert r.status_code == 403
