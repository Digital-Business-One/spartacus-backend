#!/usr/bin/env python3
"""Migrate the legacy `donations` collection and `donationConfig` to support.

- Copies each `donations/{id}` → `support/{id}` adding supportType="donation"
  and itemLabel (resolved from the legacy config when possible). Idempotent:
  skips docs already present in `support`.
- Copies `projects/{id}.donationConfig` → `.supportConfig.donations`
  (+ default services) when supportConfig is absent.

Usage (local dev):
    FIRESTORE_EMULATOR_HOST=localhost:8080 \
    GOOGLE_CLOUD_PROJECT=spartacus-artes-marciais \
    ROOT_PROJECT_ID=spartacus-artes-marciais \
    uv run python seeds/migrate_donations_to_support.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import firestore

from app.models.support import (
    DEFAULT_DONATION_ITEMS,
    DEFAULT_SERVICE_ITEMS,
    DEFAULT_THANK_YOU,
)


def run() -> None:
    firebase_admin.initialize_app()
    db = firestore.client()

    # 1) Config migration (per project)
    print("Migrando configs de doação → supportConfig...")
    for proj in db.collection("projects").stream():
        data = proj.to_dict()
        if data.get("supportConfig"):
            continue
        legacy = data.get("donationConfig") or {}
        donations = legacy.get("items") or [
            {"code": k, "label": v, "active": True}
            for k, v in DEFAULT_DONATION_ITEMS.items()
        ]
        services = [
            {"code": k, "label": v, "active": True}
            for k, v in DEFAULT_SERVICE_ITEMS.items()
        ]
        proj.reference.update({
            "supportConfig": {
                "donations": donations,
                "services": services,
                "thankYouMessage": legacy.get("thankYouMessage", DEFAULT_THANK_YOU),
            }
        })
        print(f"  ✓ {proj.id}")

    # 2) Records migration
    print("Migrando registros donations → support...")
    support = db.collection("support")
    count = 0
    for d in db.collection("donations").stream():
        if support.document(d.id).get().exists:
            continue
        x = dict(d.to_dict())
        x.setdefault("supportType", "donation")
        x.setdefault("itemLabel", x.get("item"))
        support.document(d.id).set(x)
        count += 1
    print(f"  ✓ {count} registros copiados (idempotente). Done.")


if __name__ == "__main__":
    run()
