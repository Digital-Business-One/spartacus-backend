#!/usr/bin/env python3
"""
Script utilitário para apagar completamente uma conta do Firestore e Firebase Auth.

Remove: documento em users, memberships vinculadas, dependentes, e o registro
no Firebase Authentication. Útil para resetar contas durante testes.

NÃO faz parte da suite de testes automatizados — executar manualmente.

Uso:
    # Por e-mail (busca no Firestore)
    OPENSSL_CONF="" uv run python scripts/delete_account.py --email joao@example.com

    # Por UID direto
    OPENSSL_CONF="" uv run python scripts/delete_account.py --uid abc123

    # Dry run (mostra o que seria apagado, sem apagar)
    OPENSSL_CONF="" uv run python scripts/delete_account.py --email joao@example.com --dry-run
"""

import argparse
import sys

import firebase_admin
from firebase_admin import auth, credentials, firestore


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


def delete_account(uid: str, dry_run: bool = False) -> None:
    db = firestore.client()
    deleted = []

    # 1. Documento do usuário
    user_ref = db.collection("users").document(uid)
    user_doc = user_ref.get()
    if user_doc.exists:
        deleted.append(f"  users/{uid}")
        if not dry_run:
            user_ref.delete()

    # 2. Dependentes (uid_dep_*)
    dep_docs = list(
        db.collection("users")
        .where("guardianUid", "==", uid)
        .stream()
    )
    for dep in dep_docs:
        deleted.append(f"  users/{dep.id}")
        if not dry_run:
            dep.reference.delete()
        # Membership do dependente
        dep_memberships = list(
            db.collection("memberships")
            .where("userId", "==", dep.id)
            .stream()
        )
        for m in dep_memberships:
            deleted.append(f"  memberships/{m.id}")
            if not dry_run:
                m.reference.delete()

    # 3. Memberships do usuário principal
    memberships = list(
        db.collection("memberships")
        .where("userId", "==", uid)
        .stream()
    )
    for m in memberships:
        deleted.append(f"  memberships/{m.id}")
        if not dry_run:
            m.reference.delete()

    # 4. Presenças do usuário
    presencas = list(
        db.collection("presencas")
        .where("userId", "==", uid)
        .stream()
    )
    for p in presencas:
        deleted.append(f"  presencas/{p.id}")
        if not dry_run:
            p.reference.delete()

    # 5. Firebase Auth
    auth_deleted = False
    try:
        auth.get_user(uid)
        deleted.append(f"  Firebase Auth: {uid}")
        if not dry_run:
            auth.delete_user(uid)
        auth_deleted = True
    except auth.UserNotFoundError:
        pass

    # Resumo
    action = "Seria apagado" if dry_run else "Apagado"
    if deleted:
        print(f"\n  {action}:")
        for item in deleted:
            print(item)
        print()
    else:
        print("\n  Nenhum dado encontrado para esse usuário.\n")

    return len(deleted) > 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apaga completamente uma conta do Firestore e Firebase Auth."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", help="E-mail da conta a apagar")
    group.add_argument("--uid", help="UID Firebase da conta a apagar")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas mostra o que seria apagado, sem apagar",
    )
    args = parser.parse_args()

    init_firebase()
    db = firestore.client()

    if args.email:
        uid = find_uid_by_email(db, args.email)
        auth_uid = find_auth_uid_by_email(args.email)

        if not uid and not auth_uid:
            print(f"\n  Nenhuma conta encontrada para {args.email}\n")
            sys.exit(1)

        if uid and auth_uid and uid != auth_uid:
            print(f"\n  Atenção: UID Firestore ({uid}) difere do Auth ({auth_uid})")
            print("  Ambos serão limpos.\n")

        target_uid = uid or auth_uid
        label = args.email
    else:
        target_uid = args.uid
        label = args.uid

    mode = " (DRY RUN)" if args.dry_run else ""
    print(f"\n{'='*60}")
    print(f"  Deletar conta: {label}{mode}")
    print(f"  UID: {target_uid}")
    print(f"{'='*60}")

    if not args.dry_run:
        confirm = input("\n  Tem certeza? Essa ação é irreversível. [y/N] ")
        if confirm.lower() != "y":
            print("  Cancelado.\n")
            sys.exit(0)

    delete_account(target_uid, dry_run=args.dry_run)

    # Se UIDs divergem, limpar o Auth do segundo também
    if args.email and auth_uid and uid and auth_uid != uid:
        print(f"  Limpando Auth UID divergente: {auth_uid}")
        if not args.dry_run:
            auth.delete_user(auth_uid)
        print(f"  Firebase Auth: {auth_uid}")

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
