#!/usr/bin/env python3
"""Lista as pessoas que usaram (navegaram) o app nos últimos N dias.

O app **não** tem tracking de sessão/navegação por tela (não há `lastSeen`,
heartbeat nem analytics). O sinal disponível mais próximo de "usou o app" é
o **Firebase Auth**:

  - `lastRefreshTime`  → o token de ID foi renovado (o app abriu / estava em
    uso; o Firebase renova o token ~a cada hora de uso). É o melhor sinal.
  - `lastSignInTime`   → último login efetivo. Usado como fallback quando não
    há refresh (ex.: no emulador local, que não rastreia refresh).

Para cada usuário do Auth ativo na janela, junta nome/e-mail (coleção
`users`) e papéis (coleção `memberships`) do Firestore e imprime uma tabela
ordenada da atividade mais recente para a mais antiga.

⚠️ Limite: isto mede "renovou token / logou", não navegação por tela. Para
métricas de navegação de verdade seria preciso instrumentar o app (um campo
`lastSeen` atualizado no foreground, ou Firebase Analytics/GA4).

Uso (dev local, contra os emuladores — sem ADC):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    FIREBASE_AUTH_EMULATOR_HOST=localhost:9099 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    uv run python scripts/active_users.py --days 10

Uso (produção — requer ADC / gcloud auth application-default login):
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    uv run python scripts/active_users.py --days 10 --csv usuarios_ativos.csv

Dentro do container do backend (docker-compose):
    docker compose exec backend uv run python scripts/active_users.py --days 10
"""
import argparse
import csv as csv_mod
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

import firebase_admin  # noqa: E402  (após load_dotenv, de propósito)
from firebase_admin import auth, firestore  # noqa: E402

# Brasnorte é UTC-4; datas são exibidas nesse fuso.
_TZ = timezone(timedelta(hours=-4))


def _init_firebase() -> None:
    """Init emulator-aware: credenciais anônimas contra o emulador (sem ADC);
    ADC em produção."""
    if firebase_admin._apps:
        return
    use_emulator = bool(
        os.getenv("FIRESTORE_EMULATOR_HOST") or os.getenv("FIREBASE_AUTH_EMULATOR_HOST")
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
        firebase_admin.initialize_app()


def _load_directory(db) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """{uid: user_doc} e {uid: [roles]} a partir do Firestore."""
    users = {u.id: (u.to_dict() or {}) for u in db.collection("users").stream()}
    roles: dict[str, list[str]] = {}
    for m in db.collection("memberships").stream():
        md = m.to_dict() or {}
        uid = md.get("userId")
        if uid:
            roles.setdefault(uid, [])
            for r in md.get("roles") or []:
                if r not in roles[uid]:
                    roles[uid].append(r)
    return users, roles


def _activity_ms(metadata) -> tuple[int | None, str]:
    """Retorna (timestamp_ms, sinal). Prefere refresh; cai para sign-in."""
    refresh = getattr(metadata, "last_refresh_timestamp", None)
    if refresh:
        return refresh, "refresh"
    signin = getattr(metadata, "last_sign_in_timestamp", None)
    if signin:
        return signin, "sign-in"
    return None, "—"


def collect(days: int) -> list[dict]:
    db = firestore.client()
    users, roles = _load_directory(db)
    cutoff_ms = int(
        (datetime.now(_TZ) - timedelta(days=days)).timestamp() * 1000
    )

    active: list[dict] = []
    for u in auth.list_users().iterate_all():
        ts_ms, signal = _activity_ms(u.user_metadata)
        if ts_ms is None or ts_ms < cutoff_ms:
            continue
        doc = users.get(u.uid, {})
        active.append(
            {
                "uid": u.uid,
                "name": doc.get("name") or u.display_name or "(sem nome)",
                "email": u.email or doc.get("email") or "—",
                "roles": ", ".join(roles.get(u.uid, [])) or "—",
                "last_activity": datetime.fromtimestamp(ts_ms / 1000, _TZ),
                "signal": signal,
            }
        )

    active.sort(key=lambda r: r["last_activity"], reverse=True)
    return active


def _print_table(rows: list[dict], days: int) -> None:
    if not rows:
        print(f"Nenhuma pessoa usou o app nos últimos {days} dias.")
        return
    print(f"\n{len(rows)} pessoa(s) usaram o app nos últimos {days} dias "
          "(ordenado da atividade mais recente):\n")
    header = f"{'Última atividade':<20} {'Sinal':<8} {'Nome':<28} {'Papéis':<24} E-mail"
    print(header)
    print("-" * len(header))
    for r in rows:
        when = r["last_activity"].strftime("%d/%m/%Y %H:%M")
        print(f"{when:<20} {r['signal']:<8} {r['name'][:27]:<28} "
              f"{r['roles'][:23]:<24} {r['email']}")
    # nota se algum registro usou fallback de sign-in (comum no emulador)
    if any(r["signal"] == "sign-in" for r in rows):
        print("\n(*) 'sign-in' = sem lastRefreshTime; usei o último login como "
              "fallback — comum no emulador local. Em produção o refresh existe.")


def _write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv_mod.writer(fh)
        w.writerow(["uid", "name", "email", "roles", "last_activity", "signal"])
        for r in rows:
            w.writerow([
                r["uid"], r["name"], r["email"], r["roles"],
                r["last_activity"].isoformat(), r["signal"],
            ])
    print(f"\nCSV salvo em: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--days", type=int, default=10,
                        help="Janela em dias (padrão: 10)")
    parser.add_argument("--csv", metavar="ARQUIVO",
                        help="Também exporta o resultado para CSV")
    args = parser.parse_args()

    _init_firebase()
    rows = collect(args.days)
    _print_table(rows, args.days)
    if args.csv:
        _write_csv(rows, args.csv)


if __name__ == "__main__":
    main()
