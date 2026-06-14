#!/usr/bin/env python3
"""Seed the `graduation_systems` matrix (belt/degree rules per modality+age).

Idempotent: creates one doc per graduable modality (id "{projectId}_{slug}").
Defaults are sensible starting points and meant to be editable later — the
Muay Thai / Capoeira color sequences are group-dependent.

Usage (local dev):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_graduation_systems.py

Usage (production — requires ADC):
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/seed_graduation_systems.py
"""
import os

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import firestore

_COLLECTION = "graduation_systems"


def _belts(seq: list[tuple[str, str, str]], max_degree: int) -> list[dict]:
    """seq = [(slug, name, color), ...] → ordered belt dicts."""
    return [
        {
            "order": i,
            "slug": slug,
            "name": name,
            "color": color,
            "maxDegree": max_degree,
        }
        for i, (slug, name, color) in enumerate(seq)
    ]


# Jiu-Jitsu — age-banded, 4 degrees per belt (IBJJF-inspired, simplified;
# kids progress through more colors, adults through fewer). Editable later.
_JIU_KIDS = _belts([
    ("branca", "Branca", "#f2f2f2"),
    ("cinza", "Cinza", "#a8a8a8"),
    ("amarela", "Amarela", "#f0c83b"),
    ("laranja", "Laranja", "#ea8226"),
    ("verde", "Verde", "#2ea247"),
], max_degree=4)

_JIU_ADULT = _belts([
    ("branca", "Branca", "#f2f2f2"),
    ("azul", "Azul", "#1f4fa0"),
    ("roxa", "Roxa", "#5a2c89"),
    ("marrom", "Marrom", "#5a3515"),
    ("preta", "Preta", "#111111"),
], max_degree=4)

# Muay Thai — linear prajied/armband colors, belt-only (group-dependent).
_MUAY = _belts([
    ("branca", "Branca", "#f2f2f2"),
    ("amarela", "Amarela", "#f0c83b"),
    ("laranja", "Laranja", "#ea8226"),
    ("verde", "Verde", "#2ea247"),
    ("azul", "Azul", "#1f4fa0"),
    ("roxa", "Roxa", "#5a2c89"),
    ("marrom", "Marrom", "#5a3515"),
    ("vermelha", "Vermelha", "#b83228"),
    ("preta", "Preta", "#111111"),
], max_degree=0)

# Capoeira — linear corda colors, belt-only (group-dependent).
_CAPOEIRA = _belts([
    ("crua", "Crua", "#dac6a5"),
    ("amarela", "Amarela", "#f0c83b"),
    ("laranja", "Laranja", "#ea8226"),
    ("azul", "Azul", "#1f4fa0"),
    ("verde", "Verde", "#2ea247"),
    ("roxa", "Roxa", "#5a2c89"),
    ("marrom", "Marrom", "#5a3515"),
    ("vermelha", "Vermelha", "#b83228"),
    ("branca", "Branca", "#f2f2f2"),
], max_degree=0)


def _systems(project_id: str) -> list[dict]:
    return [
        {
            "projectId": project_id,
            "modalitySlug": "jiu-jitsu",
            "modalityName": "Jiu-Jitsu",
            "type": "age_banded",
            "ageBands": [
                {"minAge": 4, "maxAge": 15, "belts": _JIU_KIDS},
                {"minAge": 16, "maxAge": 200, "belts": _JIU_ADULT},
            ],
        },
        {
            "projectId": project_id,
            "modalitySlug": "muay-thai",
            "modalityName": "Muay Thai",
            "type": "linear",
            "ageBands": [{"minAge": 0, "maxAge": 200, "belts": _MUAY}],
        },
        {
            "projectId": project_id,
            "modalitySlug": "capoeira",
            "modalityName": "Capoeira",
            "type": "linear",
            "ageBands": [{"minAge": 0, "maxAge": 200, "belts": _CAPOEIRA}],
        },
    ]


def run() -> None:
    firebase_admin.initialize_app()
    project_id = os.getenv("ROOT_PROJECT_ID", "spartacus-artes-marciais")
    db = firestore.client()
    col = db.collection(_COLLECTION)

    print(f"Seeding graduation_systems for project {project_id}...")
    for sys in _systems(project_id):
        doc_id = f"{project_id}_{sys['modalitySlug']}"
        col.document(doc_id).set(sys)  # overwrite — keeps defaults current
        print(f"  ✓ {doc_id} ({sys['modalityName']}, {sys['type']})")
    print("Done.")


if __name__ == "__main__":
    run()
