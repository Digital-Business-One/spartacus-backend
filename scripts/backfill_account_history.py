#!/usr/bin/env python3
"""Backfill de Account History (RFC-12) — gera as entradas históricas ausentes.

Algumas contas foram criadas ANTES do sistema de histórico existir, então
``users/{uid}/historic`` está vazio/incompleto mesmo a conta tendo transações
reais (criação, aprovação/suspensão, presenças, apoios, graduação). Este script
reconcilia o histórico a partir do estado/transações reais da conta, espelhando
o que o sistema vivo grava — porém com os TIMESTAMPS REAIS das transações.

Segurança / idempotência:
  * Por conta e por categoria, só faz backfill se NÃO houver entradas "vivas"
    (não-backfilled) daquela categoria — nunca duplica o que o sistema gravou.
  * Entradas geradas usam doc id determinístico ``bf_*`` + campos
    ``backfilled: true`` e ``sourceRef`` → re-rodar sobrescreve (idempotente) e
    a reversão é trivial (apagar onde ``backfilled == true``).

Uso (emulador):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python scripts/backfill_account_history.py --dry-run

Uso (produção — requer ADC):
    gcloud auth application-default login
    uv run python scripts/backfill_account_history.py --dry-run
    uv run python scripts/backfill_account_history.py --execute

Flags:
    --dry-run        (padrão) apenas relata, não grava
    --execute        grava (pede confirmação)
    --project-id ID  projeto alvo (padrão: ROOT_PROJECT_ID / spartacus-artes-marciais)
    --uid UID        reconcilia apenas uma conta (debug)
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import credentials, firestore

# Reuse the live description helpers so backfilled entries read identically.
from app.models.graduation_system import GraduationSystem
from app.services.account_service import AccountService
from app.services.graduation_service import GraduationService
from app.services.support_service import _TYPE_LABEL, SupportService
from app.services.validation_service import ValidationService

_DEFAULT_PROJECT_ID = "spartacus-artes-marciais"
_USERS = "users"
_HISTORIC = "historic"
_MEMBERSHIPS = "memberships"
_ATTENDANCE = "attendance"
_SUPPORT = "support"
_GRAD_SYSTEMS = "graduation_systems"

# Account-history eventType per "category" used by the skip-if-live rule.
_APPROVAL_TYPES = {"approval", "suspension"}

# Terminal approvalStatus → (eventType, action) reconstructable from the doc.
_TERMINAL_STATUS = {
    "approved": ("approval", "approve"),
    "rejected": ("approval", "reject"),
    "archived": ("approval", "archive"),
    "expelled": ("suspension", "expel"),
}


@dataclass
class PlannedEntry:
    category: str  # logical bucket: creation|approval|attendance|support|graduation
    doc_id: str  # deterministic, prefixed bf_
    event_type: str
    event_subtype: str | None
    actor_uid: str
    actor_name: str
    actor_roles: list[str]
    description: str
    created_at: str  # real source timestamp (ISO8601)
    source_ref: str


@dataclass
class AccountResult:
    uid: str
    name: str
    created: dict[str, int] = field(default_factory=dict)
    skipped_live: dict[str, int] = field(default_factory=dict)
    no_source: list[str] = field(default_factory=list)


# ── Firebase init (mirrors scripts/delete_account.py) ─────────────────────────


def init_firebase() -> None:
    if firebase_admin._apps:
        return
    if os.getenv("FIRESTORE_EMULATOR_HOST"):
        firebase_admin.initialize_app()
    else:
        firebase_admin.initialize_app(credentials.ApplicationDefault())


# ── small utilities ───────────────────────────────────────────────────────────


def _to_iso(value) -> str:
    """Normalize a Firestore timestamp/str/datetime to an ISO8601 string."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    iso = getattr(value, "isoformat", None)  # google Timestamp / DatetimeWithNanos
    return iso() if callable(iso) else str(value)


class _Lookup:
    """Cached name/roles resolution to avoid repeated reads."""

    def __init__(self, db, project_id: str):
        self._db = db
        self._project_id = project_id
        self._names: dict[str, str] = {}
        self._roles: dict[str, list[str]] = {}

    def name(self, uid: str) -> str:
        if not uid:
            return ""
        if uid not in self._names:
            doc = self._db.collection(_USERS).document(uid).get()
            self._names[uid] = doc.to_dict().get("name", "") if doc.exists else ""
        return self._names[uid]

    def roles(self, uid: str) -> list[str]:
        if not uid:
            return []
        if uid not in self._roles:
            doc = (
                self._db.collection(_MEMBERSHIPS)
                .document(f"{self._project_id}_{uid}")
                .get()
            )
            self._roles[uid] = doc.to_dict().get("roles", []) if doc.exists else []
        return self._roles[uid]


