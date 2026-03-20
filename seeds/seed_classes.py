#!/usr/bin/env python3
"""Seed classes (turmas) for the ROOT Spartacus project into Firestore.

Destructive: deletes ALL existing classes for the project before inserting.
Data source: docs/images/turmas.jpeg (official schedule poster).

Usage (local dev with emulators running):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    FIREBASE_AUTH_EMULATOR_HOST=localhost:9099 \
    FIREBASE_STORAGE_EMULATOR_HOST=localhost:9199 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_classes.py

Usage (production — requires ADC):
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_classes.py
"""
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import firestore

_CLASSES: list[dict] = [
    # ─── Muay Thai ────────────────────────────────────────────────────────────
    {
        "id": "muay-thai-kids",
        "name": "Muay Thai Kids e Juvenil",
        "modality": "Muay Thai",
        "weeklySchedule": {
            "days": ["mon", "wed"],
            "startTime": "16:00",
            "endTime": "17:00",
        },
        "teacherName": None,
        "ageRange": {"min": 5, "max": 17},
    },
    # ─── Capoeira ─────────────────────────────────────────────────────────────
    {
        "id": "capoeira",
        "name": "Capoeira — Todas as Idades",
        "modality": "Capoeira",
        "weeklySchedule": {
            "days": ["mon"],
            "startTime": "17:30",
            "endTime": "18:30",
        },
        "teacherName": None,
        "ageRange": {"min": 5, "max": None},
    },
    # ─── Jiu-Jitsu ────────────────────────────────────────────────────────────
    {
        "id": "jiu-jitsu-kids-matutino",
        "name": "Jiu-Jitsu Juvenil e Kids — Matutino",
        "modality": "Jiu-Jitsu",
        "weeklySchedule": {
            "days": ["tue", "thu"],
            "startTime": "09:00",
            "endTime": "10:00",
        },
        "teacherName": None,
        "ageRange": {"min": 5, "max": 17},
    },
    {
        "id": "jiu-jitsu-kids-vespertino",
        "name": "Jiu-Jitsu Juvenil e Kids — Vespertino",
        "modality": "Jiu-Jitsu",
        "weeklySchedule": {
            "days": ["tue", "thu"],
            "startTime": "16:00",
            "endTime": "17:00",
        },
        "teacherName": None,
        "ageRange": {"min": 5, "max": 17},
    },
    {
        "id": "jiu-jitsu-adultos",
        "name": "Jiu-Jitsu Adultos",
        "modality": "Jiu-Jitsu",
        "weeklySchedule": {
            "days": ["thu"],
            "startTime": "19:30",
            "endTime": "20:30",
        },
        "teacherName": None,
        "ageRange": {"min": 16, "max": None},
    },
    # ─── MMA ──────────────────────────────────────────────────────────────────
    {
        "id": "mma",
        "name": "MMA — Cardio e Isometria",
        "modality": "MMA",
        "weeklySchedule": {
            "days": ["fri"],
            "startTime": "19:00",
            "endTime": "20:30",
        },
        "teacherName": None,
        "ageRange": {"min": 16, "max": None},
    },
]


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")

    db = firestore.client()
    now = datetime.now(timezone.utc).isoformat()
    collection = db.collection("classes")

    # ── Delete existing classes for this project ──────────────────────────────
    existing = collection.where("projectId", "==", project_id).stream()
    deleted = 0
    for doc in existing:
        doc.reference.delete()
        deleted += 1
    print(f"Deleted {deleted} existing classes for project '{project_id}'.")

    # ── Insert new classes ────────────────────────────────────────────────────
    print(f"\nSeeding {len(_CLASSES)} classes...")
    for cls in _CLASSES:
        doc_id = f"{project_id}_{cls['id']}"
        collection.document(doc_id).set({
            "projectId": project_id,
            "name": cls["name"],
            "modality": cls["modality"],
            "weeklySchedule": cls["weeklySchedule"],
            "teacherId": None,
            "teacherName": cls["teacherName"],
            "ageRange": cls["ageRange"],
            "iconUrl": None,
            "active": True,
            "createdAt": now,
        })
        print(f"  ✓ {doc_id}")

    print(f"\n{len(_CLASSES)} classes seeded.")


if __name__ == "__main__":
    run()
