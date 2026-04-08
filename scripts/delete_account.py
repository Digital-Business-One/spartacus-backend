#!/usr/bin/env python3
"""
Script utilitário para apagar completamente uma conta do Firestore + Firebase Auth.

Faz cascade recursiva: deleta o usuário, seus dependentes (recursivamente),
e todas as collections relacionadas. NÃO faz parte da suite de testes
automatizados — executar manualmente.

Collections limpas (todas escopadas por projectId):
    - users/{uid}                        (doc + sub-collections push_tokens, historic)
    - memberships                        (where projectId AND userId)
    - attendance                         (where projectId AND userId)
        + timeline_entries/attendance_{id} (deterministic projection)
    - donations                          (where projectId AND userId)
        + timeline_entries/donation_{id}
    - posts                              (where projectId AND authorUid)
        + timeline_entries/post_{id}
        + eventos_calendario/post_{id}
    - medical_history/{projectId}_{uid}
    - timeline_entries/account_{uid}
    - Firebase Auth user record

Dependentes (users com guardianUid==uid AND isDependent==true) passam
pela mesma cascade recursivamente.

Uso:
    # Por e-mail (busca no Firestore)
    OPENSSL_CONF="" uv run python scripts/delete_account.py \\
        --email joao@example.com

    # Por UID direto
    OPENSSL_CONF="" uv run python scripts/delete_account.py --uid abc123

    # Dry run (mostra o que seria apagado, sem apagar)
    OPENSSL_CONF="" uv run python scripts/delete_account.py \\
        --email joao@example.com --dry-run

    # Especificar projeto (default: spartacus-artes-marciais)
    OPENSSL_CONF="" uv run python scripts/delete_account.py \\
        --email joao@example.com --project-id outro-projeto
"""

import argparse
import os
import sys

import firebase_admin
from firebase_admin import auth, credentials, firestore

_DEFAULT_PROJECT_ID = "spartacus-artes-marciais"


def init_firebase() -> None:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.ApplicationDefault())


def find_uid_by_email(db, email: str) -> str | None:
    results = list(
        db.collection("users")
        .where("email", "==", email.lower())
        .limit(1)
        .stream()
    )
    if results:
        return results[0].id
    return None


def find_auth_uid_by_email(email: str) -> str | None:
    try:
        record = auth.get_user_by_email(email.lower())
        return record.uid
    except auth.UserNotFoundError:
        return None


def _delete_doc(ref, dry_run: bool) -> bool:
    """Delete a doc if it exists. Returns True if it existed."""
    if not ref.get().exists:
        return False
    if not dry_run:
        ref.delete()
    return True


def _add(counts: dict, key: str, n: int = 1) -> None:
    counts[key] = counts.get(key, 0) + n


def delete_user_cascade(
    db,
    project_id: str,
    uid: str,
    dry_run: bool,
    indent: str = "",
) -> dict[str, int]:
    """Recursively delete a user and all their data scoped to a project.

    Returns a dict {collection_label: count_deleted}.
    """
    counts: dict[str, int] = {}
    print(f"{indent}→ Cascade para uid={uid}")

    # ── 1. Dependentes (recursivo) ──────────────────────────────────────────
    dep_docs = list(
        db.collection("users")
        .where("guardianUid", "==", uid)
        .where("isDependent", "==", True)
        .stream()
    )
    for dep in dep_docs:
        print(f"{indent}  Dependente encontrado: {dep.id}")
        sub_counts = delete_user_cascade(
            db, project_id, dep.id, dry_run, indent + "    "
        )
        for k, v in sub_counts.items():
            _add(counts, k, v)

    # ── 2. attendance + timeline_entries/attendance_{id} ────────────────────
    attendance_records = list(
        db.collection("attendance")
        .where("projectId", "==", project_id)
        .where("userId", "==", uid)
        .stream()
    )
    for p in attendance_records:
        tl_ref = db.collection("timeline_entries").document(f"attendance_{p.id}")
        if _delete_doc(tl_ref, dry_run):
            _add(counts, "timeline_entries (attendance)")
        if not dry_run:
            p.reference.delete()
    _add(counts, "attendance", len(attendance_records))

    # ── 3. donations + timeline_entries/donation_{id} ───────────────────────
    donations = list(
        db.collection("donations")
        .where("projectId", "==", project_id)
        .where("userId", "==", uid)
        .stream()
    )
    for d in donations:
        tl_ref = db.collection("timeline_entries").document(f"donation_{d.id}")
        if _delete_doc(tl_ref, dry_run):
            _add(counts, "timeline_entries (donation)")
        if not dry_run:
            d.reference.delete()
    _add(counts, "donations", len(donations))

    # ── 4. posts + timeline_entries/post_{id} + eventos_calendario/post_{id}
    posts = list(
        db.collection("posts")
        .where("projectId", "==", project_id)
        .where("authorUid", "==", uid)
        .stream()
    )
    for post in posts:
        tl_ref = db.collection("timeline_entries").document(f"post_{post.id}")
        if _delete_doc(tl_ref, dry_run):
            _add(counts, "timeline_entries (post)")
        cal_ref = db.collection("eventos_calendario").document(f"post_{post.id}")
        if _delete_doc(cal_ref, dry_run):
            _add(counts, "eventos_calendario")
        if not dry_run:
            post.reference.delete()
    _add(counts, "posts", len(posts))

    # ── 5. timeline_entries/account_{uid} ───────────────────────────────────
    account_tl = db.collection("timeline_entries").document(f"account_{uid}")
    if _delete_doc(account_tl, dry_run):
        _add(counts, "timeline_entries (account)")

    # ── 6. memberships (escopo: projeto + user) ─────────────────────────────
    memberships = list(
        db.collection("memberships")
        .where("projectId", "==", project_id)
        .where("userId", "==", uid)
        .stream()
    )
    for m in memberships:
        if not dry_run:
            m.reference.delete()
    _add(counts, "memberships", len(memberships))

    # ── 7. medical_history/{projectId}_{uid} ────────────────────────────────
    mh_ref = db.collection("medical_history").document(f"{project_id}_{uid}")
    if _delete_doc(mh_ref, dry_run):
        _add(counts, "medical_history")

    # ── 8. push_tokens sub-collection ───────────────────────────────────────
    push_tokens = list(
        db.collection("users").document(uid).collection("push_tokens").stream()
    )
    for pt in push_tokens:
        if not dry_run:
            pt.reference.delete()
    _add(counts, "push_tokens", len(push_tokens))

    # ── 8b. historic sub-collection (RFC-12) ────────────────────────────────
    historic = list(
        db.collection("users").document(uid).collection("historic").stream()
    )
    for h in historic:
        if not dry_run:
            h.reference.delete()
    _add(counts, "historic", len(historic))

    # ── 9. user doc ─────────────────────────────────────────────────────────
    user_ref = db.collection("users").document(uid)
    if _delete_doc(user_ref, dry_run):
        _add(counts, "users")

    return counts