# ── planners (one per category) ───────────────────────────────────────────────


def plan_creation(
    uid: str, user: dict, look: _Lookup, project_id: str,
) -> list[PlannedEntry]:
    created_at = _to_iso(user.get("createdAt"))
    if not created_at:
        return []  # cannot fabricate a creation timestamp
    if user.get("isDependent"):
        guardian_uid = user.get("guardianUid", "")
        guardian_name = look.name(guardian_uid) or "responsável"
        return [PlannedEntry(
            category="creation", doc_id="bf_creation",
            event_type="creation", event_subtype="guardian_created",
            actor_uid=guardian_uid or uid, actor_name=guardian_name,
            actor_roles=look.roles(guardian_uid),
            description=f"Dependente criado por {guardian_name} (responsável)",
            created_at=created_at, source_ref=f"{_USERS}/{uid}",
        )]
    provider = user.get("authProvider", "")
    desc = (
        "Conta criada via Google"
        if provider == "google.com"
        else "Conta criada via e-mail e senha"
    )
    return [PlannedEntry(
        category="creation", doc_id="bf_creation",
        event_type="creation", event_subtype=provider or None,
        actor_uid=uid, actor_name=user.get("name", ""),
        actor_roles=look.roles(uid), description=desc,
        created_at=created_at, source_ref=f"{_USERS}/{uid}",
    )]


def plan_approval(uid: str, user: dict, look: _Lookup) -> list[PlannedEntry]:
    status = user.get("approvalStatus", "")
    mapping = _TERMINAL_STATUS.get(status)
    if mapping is None:
        return []  # mid-flow status — nothing decisive to reconstruct
    event_type, action = mapping
    if status == "approved":
        actor_uid = user.get("approvedBy") or user.get("lastUpdatedBy", "")
        created_at = _to_iso(user.get("approvedAt")) or _to_iso(user.get("updatedAt"))
    else:
        actor_uid = user.get("lastUpdatedBy", "")
        created_at = _to_iso(user.get("updatedAt"))
    if not created_at:
        return []
    actor_name = look.name(actor_uid)
    return [PlannedEntry(
        category="approval", doc_id=f"bf_{event_type}_{action}",
        event_type=event_type, event_subtype=action,
        actor_uid=actor_uid, actor_name=actor_name,
        actor_roles=look.roles(actor_uid),
        description=AccountService._describe_transition(action, actor_name),
        created_at=created_at, source_ref=f"{_USERS}/{uid}",
    )]


def plan_attendance(db, uid: str, project_id: str, look: _Lookup) -> list[PlannedEntry]:
    out: list[PlannedEntry] = []
    docs = (
        db.collection(_ATTENDANCE)
        .where("userId", "==", uid)
        .where("projectId", "==", project_id)
        .stream()
    )
    for doc in docs:
        data = doc.to_dict()
        status = data.get("status", "")
        # Live only writes account history on validation (not on raw check-in).
        if status not in ("confirmed", "absent", "absent_justified"):
            continue
        created_at = _to_iso(data.get("validatedAt")) or _to_iso(data.get("timestamp"))
        if not created_at:
            continue
        actor_uid = data.get("validatedBy", "")
        actor_name = look.name(actor_uid)
        out.append(PlannedEntry(
            category="attendance", doc_id=f"bf_att_{doc.id}",
            event_type="attendance", event_subtype=status,
            actor_uid=actor_uid, actor_name=actor_name, actor_roles=[],
            description=ValidationService._describe_validation(
                "attendance", status, data, actor_name,
            ),
            created_at=created_at, source_ref=f"{_ATTENDANCE}/{doc.id}",
        ))
    return out


