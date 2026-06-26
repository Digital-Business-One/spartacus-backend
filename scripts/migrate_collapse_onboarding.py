"""One-time migration: collapse onboarding to a single approval.

Moves accounts stuck in waiting_medical_history / pending_medical_history_approval
to 'approved', activating membership + syncing custom claims. Medical history
documents are left untouched (data preservation). Idempotent.

Usage:
    uv run python -m scripts.migrate_collapse_onboarding --project <id> [--apply]
"""
from datetime import datetime, timezone

from app.services.account_history_service import AccountHistoryService
from app.services.account_service import AccountService

STUCK = {"waiting_medical_history", "pending_medical_history_approval"}

_MEMBERSHIPS = "memberships"
_USERS = "users"


def _list_project_uids(db, project_id: str) -> list[str]:
    """Return all user UIDs that have a membership in this project."""
    uids: list[str] = []
    for m in db.collection(_MEMBERSHIPS).where("projectId", "==", project_id).stream():
        uid = m.to_dict().get("userId")
        if uid:
            uids.append(uid)
    return list(set(uids))


def migrate(db, project_id: str, dry_run: bool = True) -> dict:
    """Migrate stuck accounts to 'approved'.

    Args:
        db: Firestore client instance.
        project_id: The project tenant to migrate.
        dry_run: If True, count but do not apply changes (default).

    Returns:
        {"migrated": n} where n is the count of affected accounts.
    """
    svc = AccountService()
    history_svc = AccountHistoryService()
    migrated = 0

    uids = _list_project_uids(db, project_id)
    for uid in uids:
        user_doc = db.collection(_USERS).document(uid).get()
        if not user_doc.exists:
            continue
        data = user_doc.to_dict()
        if data.get("approvalStatus") not in STUCK:
            continue
        migrated += 1
        if dry_run:
            continue
        now = datetime.now(timezone.utc).isoformat()
        user_doc.reference.update(
            {
                "approvalStatus": "approved",
                "approvedAt": now,
                "approvedBy": "system_migration",
                "updatedAt": now,
                "lastUpdatedBy": "system_migration",
            }
        )
        svc._activate_membership(db, project_id, uid)
        history_svc.record(
            uid=uid,
            project_id=project_id,
            event_type="account",
            event_subtype="onboarding_migration",
            actor_uid="system_migration",
            actor_name="Migração",
            actor_roles=[],
            description="Conta migrada para approved (onboarding de aprovação única)",
            db=db,
        )

    return {"migrated": migrated}


def _main() -> None:  # pragma: no cover
    import argparse

    import firebase_admin
    from firebase_admin import firestore

    if not firebase_admin._apps:
        firebase_admin.initialize_app()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Project ID to migrate")
    parser.add_argument(
        "--apply", action="store_true", help="Execute migration (default is dry-run)"
    )
    args = parser.parse_args()
    db = firestore.client()
    report = migrate(db, args.project, dry_run=not args.apply)
    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] project={args.project} migrated={report['migrated']}")


if __name__ == "__main__":  # pragma: no cover
    _main()
