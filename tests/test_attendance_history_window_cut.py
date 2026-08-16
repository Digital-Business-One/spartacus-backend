"""Corte duro da data-base no calendário de `GET /attendance/history`.

Antes, a janela por turma (`max(user.createdAt, attendanceStartDate)`) só
existia dentro de `_compute_analytics`: o `%` respeitava a data-base e o
calendário não, exibindo falta sintética de meses anteriores. Estes testes
fixam o corte compartilhado — nada anterior à janela aparece, nem sintético
nem registro real, e o mês que fica vazio some da resposta.

See: docs/superpowers/specs/2026-07-29-frequencia-app-ajustes-design.md (§4)
"""

from datetime import date, datetime, timedelta, timezone

from app.services.attendance_service import AttendanceService, _ScheduleItem
from tests.test_attendance_history import (
    _att,
    _build_db,
    _class,
    _get,
)

_TZ = timezone(timedelta(hours=-4))


def _window(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T00:00:00-04:00")


def _sched(class_id="t1", day_code="wed"):
    return _ScheduleItem(
        day_code=day_code,
        class_id=class_id,
        class_name="Turma A",
        modality_name="Jiu-Jitsu",
        teacher_name="Prof",
    )


# 2026-07-01 é uma quarta-feira; o mês tem quartas em 1, 8, 15, 22 e 29.
_JULY_END = date(2026, 7, 31)


class TestBuildMonthRecordsWindow:

    def test_nao_emite_sintetica_antes_da_janela(self):
        records, _, expected = AttendanceService._build_month_records(
            2026, 7, [_sched()], {}, _JULY_END,
            {"t1": _window("2026-07-15")},
        )
        dias = sorted(r.date_sort for r in records)
        assert dias == ["2026-07-15", "2026-07-22", "2026-07-29"]
        # dia fora da janela também não infla o denominador
        assert expected == 3

    def test_descarta_registro_real_antes_da_janela(self):
        _, real = _att("a1", timestamp="2026-07-08T19:00:00-04:00")
        records, attended, _ = AttendanceService._build_month_records(
            2026, 7, [_sched()],
            {date(2026, 7, 8): [dict(real, _id="a1")]},
            _JULY_END,
            {"t1": _window("2026-07-15")},
        )
        assert all(r.date_sort != "2026-07-08" for r in records)
        assert attended == 0

    def test_turma_com_janela_none_sai_do_calendario(self):
        records, _, expected = AttendanceService._build_month_records(
            2026, 7, [_sched()], {}, _JULY_END, {"t1": None},
        )
        assert records == []
        assert expected == 0

    def test_ramo_orfao_aplica_o_mesmo_corte(self):
        """Sem agenda, registros de turma que o aluno deixou também cortam."""
        _, antigo = _att("a1", timestamp="2026-07-08T19:00:00-04:00")
        _, dentro = _att("a2", timestamp="2026-07-16T19:00:00-04:00")
        records, _, _ = AttendanceService._build_month_records(
            2026, 7, [],
            {
                date(2026, 7, 8): [dict(antigo, _id="a1")],
                date(2026, 7, 16): [dict(dentro, _id="a2")],
            },
            _JULY_END,
            {"t1": _window("2026-07-15")},
        )
        assert [r.date_sort for r in records] == ["2026-07-16"]

    def test_turma_desconhecida_nao_sofre_corte(self):
        """Sem janela conhecida, mantém o comportamento anterior."""
        records, _, expected = AttendanceService._build_month_records(
            2026, 7, [_sched()], {}, _JULY_END, {},
        )
        assert expected == 5


class TestMonthsRangeCut:

    def test_corta_no_mes_da_janela_mais_antiga(self):
        meses = AttendanceService._resolve_months_range(
            date(2026, 8, 16), None, None, {"t1": _window("2026-07-15")},
        )
        assert meses == [(2026, 8), (2026, 7)]

    def test_janela_mais_antiga_entre_turmas_manda(self):
        meses = AttendanceService._resolve_months_range(
            date(2026, 8, 16), None, None,
            {"t1": _window("2026-07-15"), "t2": _window("2026-05-02")},
        )
        assert meses[-1] == (2026, 5)

    def test_sem_janela_valida_mantem_a_faixa_padrao(self):
        meses = AttendanceService._resolve_months_range(
            date(2026, 8, 16), None, None, {"t1": None},
        )
        assert len(meses) == 6

    def test_corta_tambem_a_faixa_explicita_de_ano_e_meses(self):
        meses = AttendanceService._resolve_months_range(
            date(2026, 8, 16), 2026, [6, 7, 8], {"t1": _window("2026-07-15")},
        )
        assert meses == [(2026, 8), (2026, 7)]


class TestHistoryEndpointCut:

    def test_mes_anterior_a_data_base_nao_aparece(self):
        """Junho não volta na resposta com data-base em 15/07."""
        classes = {"t1": dict(_class(), schedule=[
            {"day": "wed", "startTime": "19:00", "endTime": "20:00"},
        ])}
        db = _build_db(
            [_att("a1", timestamp="2026-07-15T19:00:00-04:00")],
            classes=classes,
            user_created_at="2026-01-01T00:00:00-04:00",
        )
        body = _get(db).json()
        meses = [m["month"] for m in body["months"]]
        assert "2026-06" not in meses
        assert all(m >= "2026-07" for m in meses)

    def test_aluno_sem_registro_na_janela_fica_sem_meses(self):
        """Motor desligado em todas as turmas → estado 'Nenhum registro'."""
        classes = {"t1": dict(_class(engine=False), schedule=[
            {"day": "wed", "startTime": "19:00", "endTime": "20:00"},
        ])}
        db = _build_db(
            [_att("a1", timestamp="2026-07-15T19:00:00-04:00")],
            classes=classes,
        )
        body = _get(db).json()
        assert body["months"] == []
