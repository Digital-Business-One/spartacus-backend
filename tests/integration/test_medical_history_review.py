"""
Testes de integração — Anamnese: submit (Task 3) + review (Task 4).

Task 3 verifica que:
  1. Submit da anamnese escreve o documento com status `pending_approval`.
  2. Submit NÃO altera o `approvalStatus` do usuário na coleção `users`.
  3. Submit retorna `event=None` (sem transição de conta).

Task 4 verifica que:
  1. review approve define status=approved + reviewer fields.
  2. review request_revision define status=needs_revision + review_note.
  3. review em doc inexistente levanta LookupError.
  4. resubmit após revisão retorna status=pending_approval.
  5. PATCH /medical-history/{uid}/review retorna 403 para papel não-staff.

Usa as fixtures canônicas de tests/integration/conftest.py
(app_client, restore_firestore autouse).
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from firebase_admin import firestore

from app.models.medical_history import (
    HealthBehaviorIn,
    MedicalHistoryIn,
    MedicalHistoryOut,
    MedicalHistoryRequest,
    SymptomsIn,
)
from app.services.medical_history_service import MedicalHistoryService

_PROJECT_ID = "spartacus-artes-marciais"


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


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestMedicalHistorySubmit:
    def test_submit_does_not_change_account_status(self, app_client):
        """Submit da anamnese não altera approvalStatus do usuário."""
        db = firestore.client()
        uid = "student-anamnese-001"

        _seed_user(db, uid, roles=["student"], status="approved")

        req = _build_medical_history_request()
        svc = MedicalHistoryService()
        out, event = svc.submit(_PROJECT_ID, uid, req, actor_uid=uid)

        # out deve ser MedicalHistoryOut com status pending_approval
        assert isinstance(out, MedicalHistoryOut)
        assert out.status == "pending_approval"

        # event deve ser None — nenhuma transição de conta foi disparada
        assert event is None

        # approvalStatus do usuário deve permanecer inalterado
        user = db.collection("users").document(uid).get().to_dict()
        assert user["approvalStatus"] == "approved"

    def test_submit_writes_review_note_none(self, app_client):
        """Submit inicializa reviewNote como None no documento."""
        db = firestore.client()
        uid = "student-anamnese-002"

        _seed_user(db, uid, roles=["student"], status="approved")

        req = _build_medical_history_request()
        svc = MedicalHistoryService()
        out, _ = svc.submit(_PROJECT_ID, uid, req, actor_uid=uid)

        # Campo review_note deve ser None no out
        assert out.review_note is None

        # Documento no Firestore deve ter reviewNote=None
        doc_id = f"{_PROJECT_ID}_{uid}"
        doc = db.collection("medical_history").document(doc_id).get().to_dict()
        assert doc.get("reviewNote") is None

    def test_get_returns_review_note(self, app_client):
        """get() popula review_note a partir do Firestore."""
        db = firestore.client()
        uid = "student-anamnese-003"

        _seed_user(db, uid, roles=["student"], status="approved")

        # Gravar diretamente com um reviewNote definido
        doc_id = f"{_PROJECT_ID}_{uid}"
        req = _build_medical_history_request()
        svc = MedicalHistoryService()
        svc.submit(_PROJECT_ID, uid, req, actor_uid=uid)

        # Atualizar reviewNote manualmente no Firestore
        db.collection("medical_history").document(doc_id).update(
            {"reviewNote": "Necessita reavaliação médica"}
        )

        result = svc.get(_PROJECT_ID, uid)
        assert result is not None
        assert result.review_note == "Necessita reavaliação médica"


# ── Task 4: Review endpoint (service-level) ────────────────────────────────────

_AUTH = "Bearer tok"


def _claims(project_id: str, uid: str, roles: list[str]) -> dict:
    return {
        "uid": uid,
        "email": f"{uid}@test.com",
        "projects": {project_id: roles},
    }


def _headers(project_id: str) -> dict:
    return {"Authorization": _AUTH, "X-Project-Id": project_id}


class TestMedicalHistoryReview:
    def test_review_approve_sets_status_and_reviewer(self, app_client):
        """review approve define status=approved + reviewed_by + reviewed_at."""
        db = firestore.client()
        uid = "student-review-001"
        _seed_user(db, uid, roles=["student"], status="approved")

        svc = MedicalHistoryService()
        svc.submit(_PROJECT_ID, uid, _build_medical_history_request(), actor_uid=uid)
        out = svc.review(_PROJECT_ID, uid, "approve", "", reviewer_uid="staff1")

        assert out.status == "approved"
        assert out.reviewed_by == "staff1"
        assert out.reviewed_at is not None

    def test_review_request_revision_sets_status_and_note(self, app_client):
        """review request_revision define status=needs_revision + review_note."""
        db = firestore.client()
        uid = "student-review-002"
        _seed_user(db, uid, roles=["student"], status="approved")

        svc = MedicalHistoryService()
        svc.submit(_PROJECT_ID, uid, _build_medical_history_request(), actor_uid=uid)
        out = svc.review(
            _PROJECT_ID,
            uid,
            "request_revision",
            "Faltou medicação",
            reviewer_uid="staff1",
        )

        assert out.status == "needs_revision"
        assert out.review_note == "Faltou medicação"

    def test_review_missing_doc_raises(self, app_client):
        """review em doc inexistente levanta LookupError."""
        with pytest.raises(LookupError):
            MedicalHistoryService().review(
                _PROJECT_ID, "ghost", "approve", "", reviewer_uid="staff1"
            )

    def test_resubmit_after_revision_returns_to_pending(self, app_client):
        """resubmit após needs_revision retorna status=pending_approval."""
        db = firestore.client()
        uid = "student-review-003"
        _seed_user(db, uid, roles=["student"], status="approved")

        svc = MedicalHistoryService()
        svc.submit(_PROJECT_ID, uid, _build_medical_history_request(), actor_uid=uid)
        svc.review(
            _PROJECT_ID, uid, "request_revision", "ajuste", reviewer_uid="staff1"
        )
        out, _ = svc.submit(
            _PROJECT_ID, uid, _build_medical_history_request(), actor_uid=uid
        )

        assert out.status == "pending_approval"

    def test_non_staff_gets_403_on_review(self, app_client):
        """PATCH /medical-history/{uid}/review retorna 403 para papel não-staff."""
        db = firestore.client()
        uid = "student-review-403"
        _seed_user(db, uid, roles=["student"], status="approved")

        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_PROJECT_ID, uid, ["student"]),
        ):
            response = app_client.patch(
                f"/medical-history/{uid}/review",
                json={"action": "approve", "note": ""},
                headers=_headers(_PROJECT_ID),
            )

        assert response.status_code == 403
