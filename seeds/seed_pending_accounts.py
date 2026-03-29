#!/usr/bin/env python3
"""Seed realistic pending accounts for the Pendentes tab.

Creates:
 - Guardians with 1-3 dependent students (linked via guardianUid)
 - Standalone students without guardians
 - All start at pending_approval. Mix of emailVerified true/false.

All accounts land in the "Pendentes" tab in the backoffice.
Idempotent: skips accounts whose email already exists.

Usage (local dev with emulators running):
    uv run python seeds/seed_pending_accounts.py
"""

import os
import random
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, firestore

# ── Name pools ───────────────────────────────────────────────────────────────

_GUARDIAN_NAMES = [
    ("Fernanda", "Oliveira", "female"),
    ("Ricardo", "Santos", "male"),
    ("Patrícia", "Costa", "female"),
    ("Marcos", "Almeida", "male"),
    ("Juliana", "Ferreira", "female"),
    ("Carlos", "Nascimento", "male"),
    ("Aline", "Barbosa", "female"),
    ("Roberto", "Martins", "male"),
]

_CHILD_NAMES = [
    ("Enzo", "male"),
    ("Helena", "female"),
    ("Théo", "male"),
    ("Alice", "female"),
    ("Gael", "male"),
    ("Valentina", "female"),
    ("Noah", "male"),
    ("Sophia", "female"),
    ("Heitor", "male"),
    ("Liz", "female"),
    ("Ravi", "male"),
    ("Cecília", "female"),
    ("Bento", "male"),
    ("Lívia", "female"),
    ("Caleb", "male"),
    ("Eloá", "female"),
    ("Isaac", "male"),
    ("Ayla", "female"),
]

_STANDALONE_STUDENTS = [
    ("Lucas", "Pereira", "male"),
    ("Gabriela", "Lima", "female"),
    ("Pedro", "Araújo", "male"),
    ("Beatriz", "Rodrigues", "female"),
    ("Matheus", "Gomes", "male"),
]

_ADDRESS = {
    "postalCode": "78350-000",
    "street": "Rua das Flores",
    "number": "100",
    "complement": "",
    "neighborhood": "Centro",
    "city": "Brasnorte",
    "state": "MT",
}

_CLASS_IDS_KIDS = [
    "muay-thai-kids",
    "capoeira",
    "jiu-jitsu-kids-matutino",
    "jiu-jitsu-kids-vespertino",
]


def _phone():
    return f"65{random.randint(900000000, 999999999)}"


def _child_birth():
    y = random.randint(2012, 2020)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{d:02d}/{m:02d}/{y}"


def _adult_birth():
    y = random.randint(1975, 2000)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{d:02d}/{m:02d}/{y}"


def _teen_birth():
    y = random.randint(2007, 2012)
    m = random.randint(1, 12)
    d = random.randint(1, 28)
    return f"{d:02d}/{m:02d}/{y}"


def _random_classes(project_id: str, count: int = 1) -> list[str]:
    slugs = random.sample(_CLASS_IDS_KIDS, min(count, len(_CLASS_IDS_KIDS)))
    return [f"{project_id}_{s}" for s in slugs]


def _email(first: str, last: str, tag: str) -> str:
    return f"{first.lower()}.{last.lower()}.{tag}@test.spartacus.app.br"


