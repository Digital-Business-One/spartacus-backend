"""
Testes de integração — Script de migração one-time (Task 5).

Verifica que:
  1. dry-run reporta contagem mas NÃO altera status.
  2. Ambos os estados stuck migram para `approved`.
  3. Documento de anamnese existente é preservado intacto.
  4. Segunda execução migra 0 contas (idempotente).

Usa as fixtures canônicas de tests/integration/conftest.py
(app_client, restore_firestore autouse).
"""
from datetime import datetime, timezone

from firebase_admin import firestore

from scripts.migrate_collapse_onboarding import migrate

_PROJECT_ID = "spartacus-artes-marciais"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_user(
    db,
    uid: str,
    *,
    roles: list[str],
    status: str,
) -> str:
    """Insert a user + membership into the emulated Firestore. Returns uid."""
    now = _now()
    db.collection("users").document(uid).set(
        {
            "uid": uid,
            "name": f"User {uid}",
            "email": f"{uid}@test.com",
            "approvalStatus": status,
            "isDependent": False,
            "createdAt": now,
            "updatedAt": now,
        }
    )
    db.collection("memberships").document(f"{_PROJECT_ID}_{uid}").set(
        {
            "userId": uid,
            "projectId": _PROJECT_ID,
            "roles": roles,
            "status": "pending",
            "joinedAt": now,
        }
    )
    return uid


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestMigrateCollapseOnboarding:
    def test_dry_run_reports_but_does_not_change(self, app_client):
        """dry-run conta corretamente mas não altera approvalStatus."""
        db = firestore.client()
        uid = _seed_user(
            db, "migrate-dry-001", roles=["student"], status="waiting_medical_history"
        )

        report = migrate(db, _PROJECT_ID, dry_run=True)

        assert report["migrated"] == 1
        user = db.collection("users").document(uid).get().to_dict()
        assert user["approvalStatus"] == "waiting_medical_history"  # unchanged

    def test_migrates_both_stuck_states_to_approved(self, app_client):
        """Ambos os estados stuck são migrados para approved."""
        db = firestore.client()
        uid1 = _seed_user(
            db,
            "migrate-both-001",
            roles=["student"],
            status="waiting_medical_history",
        )
        uid2 = _seed_user(
            db,
            "migrate-both-002",
            roles=["student"],
            status="pending_medical_history_approval",
        )

        report = migrate(db, _PROJECT_ID, dry_run=False)

        assert report["migrated"] == 2
        u1 = db.collection("users").document(uid1).get().to_dict()
        u2 = db.collection("users").document(uid2).get().to_dict()
        assert u1["approvalStatus"] == "approved"
        assert u2["approvalStatus"] == "approved"

    def test_preserves_existing_anamnese(self, app_client):
        """Documento de anamnese existente é preservado sem alterações."""
        db = firestore.client()
        uid = _seed_user(
            db,
            "migrate-mh-001",
            roles=["student"],
            status="pending_medical_history_approval",
        )
        mh_doc_id = f"{_PROJECT_ID}_{uid}"
        db.collection("medical_history").document(mh_doc_id).set(
            {
                "projectId": _PROJECT_ID,
                "userId": uid,
                "status": "pending_approval",
                "goals": ["fitness"],
            }
        )

        migrate(db, _PROJECT_ID, dry_run=False)

        mh = db.collection("medical_history").document(mh_doc_id).get().to_dict()
        assert mh["status"] == "pending_approval"  # untouched
        assert mh["goals"] == ["fitness"]

    def test_idempotent(self, app_client):
        """Segunda execução migra 0 contas."""
        db = firestore.client()
        _seed_user(
            db,
            "migrate-idem-001",
            roles=["student"],
            status="waiting_medical_history",
        )

        migrate(db, _PROJECT_ID, dry_run=False)
        report = migrate(db, _PROJECT_ID, dry_run=False)

        assert report["migrated"] == 0
