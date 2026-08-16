#!/usr/bin/env python3
"""Backfill de faltas — materializa as faltas dos dias já passados.

Até a virada do job noturno para a agenda das turmas, a falta só nascia se o
`aula` existisse — e `aula` só existe se alguém fez check-in naquele dia. Em
dia sem nenhum check-in, ninguém ficou com falta real: o app mostrava falta
sintética, que não é justificável nem entra no percentual. Este script
percorre, por turma com motor ativo, cada dia de aula da agenda entre a
data-base (`attendanceStartDate`) e ontem, e cria o que faltou.

Reusa `AbsenceJobService.materialize_absences`, então as guardas são
exatamente as do job: data-base da turma, `createdAt` do aluno e ausência de
registro para aquele `aulaId`. É idempotente — rodar duas vezes não cria nada
a mais. Não emite eventos (não faz sentido notificar falta retroativa).

Uso (emulador):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python scripts/backfill_absences.py --dry-run

Uso (produção — requer ADC):
    gcloud auth application-default login
    uv run python scripts/backfill_absences.py --dry-run
    uv run python scripts/backfill_absences.py --execute

Flags:
    --dry-run          (padrão) apenas relata, não grava
    --execute          grava (pede confirmação)
    --project-id ID    projeto alvo (padrão: ROOT_PROJECT_ID)
    --class-id ID      restringe a uma turma (debug)
    --from YYYY-MM-DD  início alternativo (padrão: data-base de cada turma)
    --to YYYY-MM-DD    fim (padrão: ontem)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import credentials, firestore

from app.services.absence_job_service import (
    AbsenceJobService,
    _engine_active,
    schedule_for_day,
)

_DEFAULT_PROJECT_ID = "spartacus-artes-marciais"
_TZ_OFFSET = timezone(timedelta(hours=-4))  # Brasnorte-MT = UTC-4


def init_firebase() -> None:
    if firebase_admin._apps:
        return
    if os.getenv("FIRESTORE_EMULATOR_HOST"):
        firebase_admin.initialize_app()
    else:
        firebase_admin.initialize_app(credentials.ApplicationDefault())


def _days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def backfill_class(
    db,
    project_id: str,
    class_id: str,
    class_data: dict,
    start_override: date | None,
    end: date,
    execute: bool,
) -> int:
    """Materialize the class's missing absences. Returns how many were created."""
    start_date = class_data.get("attendanceStartDate") or ""
    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        return 0
    if start_override and start_override > start:
        start = start_override

    service = AbsenceJobService()
    created = 0
    for day in _days(start, end):
        for session in schedule_for_day(class_id, class_data, day):
            if not execute:
                created += _would_create(db, project_id, class_id, session)
                continue
            events = service.materialize_absences(
                db, project_id, class_id, class_data, session,
            )
            created += len(events)
    return created


def _would_create(db, project_id: str, class_id: str, session) -> int:
    """Dry-run counterpart: counts without writing anything.

    Mirrors the guards of `materialize_absences` — enrolled students minus
    those who already have a record for this `aulaId`, minus those who joined
    after the session.
    """
    existing = (
        db.collection("attendance")
        .where("projectId", "==", project_id)
        .where("aulaId", "==", session.aula_id)
        .stream()
    )
    already = {p.to_dict().get("userId") for p in existing}

    enrolled = (
        db.collection("users")
        .where("classIds", "array_contains", class_id)
        .stream()
    )
    return sum(
        1 for u in enrolled
        if u.id not in already
        and AbsenceJobService._enrolled_by(u.to_dict() or {}, session)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-id",
        default=os.environ.get("ROOT_PROJECT_ID", _DEFAULT_PROJECT_ID),
    )
    parser.add_argument(
        "--execute", action="store_true", help="Grava (padrão: dry-run)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Não grava (padrão)",
    )
    parser.add_argument("--class-id", default=None, help="Só esta turma")
    parser.add_argument("--from", dest="date_from", default=None)
    parser.add_argument("--to", dest="date_to", default=None)
    args = parser.parse_args()

    execute = args.execute and not args.dry_run
    target = (
        "localhost (emulador)"
        if os.getenv("FIRESTORE_EMULATOR_HOST")
        else "PRODUÇÃO (ADC)"
    )
    start_override = (
        date.fromisoformat(args.date_from) if args.date_from else None
    )
    end = (
        date.fromisoformat(args.date_to)
        if args.date_to
        else datetime.now(_TZ_OFFSET).date() - timedelta(days=1)
    )

    print("=" * 64)
    print("  Backfill de faltas (frequência)")
    print("=" * 64)
    print(f"  Projeto:  {args.project_id}")
    print(f"  Destino:  {target}")
    print(f"  Até:      {end.isoformat()}")
    print(f"  Modo:     {'EXECUTAR (grava)' if execute else 'DRY-RUN'}")
    print("=" * 64)

    init_firebase()
    db = firestore.client()

    classes = []
    if args.class_id:
        doc = db.collection("classes").document(args.class_id).get()
        if doc.exists:
            classes.append(doc)
    else:
        classes = list(
            db.collection("classes")
            .where("projectId", "==", args.project_id)
            .stream()
        )

    active = [
        doc for doc in classes
        if (doc.to_dict() or {}).get("active") is not False
        and _engine_active(doc.id, doc.to_dict() or {})
    ]
    print(f"  {len(active)} turma(s) com motor ativo\n")

    if execute:
        preview = sum(
            backfill_class(
                db, args.project_id, doc.id, doc.to_dict(),
                start_override, end, execute=False,
            )
            for doc in active
        )
        print(f"  ⚠  Vai criar ~{preview} falta(s) em {target}.")
        if input("  Continuar? [y/N] ").strip().lower() != "y":
            print("  Cancelado.\n")
            sys.exit(0)
        print()

    total = 0
    for doc in active:
        data = doc.to_dict()
        n = backfill_class(
            db, args.project_id, doc.id, data,
            start_override, end, execute=execute,
        )
        total += n
        print(f"    {data.get('name', doc.id):<32} {n:>4} falta(s)")

    print("\n" + "=" * 64)
    print(f"  Total ({'gravado' if execute else 'dry-run'}): {total}")
    print("=" * 64)
    print(f"  Concluído em {datetime.now(timezone.utc).isoformat()}\n")


if __name__ == "__main__":
    main()
