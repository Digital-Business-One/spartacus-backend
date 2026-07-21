#!/usr/bin/env python3
"""Quem usou o app nos últimos N dias — a partir dos LOGS do controller/service.

Fonte mais rica que o Firebase Auth (ver `active_users.py`): o backend loga
`user_id` em cada método de Controller/Service (decorator `@log`, propagado
pelo AuthMiddleware — ADR-10). Cada request autenticado vira uma linha
structlog JSON com `user_id`, `class`, `method`, `event`, `timestamp`.

Dois modos:

  --source cloud  (padrão)  → produção. Lê o Cloud Logging via `gcloud logging
                              read` (não precisa de lib nova; precisa do gcloud
                              autenticado e permissão de leitura de logs).
  --source local            → dev. Parseia as linhas JSON do stdout do backend,
                              ex.: `docker compose logs backend`.

Enriquece `user_id` → nome/e-mail via Firestore (best-effort; emulator-aware
em dev, ADC em produção). Se não houver credencial, mostra só o `user_id`.

Exemplos:
  # Produção (workstation com gcloud autenticado + ADC p/ os nomes)
  GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    uv run python scripts/active_users_from_logs.py --days 10 --csv ativos.csv

  # Dev local (contra o container em execução)
  docker compose logs backend | \
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    uv run python scripts/active_users_from_logs.py --source local --days 10
"""
import argparse
import csv as csv_mod
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

_TZ = timezone(timedelta(hours=-4))  # Brasnorte UTC-4
_DEFAULT_SERVICE = "spartacus-backend"


# ─── Firestore enrichment (best-effort) ──────────────────────────────────────
def _load_names() -> dict[str, dict]:
    """{uid: {name, email}} do Firestore. Silencioso se não houver credencial."""
    try:
        import firebase_admin  # noqa: E402
        from firebase_admin import firestore  # noqa: E402

        if not firebase_admin._apps:
            use_emulator = bool(
                os.getenv("FIRESTORE_EMULATOR_HOST")
                or os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
            )
            if use_emulator:
                from firebase_admin import credentials as fb_credentials
                from google.auth.credentials import AnonymousCredentials

                class _EmulatorCredential(fb_credentials.Base):
                    def get_credential(self):
                        return AnonymousCredentials()

                firebase_admin.initialize_app(
                    credential=_EmulatorCredential(),
                    options={
                        "projectId": os.getenv(
                            "GOOGLE_CLOUD_PROJECT", "spartacus-artes-marciais"
                        )
                    },
                )
            else:
                firebase_admin.initialize_app(
                    options={
                        "projectId": os.getenv(
                            "GOOGLE_CLOUD_PROJECT", "spartacus-artes-marciais"
                        )
                    }
                )
        db = firestore.client()
        return {u.id: (u.to_dict() or {}) for u in db.collection("users").stream()}
    except Exception as exc:  # noqa: BLE001 — enrichment é opcional
        print(f"(aviso: sem enriquecimento de nomes — {exc})", file=sys.stderr)
        return {}


# ─── Normalização de um registro de log → (user_id, ts, method) ──────────────
def _extract(record: dict) -> tuple[str, datetime, str] | None:
    # gcloud logging read devolve LogEntry (payload em jsonPayload);
    # o stdout local já É o payload structlog.
    payload = record.get("jsonPayload", record)
    user_id = payload.get("user_id")
    if not user_id or payload.get("event") != "call":
        return None
    ts_raw = record.get("timestamp") or payload.get("timestamp")
    if not ts_raw:
        return None
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(_TZ)
    except (ValueError, AttributeError):
        return None
    method = f"{payload.get('class') or '?'}.{payload.get('method') or '?'}"
    return user_id, ts, method


