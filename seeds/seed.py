#!/usr/bin/env python3
"""Seed ROOT project data into Firestore + Firebase Storage.

Usage (local dev with emulators running):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    FIREBASE_AUTH_EMULATOR_HOST=localhost:9099 \
    FIREBASE_STORAGE_EMULATOR_HOST=localhost:9199 \
    GOOGLE_CLOUD_PROJECT=demo-spartacus \
    ROOT_PROJECT_ID=demo-spartacus \
    uv run python seeds/seed.py
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import firebase_admin
from firebase_admin import firestore, storage

_ASSETS_DIR = Path(__file__).parent / "assets"

_ROOT_DATA = {
    "name": "Spartacus Artes Marciais",
    "razao_social": "Associacao Projeto Spartacus Artes Marciais",
    "cnpj": "59.933.142/0001-54",
    "address": "Rua Rotary Internacional, 270",
    "city": "Brasnorte",
    "state": "MT",
    "zip_code": "78350-000",
    "legal_nature": "Associação Privada (399-9)",
    "founded_at": "2025-03-06",
    "is_root": True,
}


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "demo-spartacus")
    gcp_project = os.getenv("GOOGLE_CLOUD_PROJECT", project_id)

    # Upload logo to Firebase Storage
    bucket = storage.bucket(f"{gcp_project}.appspot.com")
    logo_path = _ASSETS_DIR / "logo.jpg"
    blob = bucket.blob(f"projects/{project_id}/logo.jpg")
    blob.upload_from_filename(str(logo_path), content_type="image/jpeg")
    blob.make_public()
    logo_url = blob.public_url

    # Upsert ROOT project document
    db = firestore.client()
    now = datetime.now(timezone.utc).isoformat()
    doc_data = {
        "id": project_id,
        **_ROOT_DATA,
        "logo_url": logo_url,
        "created_at": now,
    }
    db.collection("projects").document(project_id).set(doc_data, merge=True)

    print(f"ROOT project '{project_id}' seeded successfully.")
    print(f"  logo_url: {logo_url}")


if __name__ == "__main__":
    run()