def plan_support(
    db, uid: str, project_id: str, user: dict, look: _Lookup,
) -> list[PlannedEntry]:
    out: list[PlannedEntry] = []
    docs = (
        db.collection(_SUPPORT)
        .where("userId", "==", uid)
        .where("projectId", "==", project_id)
        .stream()
    )
    user_name = user.get("name", "")
    for doc in docs:
        data = doc.to_dict()
        stype = data.get("supportType", "donation")
        type_label = _TYPE_LABEL.get(stype, "Apoio")
        amount = SupportService._amount(
            data.get("itemLabel") or data.get("item", ""),
            data.get("itemDescription"),
        )
        # Pledge entry — always present in the live two-phase flow.
        pledge_at = _to_iso(data.get("createdAt"))
        if pledge_at:
            out.append(PlannedEntry(
                category="support", doc_id=f"bf_sup_{doc.id}_pledged",
                event_type="support", event_subtype=f"{stype}_pledged",
                actor_uid=uid, actor_name=user_name, actor_roles=[],
                description=f"{type_label} registrada: {amount}",
                created_at=pledge_at, source_ref=f"{_SUPPORT}/{doc.id}",
            ))
        # Validation entry, if the support was received/refused.
        status = data.get("status", "")
        if status in ("received", "absent"):
            val_at = (
                _to_iso(data.get("validatedAt"))
                or _to_iso(data.get("receivedAt"))
                or pledge_at
            )
            actor_uid = data.get("validatedBy") or data.get("receivedBy", "")
            actor_name = look.name(actor_uid)
            out.append(PlannedEntry(
                category="support", doc_id=f"bf_sup_{doc.id}_{status}",
                event_type="support", event_subtype=status,
                actor_uid=actor_uid, actor_name=actor_name, actor_roles=[],
                description=ValidationService._describe_validation(
                    "support", status, data, actor_name,
                ),
                created_at=val_at, source_ref=f"{_SUPPORT}/{doc.id}",
            ))
    return out


def _grad_system(db, project_id: str, slug: str) -> GraduationSystem | None:
    try:
        doc = db.collection(_GRAD_SYSTEMS).document(f"{project_id}_{slug}").get()
        if doc.exists:
            return GraduationSystem(**doc.to_dict())
    except Exception:
        pass
    return None


def plan_graduation(db, uid: str, project_id: str, user: dict) -> list[PlannedEntry]:
    out: list[PlannedEntry] = []
    grad = user.get("graduation") or {}
    for slug, entry in grad.items():
        status = entry.get("status", "")
        if status not in ("approved", "rejected"):
            continue
        created_at = _to_iso(entry.get("gradedAt"))
        if not created_at:
            continue
        system = _grad_system(db, project_id, slug)
        modality_name = system.modality_name if system else slug
        if status == "approved":
            belt_label = GraduationService._belt_label(system, entry)
            description = f"Graduação aprovada em {modality_name}: {belt_label}"
        else:
            description = (
                f"Graduação reprovada em {modality_name} — "
                "aluno pode corrigir e reenviar"
            )
        out.append(PlannedEntry(
            category="graduation", doc_id=f"bf_grad_{slug}",
            event_type="graduation", event_subtype=status,
            actor_uid=entry.get("gradedBy", ""),
            actor_name=entry.get("gradedByName", ""), actor_roles=[],
            description=description, created_at=created_at,
            source_ref=f"{_USERS}/{uid}#graduation.{slug}",
        ))
    return out


# ── reconciliation ────────────────────────────────────────────────────────────


def _live_event_types(db, uid: str, project_id: str) -> set[str]:
    """eventTypes already present from the LIVE system (non-backfilled)."""
    live: set[str] = set()
    docs = (
        db.collection(_USERS).document(uid).collection(_HISTORIC)
        .where("projectId", "==", project_id).stream()
    )
    for doc in docs:
        data = doc.to_dict()
        if data.get("backfilled") is True:
            continue
        et = data.get("eventType")
        if et:
            live.add(et)
    return live


def _payload(entry: PlannedEntry, project_id: str) -> dict:
    return {
        "projectId": project_id,
        "eventType": entry.event_type,
        "eventSubtype": entry.event_subtype,
        "actorUid": entry.actor_uid,
        "actorName": entry.actor_name,
        "actorRoles": entry.actor_roles,
        "description": entry.description,
        "createdAt": entry.created_at,
        "backfilled": True,
        "sourceRef": entry.source_ref,
    }


