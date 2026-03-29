#!/usr/bin/env python3
"""Seed guardian → dependent (student) family links.

Runs AFTER seed_test_accounts.py. Finds all guardian accounts already
seeded and creates 1-3 dependent student children linked via guardianUid.

Idempotent: checks if dependents already exist before creating.

Usage (local dev with emulators running):
    uv run python seeds/seed_families.py
"""

import os
import random
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, firestore

# ── Child name pools ─────────────────────────────────────────────────────────

_CHILD_NAMES_M = [
    "Enzo", "Théo", "Heitor", "Noah", "Gael",
    "Ravi", "Liam", "Caleb", "Isaac", "Bento",
]

_CHILD_NAMES_F = [
    "Helena", "Alice", "Liz", "Valentina", "Heloísa",
    "Sophia", "Cecília", "Eloá", "Lívia", "Ayla",
]

_CLASS_IDS = [
    "muay-thai-kids",
    "capoeira",
    "jiu-jitsu-kids-matutino",
    "jiu-jitsu-kids-vespertino",
]


def _random_child_birth():
    year = random.randint(2012, 2020)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{day:02d}/{month:02d}/{year}"


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    now = datetime.now(timezone.utc).isoformat()
    db = firestore.client()

    # ── Find all guardian memberships ────────────────────────────────────
    guardian_uids: list[str] = []
    mems = (
        db.collection("memberships")
        .where("projectId", "==", project_id)
        .stream()
    )
    for m in mems:
        data = m.to_dict()
        if "guardian" in data.get("roles", []):
            guardian_uids.append(data["userId"])

    if not guardian_uids:
        print("No guardian accounts found. Run seed_test_accounts.py first.")
        return

    print(f"Found {len(guardian_uids)} guardian(s). Creating family links...\n")

    created = 0
    skipped = 0
    child_idx = 0

    for g_uid in guardian_uids:
        # Read guardian doc to get their status
        g_doc = db.collection("users").document(g_uid).get()
        if not g_doc.exists:
            continue
        g_data = g_doc.to_dict()
        g_status = g_data.get("approvalStatus", "pending_approval")

        # Decide dependent status based on guardian status
        dep_status = _dependent_status(g_status)

        # Check how many dependents already exist for this guardian
        existing = list(
            db.collection("users")
            .where("guardianUid", "==", g_uid)
            .where("isDependent", "==", True)
            .stream()
        )
        if existing:
            skipped += len(existing)
            continue  # Already seeded — skip

        # Create 1-3 dependent children
        num_children = random.randint(1, 3)
        for j in range(num_children):
            gender = "male" if (child_idx + j) % 2 == 0 else "female"
            names = _CHILD_NAMES_M if gender == "male" else _CHILD_NAMES_F
            name = names[(child_idx + j) % len(names)]

            dep_uid = f"{g_uid}_dep_{j}"
            dep_email = f"dep.{dep_uid[:8]}.{j}@test.spartacus.app.br"

            class_count = random.randint(1, 2)
            slugs = random.sample(_CLASS_IDS, min(class_count, len(_CLASS_IDS)))
            class_ids = [f"{project_id}_{s}" for s in slugs]

            # Create Firebase Auth user for dependent (idempotent)
            try:
                auth.get_user(dep_uid)
            except auth.UserNotFoundError:
                auth.create_user(
                    uid=dep_uid,
                    email=dep_email,
                    password="Test1234!",
                    email_verified=True,
                    display_name=name,
                )

            # Firestore user doc
            db.collection("users").document(dep_uid).set(
                {
                    "name": name,
                    "email": dep_email,
                    "birthDate": _random_child_birth(),
                    "gender": gender,
                    "guardianUid": g_uid,
                    "isDependent": True,
                    "emailVerified": True,
                    "approvalStatus": dep_status,
                    "classIds": class_ids,
                    "createdAt": now,
                },
                merge=True,
            )

            # Membership
            mem_status = "active" if dep_status == "approved" else "pending_approval"
            db.collection("memberships").document(
                f"{project_id}_{dep_uid}"
            ).set(
                {
                    "projectId": project_id,
                    "userId": dep_uid,
                    "roles": ["student"],
                    "status": mem_status,
                    "joined_at": now,
                },
                merge=True,
            )

            if dep_status == "approved":
                auth.set_custom_user_claims(
                    dep_uid, {"projects": {project_id: ["student"]}}
                )

            created += 1

        child_idx += num_children

    # ── Summary ──────────────────────────────────────────────────────────
    print("=" * 50)
    print(f"  {created} dependent student(s) created")
    if skipped:
        print(f"  {skipped} existing dependent(s) skipped")
    print("=" * 50)
    print(f"\n  Password for all dependents: Test1234!")


def _dependent_status(guardian_status: str) -> str:
    """Derive dependent status from guardian status.

    Mirrors the real approval flow:
    - Guardian approved → dependents go to waiting_medical_history (anamnese)
    - Guardian pending  → dependents stay pending_approval
    - Guardian in other states → dependents follow same state
    """
    if guardian_status == "approved":
        return "waiting_medical_history"
    if guardian_status == "pending_approval":
        return "pending_approval"
    if guardian_status in (
        "waiting_medical_history",
        "pending_medical_history_approval",
    ):
        return "waiting_medical_history"
    # Terminal or review states — dependents follow guardian
    return guardian_status


if __name__ == "__main__":
    run()
