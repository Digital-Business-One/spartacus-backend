#!/usr/bin/env python3
"""Seed test accounts across all approval states and role profiles.

Creates 50+ accounts with diverse profiles and states for testing
the account state machine, backoffice approval flow, and UI rendering.

Usage (local dev with emulators running):
    uv run python seeds/seed_test_accounts.py
"""

import os
import random
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, firestore

# ── Test data ──────────────────────────────────────────────────────────────────

_FIRST_NAMES_M = [
    "Lucas", "Gabriel", "Pedro", "Arthur", "Miguel",
    "Rafael", "Matheus", "Davi", "Gustavo", "Bernardo",
    "Felipe", "Leonardo", "Bruno", "Henrique", "Daniel",
    "Thiago", "André", "Carlos", "Eduardo", "Marcos",
]

_FIRST_NAMES_F = [
    "Maria", "Ana", "Julia", "Beatriz", "Laura",
    "Gabriela", "Isabela", "Mariana", "Camila", "Fernanda",
    "Letícia", "Amanda", "Carolina", "Vitória", "Larissa",
    "Bruna", "Natália", "Patrícia", "Raquel", "Aline",
]

_LAST_NAMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Pereira",
    "Costa", "Ferreira", "Rodrigues", "Almeida", "Nascimento",
    "Lima", "Araújo", "Fernandes", "Barbosa", "Ribeiro",
    "Martins", "Carvalho", "Gomes", "Rocha", "Moreira",
]

_STATES = [
    "waiting_email_confirmation",
    "pending_approval",
    "waiting_medical_history",
    "pending_medical_history_approval",
    "approved",
    "rejected",
    "expelled",
    "archived",
    "waiting_registration_review",
    "revised_registration",
]

_ROLE_SETS = [
    ["student"],
    ["student"],
    ["guardian"],
    ["guardian"],
    ["teacher"],
    ["instructor"],
    ["student", "guardian"],
    ["supporter"],
    ["sponsor"],
    ["assistant"],
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


def _random_phone():
    return f"65{random.randint(900000000, 999999999)}"


def _random_birth(roles):
    if any(r in ("student",) for r in roles):
        year = random.randint(2008, 2018)
    else:
        year = random.randint(1970, 2000)
    month = random.randint(1, 12)
    day = random.randint(1, 28)
    return f"{day:02d}/{month:02d}/{year}"


def _gen_accounts(count_per_state=5):
    """Generate account specs: one per (state, role_set) combination."""
    accounts = []
    idx = 0
    for state in _STATES:
        for i in range(count_per_state):
            role_set = _ROLE_SETS[idx % len(_ROLE_SETS)]
            gender = "male" if idx % 2 == 0 else "female"
            names = _FIRST_NAMES_M if gender == "male" else _FIRST_NAMES_F
            first = names[idx % len(names)]
            last = _LAST_NAMES[idx % len(_LAST_NAMES)]
            name = f"{first} {last}"
            email = (
                f"{first.lower()}.{last.lower()}"
                f".{state[:4]}{i}@test.spartacus.app.br"
            )
            accounts.append({
                "name": name,
                "email": email,
                "gender": gender,
                "roles": role_set,
                "status": state,
                "birthDate": _random_birth(role_set),
                "phone": _random_phone(),
            })
            idx += 1
    return accounts


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    now = datetime.now(timezone.utc).isoformat()
    db = firestore.client()

    accounts = _gen_accounts(count_per_state=5)
    print(f"Generating {len(accounts)} test accounts...\n")

    # Stats
    state_count: dict[str, int] = {}
    role_count: dict[str, int] = {}

    for acc in accounts:
        email = acc["email"]
        status = acc["status"]
        roles = acc["roles"]
        password = "Test1234!"

        # Track stats
        state_count[status] = state_count.get(status, 0) + 1
        for r in roles:
            role_count[r] = role_count.get(r, 0) + 1

        # Create Firebase Auth user
        try:
            user_record = auth.get_user_by_email(email)
            uid = user_record.uid
        except auth.UserNotFoundError:
            user_record = auth.create_user(
                email=email,
                password=password,
                email_verified=status != "waiting_email_confirmation",
                display_name=acc["name"],
            )
            uid = user_record.uid

        # Set claims if approved or active membership needed
        if status == "approved":
            auth.set_custom_user_claims(
                uid, {"projects": {project_id: roles}}
            )

        # Firestore user doc
        db.collection("users").document(uid).set(
            {
                "name": acc["name"],
                "email": email,
                "birthDate": acc["birthDate"],
                "gender": acc["gender"],
                "phone": acc["phone"],
                "whatsapp": acc["phone"],
                "address": _ADDRESS,
                "approvalStatus": status,
                "isDependent": False,
                "classIds": [],
                "createdAt": now,
            },
            merge=True,
        )

        # Membership
        mem_status = "active" if status == "approved" else "pending_approval"
        db.collection("memberships").document(
            f"{project_id}_{uid}"
        ).set(
            {
                "projectId": project_id,
                "userId": uid,
                "roles": roles,
                "status": mem_status,
                "joined_at": now,
            },
            merge=True,
        )

    # ── Summary ────────────────────────────────────────────────────────────
    print("=" * 50)
    print(f"  {len(accounts)} test accounts created")
    print("=" * 50)
    print("\nBy status:")
    for s in _STATES:
        c = state_count.get(s, 0)
        print(f"  {s:45s} {c}")
    print(f"\n  {'TOTAL':45s} {sum(state_count.values())}")
    print("\nBy role:")
    for r in sorted(role_count.keys()):
        print(f"  {r:45s} {role_count[r]}")
    print(f"\n  Password for all test accounts: Test1234!")


if __name__ == "__main__":
    run()