# ─── Fontes ──────────────────────────────────────────────────────────────────
def _records_from_cloud(days: int, project: str, service: str):
    log_filter = (
        'resource.type="cloud_run_revision" '
        f'resource.labels.service_name="{service}" '
        'jsonPayload.user_id!="" '
        'jsonPayload.event="call"'
    )
    cmd = [
        "gcloud", "logging", "read", log_filter,
        "--project", project,
        "--freshness", f"{days}d",
        "--format", "json",
        "--limit", "100000",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        sys.exit("Erro: `gcloud` não encontrado. Instale o Cloud SDK ou "
                 "use --source local.")
    except subprocess.CalledProcessError as exc:
        sys.exit(f"Erro no `gcloud logging read`:\n{exc.stderr}")
    entries = json.loads(out or "[]")
    if len(entries) >= 100000:
        print("(aviso: atingiu o limite de 100000 entradas — resultado pode estar "
              "truncado; reduza --days)", file=sys.stderr)
    for e in entries:
        yield e


def _records_from_local(path: str | None):
    fh = open(path, encoding="utf-8") if path else sys.stdin
    try:
        for line in fh:
            # linhas do `docker compose logs` vêm com prefixo "backend-1  | {...}"
            brace = line.find("{")
            if brace == -1:
                continue
            try:
                yield json.loads(line[brace:])
            except json.JSONDecodeError:
                continue
    finally:
        if path:
            fh.close()


# ─── Agregação ────────────────────────────────────────────────────────────────
def aggregate(records, cutoff: datetime) -> dict[str, dict]:
    users: dict[str, dict] = {}
    for rec in records:
        parsed = _extract(rec)
        if not parsed:
            continue
        uid, ts, method = parsed
        if ts < cutoff:
            continue
        u = users.setdefault(
            uid, {"count": 0, "first": ts, "last": ts, "methods": set()}
        )
        u["count"] += 1
        u["first"] = min(u["first"], ts)
        u["last"] = max(u["last"], ts)
        u["methods"].add(method)
    return users


def _print(users: dict[str, dict], names: dict[str, dict], days: int) -> None:
    if not users:
        print(f"Nenhuma atividade de usuário nos logs dos últimos {days} dias.")
        return
    rows = sorted(users.items(), key=lambda kv: kv[1]["last"], reverse=True)
    print(f"\n{len(rows)} pessoa(s) com atividade no app (logs) nos últimos "
          f"{days} dias:\n")
    header = (f"{'Última atividade':<20} {'Reqs':>6}  {'Nome':<26} "
              f"{'Ações distintas':>15}  E-mail / user_id")
    print(header)
    print("-" * len(header))
    for uid, u in rows:
        doc = names.get(uid, {})
        name = (doc.get("name") or "(sem nome)")[:25]
        ident = doc.get("email") or uid
        print(f"{u['last'].strftime('%d/%m/%Y %H:%M'):<20} {u['count']:>6}  "
              f"{name:<26} {len(u['methods']):>15}  {ident}")


def _write_csv(users: dict[str, dict], names: dict[str, dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv_mod.writer(fh)
        w.writerow(["user_id", "name", "email", "requests",
                    "distinct_actions", "first_seen", "last_seen"])
        for uid, u in sorted(users.items(), key=lambda kv: kv[1]["last"], reverse=True):
            doc = names.get(uid, {})
            w.writerow([
                uid, doc.get("name") or "", doc.get("email") or "",
                u["count"], len(u["methods"]),
                u["first"].isoformat(), u["last"].isoformat(),
            ])
    print(f"\nCSV salvo em: {path}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", choices=["cloud", "local"], default="cloud")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--file", help="(local) arquivo de logs; padrão: stdin")
    p.add_argument(
        "--project",
        default=os.getenv("GOOGLE_CLOUD_PROJECT", "spartacus-artes-marciais"),
    )
    p.add_argument("--service", default=_DEFAULT_SERVICE)
    p.add_argument("--csv", metavar="ARQUIVO")
    p.add_argument("--no-enrich", action="store_true",
                   help="não busca nomes no Firestore")
    args = p.parse_args()

    cutoff = datetime.now(_TZ) - timedelta(days=args.days)
    if args.source == "cloud":
        records = _records_from_cloud(args.days, args.project, args.service)
    else:
        records = _records_from_local(args.file)

    users = aggregate(records, cutoff)
    names = {} if args.no_enrich else _load_names()
    _print(users, names, args.days)
    if args.csv:
        _write_csv(users, names, args.csv)


if __name__ == "__main__":
    main()