def _create_or_get_uid(
    email: str, password: str, name: str, email_verified: bool, uid: str | None = None,
) -> str:
    """Create Firebase Auth user or return existing uid. Idempotent."""
    try:
        if uid:
            user = auth.get_user(uid)
        else:
            user = auth.get_user_by_email(email)
        return user.uid
    except auth.UserNotFoundError:
        kwargs: dict = dict(
            email=email,
            password=password,
            email_verified=email_verified,
            display_name=name,
        )
        if uid:
            kwargs["uid"] = uid
        return auth.create_user(**kwargs).uid


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    now = datetime.now(timezone.utc).isoformat()
    db = firestore.client()
    pw = "Test1234!"

    created_guardians = 0
    created_deps = 0
    created_standalone = 0
    skipped = 0
    child_idx = 0

    # ── 1. Guardians with dependents ─────────────────────────────────────

    for i, (first, last, gender) in enumerate(_GUARDIAN_NAMES):
        # Alternate emailVerified: half verified, half not
        ev = i % 2 != 0
        email = _email(first, last, "resp")

        # Check idempotency
        existing = list(
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
        if existing:
            skipped += 1
            child_idx += random.randint(1, 3)
            continue

        uid = _create_or_get_uid(email, pw, f"{first} {last}", ev)
        auth.set_custom_user_claims(uid, {})

        db.collection("users").document(uid).set({
            "name": f"{first} {last}",
            "email": email,
            "birthDate": _adult_birth(),
            "gender": gender,
            "phone": _phone(),
            "whatsapp": _phone(),
            "address": _ADDRESS,
            "approvalStatus": "pending_approval",
            "emailVerified": ev,
            "isDependent": False,
            "classIds": [],
            "createdAt": now,
        }, merge=True)

        db.collection("memberships").document(f"{project_id}_{uid}").set({
            "projectId": project_id,
            "userId": uid,
            "roles": ["guardian"],
            "status": "pending_approval",
            "joined_at": now,
        }, merge=True)

        created_guardians += 1

        # Create 1-3 dependents
        num_deps = random.randint(1, 3)
        for j in range(num_deps):
            child_name, child_gender = _CHILD_NAMES[child_idx % len(_CHILD_NAMES)]
            child_idx += 1
            dep_uid = f"{uid}_dep_{j}"
            dep_email = _email(child_name, last, f"dep{j}")

            _create_or_get_uid(dep_email, pw, f"{child_name} {last}", True, uid=dep_uid)

            class_ids = _random_classes(project_id, random.randint(1, 2))

            db.collection("users").document(dep_uid).set({
                "name": f"{child_name} {last}",
                "email": dep_email,
                "birthDate": _child_birth(),
                "gender": child_gender,
                "guardianUid": uid,
                "isDependent": True,
                "approvalStatus": "pending_approval",
                "emailVerified": True,
                "classIds": class_ids,
                "createdAt": now,
            }, merge=True)

            db.collection("memberships").document(f"{project_id}_{dep_uid}").set({
                "projectId": project_id,
                "userId": dep_uid,
                "roles": ["student"],
                "status": "pending_approval",
                "joined_at": now,
            }, merge=True)

            created_deps += 1

    # ── 2. Standalone students (no guardian) ─────────────────────────────

    for i, (first, last, gender) in enumerate(_STANDALONE_STUDENTS):
        ev = i % 2 != 0
        email = _email(first, last, "aluno")

        existing = list(
            db.collection("users")
            .where("email", "==", email)
            .limit(1)
            .stream()
        )
        if existing:
            skipped += 1
            continue

        uid = _create_or_get_uid(email, pw, f"{first} {last}", ev)
        auth.set_custom_user_claims(uid, {})

        class_ids = _random_classes(project_id, random.randint(1, 2))

        db.collection("users").document(uid).set({
            "name": f"{first} {last}",
            "email": email,
            "birthDate": _teen_birth(),
            "gender": gender,
            "phone": _phone() if random.random() > 0.5 else "",
            "address": _ADDRESS,
            "approvalStatus": "pending_approval",
            "emailVerified": ev,
            "isDependent": False,
            "classIds": class_ids,
            "createdAt": now,
        }, merge=True)

        db.collection("memberships").document(f"{project_id}_{uid}").set({
            "projectId": project_id,
            "userId": uid,
            "roles": ["student"],
            "status": "pending_approval",
            "joined_at": now,
        }, merge=True)

        created_standalone += 1

    # ── Summary ──────────────────────────────────────────────────────────
    print("=" * 50)
    print(f"  {created_guardians} guardian(s) created")
    print(f"  {created_deps} dependent student(s) created")
    print(f"  {created_standalone} standalone student(s) created")
    if skipped:
        print(f"  {skipped} existing account(s) skipped")
    total = created_guardians + created_deps + created_standalone
    print(f"  {total} total accounts")
    print("=" * 50)
    print(f"\n  Password: Test1234!")
    print(f"  All appear in the 'Pendentes' tab")


if __name__ == "__main__":
    run()
