#!/usr/bin/env python3
"""Seed modalities + classes (turmas) for the ROOT Spartacus project.

Destructive: deletes ALL existing modalities and classes for the project.
Replaces the legacy seed_classes.py with the new separated model (RFC-08).

Usage (local dev):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_modalities_and_classes.py

Usage (production — requires ADC):
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_modalities_and_classes.py
"""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import firestore

# ── Modalidades ──────────────────────────────────────────────────────────────

_MODALITIES = [
    {"slug": "jiu-jitsu", "name": "Jiu-Jitsu"},
    {"slug": "muay-thai", "name": "Muay Thai"},
    {"slug": "capoeira", "name": "Capoeira"},
    {"slug": "mma", "name": "MMA"},
]

# ── Turmas ───────────────────────────────────────────────────────────────────

_CLASSES = [
    # ─── Muay Thai ────────────────────────────────────────────────────
    {
        "id": "muay-thai-kids",
        "name": "Kids e Juvenil",
        "modalitySlug": "muay-thai",
        "schedule": [
            {"day": "mon", "startTime": "16:00", "endTime": "17:00"},
            {"day": "wed", "startTime": "16:00", "endTime": "17:00"},
        ],
        "teacherName": None,
        "location": None,
        "ageRange": {"min": 5, "max": 17},
    },
    # ─── Capoeira ─────────────────────────────────────────────────────
    {
        "id": "capoeira-todas-idades",
        "name": "Todas as Idades",
        "modalitySlug": "capoeira",
        "schedule": [
            {"day": "mon", "startTime": "17:30", "endTime": "18:30"},
        ],
        "teacherName": None,
        "location": None,
        "ageRange": {"min": 5, "max": None},
    },
    # ─── Jiu-Jitsu ────────────────────────────────────────────────────
    {
        "id": "jj-kids-matutino",
        "name": "Juvenil e Kids — Matutino",
        "modalitySlug": "jiu-jitsu",
        "schedule": [
            {"day": "tue", "startTime": "09:00", "endTime": "10:00"},
            {"day": "thu", "startTime": "09:00", "endTime": "10:00"},
        ],
        "teacherName": None,
        "location": None,
        "ageRange": {"min": 5, "max": 17},
    },
    {
        "id": "jj-kids-vespertino",
        "name": "Juvenil e Kids — Vespertino",
        "modalitySlug": "jiu-jitsu",
        "schedule": [
            {"day": "tue", "startTime": "16:00", "endTime": "17:00"},
            {"day": "thu", "startTime": "16:00", "endTime": "17:00"},
        ],
        "teacherName": None,
        "location": None,
        "ageRange": {"min": 5, "max": 17},
    },
    {
        "id": "jj-adultos",
        "name": "Adultos",
        "modalitySlug": "jiu-jitsu",
        "schedule": [
            {"day": "thu", "startTime": "19:30", "endTime": "20:30"},
        ],
        "teacherName": None,
        "location": None,
        "ageRange": {"min": 16, "max": None},
    },
    # ─── MMA ──────────────────────────────────────────────────────────
    {
        "id": "mma-cardio",
        "name": "Cardio e Isometria",
        "modalitySlug": "mma",
        "schedule": [
            {"day": "fri", "startTime": "19:00", "endTime": "20:30"},
        ],
        "teacherName": None,
        "location": None,
        "ageRange": {"min": 16, "max": None},
    },
]


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    db = firestore.client()
    now = datetime.now(timezone.utc).isoformat()

    # ── Delete existing modalities ───────────────────────────────────
    mod_col = db.collection("modalities")
    existing_mods = mod_col.where("projectId", "==", project_id).stream()
    del_m = 0
    for doc in existing_mods:
        doc.reference.delete()
        del_m += 1
    print(f"Deleted {del_m} existing modalities.")

    # ── Delete existing classes ──────────────────────────────────────
    cls_col = db.collection("classes")
    existing_cls = cls_col.where("projectId", "==", project_id).stream()
    del_c = 0
    for doc in existing_cls:
        doc.reference.delete()
        del_c += 1
    print(f"Deleted {del_c} existing classes.")

    # ── Seed modalities ──────────────────────────────────────────────
    print(f"\nSeeding {len(_MODALITIES)} modalities...")
    modality_id_map: dict[str, str] = {}
    for mod in _MODALITIES:
        doc_id = f"{project_id}_{mod['slug']}"
        mod_col.document(doc_id).set({
            "projectId": project_id,
            "name": mod["name"],
            "slug": mod["slug"],
            "iconUrl": None,
            "active": True,
            "createdAt": now,
        })
        modality_id_map[mod["slug"]] = doc_id
        print(f"  ✓ {doc_id} ({mod['name']})")

    # ── Seed classes ─────────────────────────────────────────────────
    print(f"\nSeeding {len(_CLASSES)} classes...")
    for cls in _CLASSES:
        doc_id = f"{project_id}_{cls['id']}"
        modality_id = modality_id_map[cls["modalitySlug"]]
        cls_col.document(doc_id).set({
            "projectId": project_id,
            "name": cls["name"],
            "modalityId": modality_id,
            "schedule": cls["schedule"],
            "teacherId": None,
            "teacherName": cls["teacherName"],
            "location": cls["location"],
            "ageRange": cls["ageRange"],
            "iconUrl": None,
            "active": True,
            "createdAt": now,
        })
        print(f"  ✓ {doc_id}")

    print(f"\nDone: {len(_MODALITIES)} modalities + {len(_CLASSES)} classes seeded.")


if __name__ == "__main__":
    run()