def reconcile_account(
    db, uid: str, project_id: str, look: _Lookup, execute: bool,
) -> AccountResult:
    user_doc = db.collection(_USERS).document(uid).get()
    if not user_doc.exists:
        res = AccountResult(uid=uid, name="(sem doc de usuário)")
        res.no_source.append("user_doc_missing")
        return res
    user = user_doc.to_dict()
    res = AccountResult(uid=uid, name=user.get("name", ""))

    planned: list[PlannedEntry] = []
    planned += plan_creation(uid, user, look, project_id)
    planned += plan_approval(uid, user, look)
    planned += plan_attendance(db, uid, project_id, look)
    planned += plan_support(db, uid, project_id, user, look)
    planned += plan_graduation(db, uid, project_id, user)

    if not planned:
        return res

    live_types = _live_event_types(db, uid, project_id)
    hist_col = db.collection(_USERS).document(uid).collection(_HISTORIC)

    for entry in planned:
        # Skip a whole category if the LIVE system already owns it.
        owned = (
            (entry.category == "approval" and live_types & _APPROVAL_TYPES)
            or (entry.category != "approval" and entry.event_type in live_types)
        )
        if owned:
            res.skipped_live[entry.category] = (
                res.skipped_live.get(entry.category, 0) + 1
            )
            continue
        if execute:
            hist_col.document(entry.doc_id).set(_payload(entry, project_id))
        res.created[entry.category] = res.created.get(entry.category, 0) + 1
    return res


def list_project_uids(db, project_id: str) -> list[str]:
    uids: list[str] = []
    for m in db.collection(_MEMBERSHIPS).where("projectId", "==", project_id).stream():
        uid = m.to_dict().get("userId")
        if uid:
            uids.append(uid)
    return sorted(set(uids))


# ── CLI / reporting ───────────────────────────────────────────────────────────


def _print_account(res: AccountResult, execute: bool) -> None:
    if not res.created and not res.skipped_live and not res.no_source:
        return
    verb = "criadas" if execute else "a criar"
    parts: list[str] = []
    for cat, n in sorted(res.created.items()):
        parts.append(f"{cat}={n} ({verb})")
    for cat, n in sorted(res.skipped_live.items()):
        parts.append(f"{cat}=pulado(já vivo)")
    for src in res.no_source:
        parts.append(f"sem-fonte:{src}")
    print(f"  • {res.uid[:12]}… · {res.name[:28]:<28} {'; '.join(parts)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-id",
        default=os.environ.get("ROOT_PROJECT_ID", _DEFAULT_PROJECT_ID),
    )
    parser.add_argument(
        "--execute", action="store_true", help="Grava (padrão: dry-run)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Não grava (padrão)")
    parser.add_argument("--uid", default=None, help="Reconciliar apenas esta conta")
    args = parser.parse_args()

    execute = args.execute and not args.dry_run
    target = (
        "localhost (emulador)"
        if os.getenv("FIRESTORE_EMULATOR_HOST")
        else "PRODUÇÃO (ADC)"
    )

    print("=" * 64)
    print("  Backfill de Account History (RFC-12)")
    print("=" * 64)
    print(f"  Projeto:  {args.project_id}")
    print(f"  Destino:  {target}")
    print(f"  Modo:     {'EXECUTAR (grava)' if execute else 'DRY-RUN (não grava)'}")
    print("=" * 64)

    init_firebase()
    db = firestore.client()
    look = _Lookup(db, args.project_id)

    uids = [args.uid] if args.uid else list_project_uids(db, args.project_id)
    print(f"  {len(uids)} conta(s) no projeto\n")

    # In dry-run we report first; in execute we confirm before any write.
    if execute:
        # First pass over plans (no writes) just to size the impact.
        preview = [
            reconcile_account(db, uid, args.project_id, look, execute=False)
            for uid in uids
        ]
        affected = sum(1 for r in preview if r.created)
        entries = sum(sum(r.created.values()) for r in preview)
        print(
            f"  ⚠  Vai gravar {entries} entrada(s) em {affected} "
            f"conta(s) de {target}."
        )
        if input("  Continuar? [y/N] ").strip().lower() != "y":
            print("  Cancelado.\n")
            sys.exit(0)
        print()

    totals_created: dict[str, int] = {}
    totals_skipped: dict[str, int] = {}
    for uid in uids:
        res = reconcile_account(db, uid, args.project_id, look, execute=execute)
        _print_account(res, execute)
        for cat, n in res.created.items():
            totals_created[cat] = totals_created.get(cat, 0) + n
        for cat, n in res.skipped_live.items():
            totals_skipped[cat] = totals_skipped.get(cat, 0) + n

    print("\n" + "=" * 64)
    print(f"  Resumo ({'gravado' if execute else 'dry-run'}):")
    for cat in sorted(set(totals_created) | set(totals_skipped)):
        print(
            f"    {cat:<12} {'+'}{totals_created.get(cat, 0):<5}"
            f"  pulado(vivo): {totals_skipped.get(cat, 0)}"
        )
    print("  (warning não é reconstruível — vive só no histórico)")
    print("=" * 64)
    print(f"  Concluído em {datetime.now(timezone.utc).isoformat()}\n")


if __name__ == "__main__":
    main()
