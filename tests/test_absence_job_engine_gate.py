"""Job noturno de faltas — gates do motor e criação dirigida pela agenda.

Cobre os gates da Task A5 (motor desligado, retroação antes da data-base,
inconsistência ligada-sem-data) e a virada da spec de 29/07: o job passa a
percorrer a **agenda das turmas**, não os `aulas` existentes, para que um dia
sem nenhum check-in também produza falta real — justificável e contada no %.

See: docs/superpowers/specs/2026-07-18-frequencia-analitica-design.md
     docs/superpowers/specs/2026-07-29-frequencia-app-ajustes-design.md (§5)
"""

import logging
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from app.services.absence_job_service import (
    AbsenceJobService,
    Session,
    schedule_for_day,
)

_PROJECT_ID = "spartacus"
_TZ = timezone(timedelta(hours=-4))
_DAY = date(2026, 7, 15)  # quarta-feira
_SCHEDULE = [{"day": "wed", "startTime": "19:00", "endTime": "20:00"}]


# ── Fake Firestore ──────────────────────────────────────────────────────────


class _Doc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class _Ref:
    def __init__(self, col, doc_id):
        self._col = col
        self.id = doc_id

    def get(self):
        return _Doc(self.id, self._col.docs.get(self.id))

    def set(self, data):
        self._col.docs[self.id] = data


class _Query:
    def __init__(self, docs):
        self._docs = docs

    def where(self, field, op, value):
        if op == "array_contains":
            return _Query([
                d for d in self._docs
                if value in (d.to_dict().get(field) or [])
            ])
        return _Query([
            d for d in self._docs if d.to_dict().get(field) == value
        ])

    def stream(self):
        return list(self._docs)


class _Collection:
    def __init__(self, docs):
        self.docs = dict(docs)
        self.added: list[dict] = []

    def document(self, doc_id):
        return _Ref(self, doc_id)

    def where(self, field, op, value):
        docs = [_Doc(i, d) for i, d in self.docs.items()]
        return _Query(docs).where(field, op, value)

    def stream(self):
        return [_Doc(i, d) for i, d in self.docs.items()]

    def add(self, data):
        self.added.append(data)
        doc_id = f"att{len(self.added)}"
        self.docs[doc_id] = data
        return None, _Ref(self, doc_id)


class _DB:
    def __init__(self, **collections):
        self._c = {name: _Collection(docs) for name, docs in collections.items()}

    def collection(self, name):
        return self._c.setdefault(name, _Collection({}))


def _class(
    engine=True,
    start_date="2026-07-01",
    schedule=None,
    active=True,
):
    return {
        "projectId": _PROJECT_ID,
        "name": "Turma A",
        "active": active,
        "modalityId": "spartacus_jiu-jitsu",
        "attendanceEngineEnabled": engine,
        "attendanceStartDate": start_date,
        "schedule": _SCHEDULE if schedule is None else schedule,
    }


def _db(class_data=None, users=None, attendance=None):
    return _DB(
        classes={"t1": class_data or _class()},
        users=users or {
            "u1": {
                "name": "Aluno",
                "classIds": ["t1"],
                "createdAt": "2026-01-01T00:00:00-04:00",
            },
        },
        attendance=attendance or {},
        aulas={},
        modalities={"spartacus_jiu-jitsu": {"slug": "jiu-jitsu"}},
    )


def _session(day=_DAY):
    return Session("t1", day, "19:00", "20:00")


def _run(db, now):
    with patch("app.services.absence_job_service.firestore") as fs:
        fs.client.return_value = db
        return AbsenceJobService().compute(_PROJECT_ID, now=now)


# ── Gates do motor (Task A5) ────────────────────────────────────────────────


class TestJobSkipsEngineOff:
    def test_engine_off_turma_creates_no_absences(self):
        db = _db(_class(engine=False))
        events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert db.collection("attendance").added == []
        assert events == []

    def test_turma_inativa_nao_gera_falta(self):
        db = _db(_class(active=False))
        events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert events == []


class TestJobNeverRetroacts:
    def test_aula_date_before_start_date_creates_no_absences(self):
        db = _db(_class(start_date="2026-07-16"))
        events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert db.collection("attendance").added == []
        assert events == []

    def test_aula_date_on_or_after_start_date_creates_absences(self):
        db = _db(_class(start_date="2026-07-15"))
        events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert len(db.collection("attendance").added) == 1
        assert len(events) == 1


class TestInconsistencyGuard:
    def test_enabled_without_start_date_is_treated_as_off_and_logged(
        self, caplog,
    ):
        db = _db(_class(start_date=None))
        with caplog.at_level(logging.ERROR):
            events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert db.collection("attendance").added == []
        assert events == []

        assert any(
            "attendance-engine-inconsistency" in r.getMessage()
            and "t1" in r.getMessage()
            for r in caplog.records if r.levelno == logging.ERROR
        )


