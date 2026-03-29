#!/usr/bin/env python3
"""Seed admin account for backoffice/app access.

Creates a Firebase Auth user with email/password and full access
(owner + assistant roles) to the ROOT project.

Usage (local dev with emulators running):
    uv run python seeds/seed_admin_account.py

Usage (production — requires ADC):
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_admin_account.py
"""

import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, firestore

_ADMIN = {
    "email": "admin@spartacus.app.br",
    "password": "753!159#486$624",
    "name": "Administrador Spartacus",
    "birthDate": "15/06/1985",
    "gender": "male",
    "phone": "65988887777",
    "whatsapp": "65988887777",
    "address": {
        "postalCode": "78350-000",
        "street": "Rua Rotary Internacional",
        "number": "270",
        "complement": "",
        "neighborhood": "Centro",
        "city": "Brasnorte",
        "state": "MT",
    },
    "roles": ["owner", "assistant"],
}


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    now = datetime.now(timezone.utc).isoformat()
    db = firestore.client()

    email = _ADMIN["email"]
    password = _ADMIN["password"]
    roles = _ADMIN["roles"]

    # ── Create Firebase Auth user (idempotent) ───────────────────────────
    try:
        user_record = auth.get_user_by_email(email)
        uid = user_record.uid
        # Update password in case it changed
        auth.update_user(uid, password=password, email_verified=True)
        print(f"Admin user already exists: {uid} — password updated")
    except auth.UserNotFoundError:
        user_record = auth.create_user(
            email=email,
            password=password,
            email_verified=True,
            display_name=_ADMIN["name"],
        )
        uid = user_record.uid
        print(f"Admin user created: {uid}")

    # ── Set custom claims ────────────────────────────────────────────────
    auth.set_custom_user_claims(uid, {
        "projects": {project_id: roles},
    })
    print(f"  Custom claims: projects.{project_id} = {roles}")

    # ── Upsert user document ─────────────────────────────────────────────
    db.collection("users").document(uid).set(
        {
            "name": _ADMIN["name"],
            "email": email,
            "birthDate": _ADMIN["birthDate"],
            "gender": _ADMIN["gender"],
            "phone": _ADMIN["phone"],
            "whatsapp": _ADMIN["whatsapp"],
            "address": _ADMIN["address"],
            "approvalStatus": "approved",
            "isDependent": False,
            "classIds": [],
            "createdAt": now,
        },
        merge=True,
    )
    print(f"  User document: users/{uid}")

    # ── Upsert membership ────────────────────────────────────────────────
    db.collection("memberships").document(f"{project_id}_{uid}").set(
        {
            "projectId": project_id,
            "userId": uid,
            "roles": roles,
            "status": "active",
            "joined_at": now,
        },
        merge=True,
    )
    print(f"  Membership: {email} → {project_id} [{', '.join(roles)}]")

    print(f"\nAdmin account ready:")
    print(f"  Email: {email}")
    print(f"  Password: {password}")
    print(f"  Roles: {', '.join(roles)}")
    print(f"  Status: approved (login enabled)")


if __name__ == "__main__":
    run()
