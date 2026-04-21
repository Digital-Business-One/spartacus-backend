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

_OWNER_EMAIL = "admin@spartacus.app.br"
_OWNER_NAME = "Admin Spartacus"


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
        f"{gcp_project}.firebasestorage.app",
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
        # Public access is handled by IAM (uniform bucket-level access),
        # not legacy ACL — so no blob.make_public() needed.
        logo_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{object_path.replace('/', '%2F')}?alt=media"

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

    # ── Locate admin account (created by seed_admin_account.py) ────────────────
    # If admin user exists, ensure owner membership for the ROOT project.
    # Does NOT create the user — that's seed_admin_account.py's responsibility.
    try:
        user_record = auth.get_user_by_email(_OWNER_EMAIL)
        uid = user_record.uid
        print(f"Admin user found: {uid} ({_OWNER_EMAIL})")

        # Ensure owner role in custom claims
        existing_claims = user_record.custom_claims or {}
        projects_claims = existing_claims.get("projects", {})
        current_roles = set(projects_claims.get(project_id, []))
        if "owner" not in current_roles:
            current_roles.add("owner")
            projects_claims[project_id] = list(current_roles)
            auth.set_custom_user_claims(uid, {"projects": projects_claims})

        # Upsert owner membership
        db.collection("memberships").document(f"{project_id}_{uid}").set(
            {
                "projectId": project_id,
                "userId": uid,
                "roles": list(current_roles),
                "status": "active",
                "joined_at": now,
            },
            merge=True,
        )
        print(f"Owner membership ensured: {_OWNER_EMAIL} → {project_id}")
    except auth.UserNotFoundError:
        print(f"Admin user ({_OWNER_EMAIL}) not found — run seed_admin_account first.")

    print("\nSeed complete.")


if __name__ == "__main__":
    run()