# ── Job dirigido pela agenda (spec 29/07 §5) ────────────────────────────────


class TestScheduleDriven:
    def test_cria_falta_em_dia_sem_nenhum_checkin(self):
        """Nenhum `aula` existia — é o caso que o job antigo não alcançava."""
        db = _db()
        events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        criadas = db.collection("attendance").added
        assert len(criadas) == 1
        assert criadas[0]["status"] == "absent"
        assert criadas[0]["validatedBy"] == "system"
        assert len(events) == 1

    def test_materializa_a_aula_com_id_deterministico(self):
        db = _db()
        _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        aulas = db.collection("aulas").docs
        assert "t1_20260715_1900" in aulas
        assert db.collection("attendance").added[0]["aulaId"] == (
            "t1_20260715_1900"
        )

    def test_timestamp_e_o_inicio_da_aula_nao_a_hora_do_job(self):
        """Sem isso a falta cairia no dia errado do calendário no backfill."""
        db = _db()
        _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert db.collection("attendance").added[0]["timestamp"] == (
            "2026-07-15T19:00:00-04:00"
        )

    def test_ignora_sessao_que_ainda_nao_terminou(self):
        db = _db()
        events = _run(db, datetime(2026, 7, 15, 19, 30, tzinfo=_TZ))

        assert db.collection("attendance").added == []
        assert events == []

    def test_dia_fora_da_agenda_nao_gera_nada(self):
        db = _db()
        # 16/07/2026 é quinta; a agenda da turma é quarta.
        events = _run(db, datetime(2026, 7, 16, 23, 59, tzinfo=_TZ))

        assert events == []

    def test_nao_cria_para_quem_ja_tem_registro_na_aula(self):
        db = _db(attendance={
            "existente": {
                "projectId": _PROJECT_ID,
                "userId": "u1",
                "aulaId": "t1_20260715_1900",
                "status": "confirmed",
            },
        })
        events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert db.collection("attendance").added == []
        assert events == []

    def test_nao_duplica_em_segunda_execucao(self):
        db = _db()
        now = datetime(2026, 7, 15, 23, 59, tzinfo=_TZ)
        _run(db, now)
        _run(db, now)

        assert len(db.collection("attendance").added) == 1

    def test_ignora_aluno_matriculado_depois_da_aula(self):
        db = _db(users={
            "novato": {
                "name": "Novato",
                "classIds": ["t1"],
                "createdAt": "2026-07-20T00:00:00-04:00",
            },
        })
        events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert db.collection("attendance").added == []
        assert events == []

    def test_aluno_sem_created_at_nao_e_barrado(self):
        db = _db(users={"u1": {"name": "Aluno", "classIds": ["t1"]}})
        events = _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        assert len(events) == 1

    def test_grava_snapshot_de_graduacao_e_modalidade(self):
        db = _db()
        _run(db, datetime(2026, 7, 15, 23, 59, tzinfo=_TZ))

        criada = db.collection("attendance").added[0]
        assert criada["modalitySlug"] == "jiu-jitsu"
        assert "graduationSnapshot" in criada


class TestScheduleForDay:
    def test_le_a_agenda_legada_weekly_schedule(self):
        class_data = {
            "schedule": [],
            "weeklySchedule": {
                "days": ["wed"], "startTime": "19:00", "endTime": "20:00",
            },
        }
        sessions = schedule_for_day("t1", class_data, _DAY)

        assert [s.aula_id for s in sessions] == ["t1_20260715_1900"]

    def test_ignora_item_sem_horario(self):
        class_data = {"schedule": [{"day": "wed"}]}

        assert schedule_for_day("t1", class_data, _DAY) == []

    def test_multiplas_sessoes_no_mesmo_dia(self):
        class_data = {"schedule": [
            {"day": "wed", "startTime": "08:00", "endTime": "09:00"},
            {"day": "wed", "startTime": "19:00", "endTime": "20:00"},
        ]}

        assert len(schedule_for_day("t1", class_data, _DAY)) == 2


# ── Backfill (§5.2) — mesmas guardas, sem depender do relógio ───────────────


class TestBackfillIdempotencia:
    def test_materialize_absences_e_idempotente(self):
        db = _db()
        svc = AbsenceJobService()
        args = (db, _PROJECT_ID, "t1", _class(), _session())

        primeira = svc.materialize_absences(*args)
        segunda = svc.materialize_absences(*args)

        assert len(primeira) == 1
        assert segunda == []
        assert len(db.collection("attendance").added) == 1

    def test_respeita_a_data_base_da_turma(self):
        db = _db()
        svc = AbsenceJobService()

        events = svc.materialize_absences(
            db, _PROJECT_ID, "t1", _class(start_date="2026-07-16"), _session(),
        )

        assert events == []
