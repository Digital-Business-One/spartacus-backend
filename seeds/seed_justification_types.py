#!/usr/bin/env python3
"""Seed the `justification_types` catalog (absence-justification types).

Idempotent: creates one doc per type (id "{projectId}_{slug}"). The 4
confirmed types: Saúde (anexo obrigatório), Viagem, Compromisso escolar,
Outro — editable later via backoffice CRUD (`justification_types` router).

Usage (local dev):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_justification_types.py

Usage (production — requires ADC):
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_justification_types.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import firestore

_COLLECTION = "justification_types"


def _type(slug: str, name: str, order: int, requires_attachment: bool) -> dict:
    return {
        "slug": slug,
        "name": name,
        "allowsAttachment": True,
        "requiresAttachment": requires_attachment,
        "active": True,
        "order": order,
    }


def _types(project_id: str) -> list[dict]:
    return [
        {"projectId": project_id, **_type("saude", "Saúde", 1, True)},
        {"projectId": project_id, **_type("viagem", "Viagem", 2, False)},
        {
            "projectId": project_id,
            **_type("compromisso-escolar", "Compromisso escolar", 3, False),
        },
        {"projectId": project_id, **_type("outro", "Outro", 4, False)},
    ]


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    db = firestore.client()
    col = db.collection(_COLLECTION)

    print(f"Seeding justification_types for project {project_id}...")
    for t in _types(project_id):
        doc_id = f"{project_id}_{t['slug']}"
        col.document(doc_id).set(t)  # overwrite — keeps defaults current
        print(f"  ✓ {doc_id} ({t['name']})")
    print("Done.")


if __name__ == "__main__":
    run()
