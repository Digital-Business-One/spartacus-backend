"""
Testes de integração — Fluxo de aprovação colapsado (Task 2).

Verifica que:
  1. Um student aprovado vai direto para `approved` com membership ativa.
  2. Um guardian aprovado também leva seus dependentes direto para `approved`
     com membership ativa (sem passar por waiting_medical_history).
"""
from datetime import datetime, timezone

from firebase_admin import firestore

from app.services.account_service import AccountService

_PROJECT_ID = "spartacus-artes-marciais"
_ACTOR_UID = "staff-actor-001"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_user(
    db,
    uid: str,
    *,
    roles: list[str],
    status: str = "pending_approval",
    guardian_uid: str | None = None,
    is_dependent: bool = False,
) -> None:
    """Insere um usuário + membership no Firestore emulado."""
    now = _now()
    db.collection("users").document(uid).set(
        {
            "uid": uid,
            "name": f"User {uid}",
            "email": f"{uid}@test.com",
            "approvalStatus": status,
            "isDependent": is_dependent,
            **({"guardianUid": guardian_uid} if guardian_uid else {}),
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


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCollapseApproval:
    def test_approve_student_goes_straight_to_approved(self, app_client):
        """Student em pending_approval vai direto para approved com membership ativa."""
        db = firestore.client()
        uid = "student-solo-001"

        _seed_user(db, uid, roles=["student"], status="pending_approval")

        svc = AccountService()
        resp, _ = svc.execute_transition(
            _PROJECT_ID, uid, "approve", actor_uid=_ACTOR_UID
        )

        assert resp.new_status == "approved"

        user = db.collection("users").document(uid).get().to_dict()
        assert user["approvalStatus"] == "approved"
        assert user.get("approvedBy") == _ACTOR_UID

        mem = (
            db.collection("memberships")
            .document(f"{_PROJECT_ID}_{uid}")
            .get()
            .to_dict()
        )
        assert mem["status"] == "active"

    def test_guardian_approval_approves_student_dependents_directly(
        self, app_client
    ):
        """Guardian aprovado leva dependente student direto para approved."""
        db = firestore.client()
        guardian_uid = "guardian-001"
        dep_uid = "student-dep-001"

        _seed_user(db, guardian_uid, roles=["guardian"], status="pending_approval")
        _seed_user(
            db,
            dep_uid,
            roles=["student"],
            status="pending_approval",
            guardian_uid=guardian_uid,
            is_dependent=True,
        )

        svc = AccountService()
        resp, _ = svc.execute_transition(
            _PROJECT_ID, guardian_uid, "approve", actor_uid=_ACTOR_UID
        )

        assert resp.new_status == "approved"

        # Guardian membership deve estar ativa
        guardian_mem = (
            db.collection("memberships")
            .document(f"{_PROJECT_ID}_{guardian_uid}")
            .get()
            .to_dict()
        )
        assert guardian_mem["status"] == "active"

        # Dependente deve ir direto para approved (não waiting_medical_history)
        dep_user = db.collection("users").document(dep_uid).get().to_dict()
        assert dep_user["approvalStatus"] == "approved", (
            f"Expected 'approved' but got '{dep_user['approvalStatus']}' — "
            "dependent should not go to waiting_medical_history"
        )
        assert dep_user.get("approvedBy") == _ACTOR_UID

        dep_mem = (
            db.collection("memberships")
            .document(f"{_PROJECT_ID}_{dep_uid}")
            .get()
            .to_dict()
        )
        assert dep_mem["status"] == "active"

    def test_guardian_with_no_dependents_approved_normally(self, app_client):
        """Guardian sem dependentes é aprovado normalmente sem erros."""
        db = firestore.client()
        uid = "guardian-no-deps-001"

        _seed_user(db, uid, roles=["guardian"], status="pending_approval")

        svc = AccountService()
        resp, _ = svc.execute_transition(
            _PROJECT_ID, uid, "approve", actor_uid=_ACTOR_UID
        )

        assert resp.new_status == "approved"

        mem = (
            db.collection("memberships")
            .document(f"{_PROJECT_ID}_{uid}")
            .get()
            .to_dict()
        )
        assert mem["status"] == "active"
