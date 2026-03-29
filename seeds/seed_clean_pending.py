#!/usr/bin/env python3
"""Delete only accounts created by seed_pending_accounts.py.

Targets emails matching:
  *.resp@test.spartacus.app.br  (guardians)
  *.dep*@test.spartacus.app.br  (dependents)
  *.aluno@test.spartacus.app.br (standalone students)

Leaves other test accounts (seed_test_accounts.py) untouched.

Usage (local dev with emulators running):
    uv run python seeds/seed_clean_pending.py
"""

import os

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, firestore

_PENDING_TAGS = (".resp@", ".dep", ".aluno@")
_TEST_DOMAIN = "@test.spartacus.app.br"


def _is_pending_seed(email: str) -> bool:
    if not email.endswith(_TEST_DOMAIN):
        return False
    return any(tag in email for tag in _PENDING_TAGS)


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    db = firestore.client()

    print("Scanning for pending seed accounts...")
    target_uids: list[str] = []
    for doc in db.collection("users").stream():
        email = doc.to_dict().get("email", "")
        if _is_pending_seed(email):
            target_uids.append(doc.id)

    if not target_uids:
        print("No pending seed accounts found.")
        return

    print(f"Found {len(target_uids)} account(s). Deleting...\n")

    deleted_auth = 0
    deleted_users = 0
    deleted_mems = 0

    for uid in target_uids:
        db.collection("users").document(uid).delete()
        deleted_users += 1

        mem_ref = db.collection("memberships").document(f"{project_id}_{uid}")
        if mem_ref.get().exists:
            mem_ref.delete()
            deleted_mems += 1

        try:
            auth.delete_user(uid)
            deleted_auth += 1
        except auth.UserNotFoundError:
            pass

    print("=" * 50)
    print(f"  {deleted_auth} Firebase Auth user(s) deleted")
    print(f"  {deleted_users} Firestore user doc(s) deleted")
    print(f"  {deleted_mems} membership doc(s) deleted")
    print("=" * 50)


if __name__ == "__main__":
    run()
