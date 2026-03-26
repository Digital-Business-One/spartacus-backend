#!/usr/bin/env python3
"""Seed ROOT project data into Firestore + Firebase Storage.

Seeds:
  - ROOT project document (projects collection)
  - Owner user + membership (users + memberships collections)
  - Logo upload to Firebase Storage

Usage (local dev with emulators running):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    FIREBASE_AUTH_EMULATOR_HOST=localhost:9099 \
    FIREBASE_STORAGE_EMULATOR_HOST=localhost:9199 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_root_project.py
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, firestore, storage

_ASSETS_DIR = Path(__file__).parent / "assets"

_ROOT_DATA = {
    "name": "Spartacus Artes Marciais",
    "razao_social": "Associacao Projeto Spartacus Artes Marciais",
    "cnpj": "59.933.142/0001-54",
    "address": "Rua Rotary Internacional, 270",
    "city": "Brasnorte",
    "state": "MT",
    "zip_code": "78350-000",
    "legal_nature": "Associação Privada",
    "founded_at": "2025-03-06",
    "is_root": True,
}

_OWNER = {
    "name": "Istanrley Aparecido Araujo Amaral",
    "email": "praquemdecide@gmail.com",
    "birthDate": "01/01/1990",
    "gender": "male",
    "phone": "65999999999",
    "whatsapp": "65999999999",
    "address": {
        "postalCode": "78350-000",
        "street": "Rua Rotary Internacional",
        "number": "270",
        "complement": "",
        "neighborhood": "Centro",
        "city": "Brasnorte",
        "state": "MT",
    },
}


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    gcp_project = os.getenv("GOOGLE_CLOUD_PROJECT", project_id)
    now = datetime.now(timezone.utc).isoformat()

    # ── Upload logo to Firebase Storage ────────────────────────────────────────
    storage_emulator = os.getenv("FIREBASE_STORAGE_EMULATOR_HOST")
    logo_path = _ASSETS_DIR / "logo.jpg"
    bucket_name = os.getenv(
        "FIREBASE_STORAGE_BUCKET",
        f"{gcp_project}.appspot.com" if storage_emulator else f"{gcp_project}.firebasestorage.app",
    )
    object_path = f"projects/{project_id}/logo.jpg"

    if storage_emulator:
        # Emulator: upload via REST API (SDK ignores EMULATOR_HOST for Storage)
        import requests as req
        upload_url = (
            f"http://{storage_emulator}/upload/storage/v1/b/{bucket_name}/o"
            f"?uploadType=media&name={object_path}"
        )
        with open(logo_path, "rb") as f:
            resp = req.post(upload_url, data=f, headers={"Content-Type": "image/jpeg"})
        resp.raise_for_status()
        logo_url = (
            f"http://{storage_emulator}/v0/b/{bucket_name}/o/"
            f"{object_path.replace('/', '%2F')}?alt=media"
        )
    else:
        bucket = storage.bucket(bucket_name)
        blob = bucket.blob(object_path)
        blob.upload_from_filename(str(logo_path), content_type="image/jpeg")
        blob.make_public()
        logo_url = blob.public_url

    # ── Upsert ROOT project document ─────────────────────────────────────────
    db = firestore.client()
    doc_data = {
        "id": project_id,
        **_ROOT_DATA,
        "logo_url": logo_url,
        "created_at": now,
    }
    db.collection("projects").document(project_id).set(doc_data, merge=True)
    print(f"ROOT project '{project_id}' seeded.")
    print(f"  logo_url: {logo_url}")

    # ── Create owner Firebase Auth user (idempotent) ─────────────────────────
    owner_email = _OWNER["email"]
    try:
        user_record = auth.get_user_by_email(owner_email)
        uid = user_record.uid
        print(f"Owner user already exists: {uid}")
    except auth.UserNotFoundError:
        user_record = auth.create_user(
            email=owner_email,
            email_verified=True,
            display_name=_OWNER["name"],
            password="Spartacus2025!",
        )
        uid = user_record.uid
        print(f"Owner user created: {uid}")

    # Set custom claims for owner
    auth.set_custom_user_claims(uid, {
        "projects": {project_id: ["owner"]},
    })

    # ── Upsert owner user document ───────────────────────────────────────────
    db.collection("users").document(uid).set(
        {
            **_OWNER,
            "approvalStatus": "approved",
            "isDependent": False,
            "classIds": [],
            "createdAt": now,
        },
        merge=True,
    )

    # ── Upsert owner membership ──────────────────────────────────────────────
    db.collection("memberships").document(f"{project_id}_{uid}").set(
        {
            "projectId": project_id,
            "userId": uid,
            "roles": ["owner"],
            "status": "active",
            "joined_at": now,
        },
        merge=True,
    )
    print(f"Owner membership created: {owner_email} → {project_id}")

    print("\nSeed complete.")


if __name__ == "__main__":
    run()