def delete_auth_user(uid: str, dry_run: bool) -> bool:
    try:
        auth.get_user(uid)
    except auth.UserNotFoundError:
        return False
    if not dry_run:
        auth.delete_user(uid)
    return True


def _print_summary(counts: dict[str, int], action: str) -> None:
    if not counts or all(v == 0 for v in counts.values()):
        print("\n  Nenhum dado encontrado.\n")
        return
    print(f"\n  {action}:")
    width = max(len(k) for k in counts.keys() if counts.get(k, 0) > 0)
    for key in sorted(counts.keys()):
        v = counts[key]
        if v > 0:
            print(f"    {key:<{width}}  {v:>4}")
    total = sum(counts.values())
    print(f"    {'─' * width}  {'─' * 4}")
    print(f"    {'TOTAL':<{width}}  {total:>4}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apaga uma conta + todos os dados relacionados, "
            "escopado por projectId. Cascade recursivo para dependentes."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", help="E-mail da conta a apagar")
    group.add_argument("--uid", help="UID Firebase da conta a apagar")
    parser.add_argument(
        "--project-id",
        default=os.environ.get("ROOT_PROJECT_ID", _DEFAULT_PROJECT_ID),
        help=f"Project ID (default: {_DEFAULT_PROJECT_ID})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas mostra o que seria apagado, sem apagar",
    )
    args = parser.parse_args()

    init_firebase()
    db = firestore.client()

    # ── Resolve UID ──
    auth_uid: str | None = None
    if args.email:
        firestore_uid = find_uid_by_email(db, args.email)
        auth_uid = find_auth_uid_by_email(args.email)

        if not firestore_uid and not auth_uid:
            print(f"\n  Nenhuma conta encontrada para {args.email}\n")
            sys.exit(1)

        if firestore_uid and auth_uid and firestore_uid != auth_uid:
            print(
                f"\n  Atenção: UID Firestore ({firestore_uid}) "
                f"difere do Auth ({auth_uid})"
            )
            print("  Ambos serão limpos.\n")

        target_uid: str = firestore_uid or auth_uid or ""
        label = args.email
    else:
        target_uid = args.uid
        label = args.uid

    if not target_uid:
        print("\n  ERRO: target_uid vazio. Abortando.\n")
        sys.exit(1)

    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\n{'=' * 60}")
    print(f"  Deletar conta: {label}{mode}")
    print(f"  Projeto:       {args.project_id}")
    print(f"  UID alvo:      {target_uid}")
    if args.email and auth_uid and auth_uid != target_uid:
        print(f"  Auth UID extra: {auth_uid}")
    print(f"{'=' * 60}\n")

    if not args.dry_run:
        confirm = input(
            "  ⚠  ATENÇÃO: ação irreversível.\n"
            "  Tem certeza? [y/N] "
        )
        if confirm.lower() != "y":
            print("  Cancelado.\n")
            sys.exit(0)

    # ── Cascade delete (Firestore) ──
    counts = delete_user_cascade(
        db, args.project_id, target_uid, dry_run=args.dry_run
    )

    # ── Firebase Auth ──
    if delete_auth_user(target_uid, dry_run=args.dry_run):
        _add(counts, "Firebase Auth")

    # ── Auth UID divergente ──
    if (
        args.email
        and auth_uid
        and auth_uid != target_uid
        and delete_auth_user(auth_uid, dry_run=args.dry_run)
    ):
        _add(counts, "Firebase Auth (divergente)")

    action = "Seria apagado" if args.dry_run else "Apagado"
    _print_summary(counts, action)
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
