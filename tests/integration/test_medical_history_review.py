"""
Testes de integração — Anamnese não transiciona conta (Task 3).

Verifica que:
  1. Submit da anamnese escreve o documento com status `pending_approval`.
  2. Submit NÃO altera o `approvalStatus` do usuário na coleção `users`.
  3. Submit retorna `event=None` (sem transição de conta).

Usa as fixtures canônicas de tests/integration/conftest.py
(app_client, restore_firestore autouse).
"""
from datetime import datetime, timezone

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
