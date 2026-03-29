#!/usr/bin/env python3
"""Delete all test accounts and their relationships.

Removes every Firebase Auth user and Firestore doc whose email
ends with @test.spartacus.app.br. Also removes their memberships.

Safe: never touches the admin account (admin@spartacus.app.br)
or the root project doc.

Usage (local dev with emulators running):
    uv run python seeds/seed_clean_test_data.py
"""

import os

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, firestore

_TEST_DOMAIN = "@test.spartacus.app.br"


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    db = firestore.client()

    # ── 1. Find all test users in Firestore ─────────────────────────────
    print("Scanning Firestore users...")
    test_uids: list[str] = []
    for doc in db.collection("users").stream():
        data = doc.to_dict()
        email = data.get("email", "")
        if email.endswith(_TEST_DOMAIN):
            test_uids.append(doc.id)

    if not test_uids:
        print("No test accounts found. Nothing to clean.")
        return

    print(f"Found {len(test_uids)} test account(s). Deleting...\n")

    deleted_auth = 0
    deleted_users = 0
    deleted_mems = 0

    for uid in test_uids:
        # Delete Firestore user doc
        db.collection("users").document(uid).delete()
        deleted_users += 1

        # Delete membership doc
        mem_ref = db.collection("memberships").document(f"{project_id}_{uid}")
        if mem_ref.get().exists:
            mem_ref.delete()
            deleted_mems += 1

        # Delete Firebase Auth user
        try:
            auth.delete_user(uid)
            deleted_auth += 1
        except auth.UserNotFoundError:
            pass

    # ── Summary ──────────────────────────────────────────────────────────
    print("=" * 50)
    print(f"  {deleted_auth} Firebase Auth user(s) deleted")
    print(f"  {deleted_users} Firestore user doc(s) deleted")
    print(f"  {deleted_mems} membership doc(s) deleted")
    print("=" * 50)


if __name__ == "__main__":
    run()
