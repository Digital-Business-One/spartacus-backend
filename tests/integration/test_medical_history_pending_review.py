"""
Integration tests — GET /medical-history/pending-review (Task 1).

Verifies:
  1. list_pending_review returns only pending_approval docs
     (not approved/needs_revision).
  2. Non-staff (student) gets 403 on the HTTP endpoint.
  3. Staff (owner) gets 200 with the seeded pending uid + name.

Uses canonical fixtures from tests/integration/conftest.py
(app_client, restore_firestore autouse).
"""
from datetime import datetime, timezone
from unittest.mock import patch

from firebase_admin import firestore

from app.models.medical_history import (
    HealthBehaviorIn,
    MedicalHistoryIn,
    MedicalHistoryRequest,
    SymptomsIn,
)
from app.services.medical_history_service import MedicalHistoryService

_PROJECT_ID = "spartacus-artes-marciais"
_AUTH = "Bearer tok"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_medical_history_request() -> MedicalHistoryRequest:
    """Minimal valid MedicalHistoryRequest — all symptoms 'never', goals=['fitness']."""
    symptoms = SymptomsIn(
        coughing_blood="never",
        abdominal_pain="never",
        leg_pain="never",
        arm_pain="never",
        back_neck_pain="never",
        chest_pain="never",
        joint_pain="never",
        shortness_of_breath="never",
        feeling_weak="never",
        dizziness="never",
        heart_palpitation="never",
    )
    medical_history = MedicalHistoryIn(symptoms=symptoms)
    health_behavior = HealthBehaviorIn()
    return MedicalHistoryRequest(
        medical_history=medical_history,
        health_behavior=health_behavior,
        goals=["fitness"],
    )


def _seed_user(db, uid: str, *, roles: list[str], status: str) -> None:
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
            "status": "active",
            "joinedAt": now,
        }
    )


def _claims(project_id: str, uid: str, roles: list[str]) -> dict:
    return {
        "uid": uid,
        "email": f"{uid}@test.com",
        "projects": {project_id: roles},
    }


def _headers(project_id: str) -> dict:
    return {"Authorization": _AUTH, "X-Project-Id": project_id}


# ── Service-level tests ────────────────────────────────────────────────────────


class TestListPendingReviewService:
    def test_lists_only_pending_approval(self, app_client):
        """list_pending_review returns pending_approval only (not approved/revision)."""
        db = firestore.client()
        uid_a = "pending-review-svc-001"
        uid_b = "pending-review-svc-002"
        uid_c = "pending-review-svc-003"

        _seed_user(db, uid_a, roles=["student"], status="approved")
        _seed_user(db, uid_b, roles=["student"], status="approved")
        _seed_user(db, uid_c, roles=["student"], status="approved")

        svc = MedicalHistoryService()
        req = _build_medical_history_request()

        # uid_a: pending_approval (via submit)
        svc.submit(_PROJECT_ID, uid_a, req, actor_uid=uid_a)

        # uid_b: approved (submit then review approve)
        svc.submit(_PROJECT_ID, uid_b, req, actor_uid=uid_b)
        svc.review(_PROJECT_ID, uid_b, "approve", "", reviewer_uid="staff1")

        # uid_c: needs_revision (submit then review request_revision)
        svc.submit(_PROJECT_ID, uid_c, req, actor_uid=uid_c)
        svc.review(
            _PROJECT_ID,
            uid_c,
            "request_revision",
            "Faltou info",
            reviewer_uid="staff1",
        )

        items = svc.list_pending_review(_PROJECT_ID)
        uids = {i.uid for i in items}

        assert uid_a in uids
        assert uid_b not in uids
        assert uid_c not in uids

    def test_list_pending_review_returns_name(self, app_client):
        """list_pending_review includes the user's name from the users collection."""
        db = firestore.client()
        uid = "pending-review-svc-004"

        _seed_user(db, uid, roles=["student"], status="approved")
        svc = MedicalHistoryService()
        svc.submit(_PROJECT_ID, uid, _build_medical_history_request(), actor_uid=uid)

        items = svc.list_pending_review(_PROJECT_ID)
        match = next((i for i in items if i.uid == uid), None)
        assert match is not None
        assert match.name == f"User {uid}"

    def test_list_pending_review_empty_when_none(self, app_client):
        """list_pending_review returns empty list when no pending_approval docs."""
        items = MedicalHistoryService().list_pending_review(_PROJECT_ID)
        assert items == []


# ── HTTP endpoint tests ────────────────────────────────────────────────────────


class TestListPendingReviewEndpoint:
    def test_non_staff_gets_403(self, app_client):
        """GET /medical-history/pending-review returns 403 for a student."""
        db = firestore.client()
        uid = "pending-review-http-403"
        _seed_user(db, uid, roles=["student"], status="approved")

        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, uid, ["student"]),
        ):
            response = app_client.get(
                "/medical-history/pending-review",
                headers=_headers(_PROJECT_ID),
            )

        assert response.status_code == 403

    def test_staff_owner_gets_200_with_pending_item(self, app_client):
        """GET /medical-history/pending-review returns 200 for owner with pending."""
        db = firestore.client()
        student_uid = "pending-review-http-student"
        staff_uid = "pending-review-http-owner"

        _seed_user(db, student_uid, roles=["student"], status="approved")
        _seed_user(db, staff_uid, roles=["owner"], status="approved")

        # Submit anamnese for student (pending_approval)
        MedicalHistoryService().submit(
            _PROJECT_ID,
            student_uid,
            _build_medical_history_request(),
            actor_uid=student_uid,
        )

        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, staff_uid, ["owner"]),
        ):
            response = app_client.get(
                "/medical-history/pending-review",
                headers=_headers(_PROJECT_ID),
            )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        uids = {item["uid"] for item in data["items"]}
        assert student_uid in uids
        names = {item["name"] for item in data["items"]}
        assert f"User {student_uid}" in names

    def test_staff_teacher_gets_200(self, app_client):
        """GET /medical-history/pending-review returns 200 for teacher role."""
        db = firestore.client()
        teacher_uid = "pending-review-http-teacher"
        _seed_user(db, teacher_uid, roles=["teacher"], status="approved")

        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, teacher_uid, ["teacher"]),
        ):
            response = app_client.get(
                "/medical-history/pending-review",
                headers=_headers(_PROJECT_ID),
            )

        assert response.status_code == 200
        assert "items" in response.json()
