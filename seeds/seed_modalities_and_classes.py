#!/usr/bin/env python3
"""Migrate classes to the new modalities + turmas model (RFC-08).

NON-DESTRUCTIVE:
- Creates `modalities` collection if not exists (idempotent)
- Updates existing `classes` documents IN PLACE:
  - Adds `modalityId` (resolved from `modality` text field)
  - Converts `weeklySchedule` → `schedule[]` array
  - Adds `location` field (null)
  - Preserves document IDs, names, and all other fields
- Does NOT delete any documents
- Does NOT change document IDs (classIds in users stay valid)
- Safe to run multiple times (idempotent)

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

# ── Modalidades oficiais ─────────────────────────────────────────────────────

_MODALITIES = [
    {"slug": "jiu-jitsu", "name": "Jiu-Jitsu"},
    {"slug": "muay-thai", "name": "Muay Thai"},
    {"slug": "capoeira", "name": "Capoeira"},
    {"slug": "mma", "name": "MMA"},
]

# Map from free-text modality (as stored in old classes) to slug
_MODALITY_TEXT_TO_SLUG: dict[str, str] = {
    "Jiu-Jitsu": "jiu-jitsu",
    "jiu-jitsu": "jiu-jitsu",
    "Muay Thai": "muay-thai",
    "muay-thai": "muay-thai",
    "Capoeira": "capoeira",
    "capoeira": "capoeira",
    "MMA": "mma",
    "mma": "mma",
}


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    db = firestore.client()
    now = datetime.now(timezone.utc).isoformat()

    # ── Step 1: Ensure modalities exist (idempotent) ─────────────────
    print("Step 1: Ensuring modalities exist...")
    mod_col = db.collection("modalities")
    modality_id_map: dict[str, str] = {}  # slug → doc_id

    for mod in _MODALITIES:
        doc_id = f"{project_id}_{mod['slug']}"
        modality_id_map[mod["slug"]] = doc_id

        doc = mod_col.document(doc_id).get()
        if doc.exists:
            print(f"  ✓ {doc_id} already exists — skipped")
        else:
            mod_col.document(doc_id).set({
                "projectId": project_id,
                "name": mod["name"],
                "slug": mod["slug"],
                "iconUrl": None,
                "active": True,
                "createdAt": now,
            })
            print(f"  + {doc_id} created ({mod['name']})")

    # ── Step 2: Migrate existing classes in place ────────────────────
    print("\nStep 2: Migrating existing classes...")
    cls_col = db.collection("classes")
    all_classes = list(
        cls_col.where("projectId", "==", project_id).stream()
    )

    migrated = 0
    skipped = 0
    errors = 0

    for doc in all_classes:
        data = doc.to_dict()
        doc_id = doc.id
        updates: dict = {}

        # 2a: Add modalityId if missing
        if not data.get("modalityId"):
            modality_text = data.get("modality", "")
            slug = _MODALITY_TEXT_TO_SLUG.get(modality_text)
            if slug and slug in modality_id_map:
                updates["modalityId"] = modality_id_map[slug]
            else:
                print(
                    f"  ⚠ {doc_id}: unknown modality "
                    f"'{modality_text}' — skipped modalityId"
                )
                errors += 1

        # 2b: Convert weeklySchedule → schedule[] if needed
        if not data.get("schedule") and data.get("weeklySchedule"):
            ws = data["weeklySchedule"]
            days = ws.get("days", [])
            start = ws.get("startTime", "")
            end = ws.get("endTime", "")
            updates["schedule"] = [
                {"day": d, "startTime": start, "endTime": end}
                for d in days
            ]

        # 2c: Add location if missing
        if "location" not in data:
            updates["location"] = None

        # Apply updates
        if updates:
            doc.reference.update(updates)
            migrated += 1
            print(f"  ✓ {doc_id} — migrated ({list(updates.keys())})")
        else:
            skipped += 1
            print(f"  · {doc_id} — already migrated, skipped")

    print(f"\nDone: {migrated} migrated, {skipped} skipped, {errors} warnings")
    print(f"Modalities: {len(_MODALITIES)}, Classes: {len(all_classes)}")


if __name__ == "__main__":
    run()
