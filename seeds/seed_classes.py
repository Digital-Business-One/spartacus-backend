#!/usr/bin/env python3
"""Seed classes (turmas) for the ROOT Spartacus project into Firestore.

Idempotent: uses merge=True, so re-running does not duplicate documents.
iconUrl is left null — upload modality icons to Firebase Storage and update manually.
Expected Storage paths: modalities/{jiu-jitsu,capoeira,muay-thai,mma,general}.png

Usage (local dev with emulators running):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \\
    FIREBASE_AUTH_EMULATOR_HOST=localhost:9099 \\
    FIREBASE_STORAGE_EMULATOR_HOST=localhost:9199 \\
    GOOGLE_CLOUD_PROJECT=demo-spartacus \\
    ROOT_PROJECT_ID=demo-spartacus \\
    uv run python seeds/seed_classes.py
"""
import os
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import firestore

_CLASSES: list[dict] = [
    # ─── Kids & Youth ─────────────────────────────────────────────────────────
    {
        "id": "jiu-jitsu-kids",
        "name": "Jiu-Jitsu Kids",
        "modality": "Jiu-Jitsu",
        "weeklySchedule": {
            "days": ["mon", "wed", "fri"],
            "startTime": "08:00",
            "endTime": "09:00",
        },
        "teacherName": "Istanrley",
        "ageRange": {"min": 5, "max": 12},
    },
    {
        "id": "capoeira-kids",
        "name": "Capoeira Kids",
        "modality": "Capoeira",
        "weeklySchedule": {
            "days": ["tue", "thu"],
            "startTime": "09:00",
            "endTime": "10:00",
        },
        "teacherName": "Mestre João",
        "ageRange": {"min": 6, "max": 14},
    },
    {
        "id": "mma-youth",
        "name": "MMA Youth",
        "modality": "MMA",
        "weeklySchedule": {
            "days": ["mon", "wed", "fri"],
            "startTime": "14:00",
            "endTime": "15:00",
        },
        "teacherName": None,
        "ageRange": {"min": 13, "max": 17},
    },
    {
        "id": "kids-youth-general-morning",
        "name": "Kids & Youth General",
        "modality": "General",
        "weeklySchedule": {
            "days": ["tue", "wed", "thu", "fri"],
            "startTime": "09:00",
            "endTime": "10:00",
        },
        "teacherName": None,
        "ageRange": {"min": 5, "max": 17},
    },
    {
        "id": "kids-youth-general-afternoon",
        "name": "Kids & Youth General",
        "modality": "General",
        "weeklySchedule": {
            "days": ["tue", "wed", "thu", "fri"],
            "startTime": "16:00",
            "endTime": "17:00",
        },
        "teacherName": None,
        "ageRange": {"min": 5, "max": 17},
    },
    # ─── Adults ───────────────────────────────────────────────────────────────
    {
        "id": "jiu-jitsu-adults",
        "name": "Jiu-Jitsu Adults",
        "modality": "Jiu-Jitsu",
        "weeklySchedule": {
            "days": ["mon", "wed"],
            "startTime": "16:00",
            "endTime": "17:00",
        },
        "teacherName": "Istanrley",
        "ageRange": {"min": 16, "max": None},
    },
    {
        "id": "muay-thai",
        "name": "Muay Thai",
        "modality": "Muay Thai",
        "weeklySchedule": {
            "days": ["mon", "wed", "fri"],
            "startTime": "20:00",
            "endTime": "21:00",
        },
        "teacherName": None,
        "ageRange": {"min": 16, "max": None},
    },
    {
        "id": "capoeira-adults",
        "name": "Capoeira",
        "modality": "Capoeira",
        "weeklySchedule": {
            "days": ["mon"],
            "startTime": "17:00",
            "endTime": "18:00",
        },
        "teacherName": "Mestre João",
        "ageRange": {"min": 16, "max": None},
    },
    {
        "id": "mma-submission",
        "name": "MMA / Submission Wrestling",
        "modality": "MMA",
        "weeklySchedule": {
            "days": ["fri"],
            "startTime": "19:00",
            "endTime": "20:00",
        },
        "teacherName": None,
        "ageRange": {"min": 16, "max": None},
    },
    {
        "id": "adults-general",
        "name": "Adults General",
        "modality": "General",
        "weeklySchedule": {
            "days": ["fri"],
            "startTime": "19:00",
            "endTime": "20:00",
        },
        "teacherName": None,
        "ageRange": {"min": 18, "max": None},
    },
]


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "demo-spartacus")

    db = firestore.client()
    now = datetime.now(timezone.utc).isoformat()
    collection = db.collection("classes")

    print(f"Seeding classes for project '{project_id}'...")
    for cls in _CLASSES:
        doc_id = f"{project_id}_{cls['id']}"
        collection.document(doc_id).set(
            {
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
            },
            merge=True,
        )
        print(f"  ✓ {doc_id}")

    print(f"\n{len(_CLASSES)} classes seeded.")
    print("Reminder: iconUrl is null — upload icons to Storage when ready.")
    print("  Paths: modalities/<slug>.png")
    print("  Slugs: jiu-jitsu, capoeira, muay-thai, mma, general")


if __name__ == "__main__":
    run()
