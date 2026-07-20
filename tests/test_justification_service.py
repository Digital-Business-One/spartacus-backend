"""TDD tests for Task B1 — justification status-transition service.

Covers the transition matrix (`justify`/`approve`/`reject`), origin-status
validation (409 on mismatch), the `rejectReason` requirement, and the
reenvio (resubmit-after-rejection) rule that archives the prior entry into
`justificationHistory[]`.

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
(sections "Decisões" → "Fluxo de status", "Modelo de dados").
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.justification_service import JustificationService


def _db_with_attendance(data: dict, actor_name: str = "Staff Um") -> tuple:
    """Fake firestore client with an `attendance/{doc_id}` doc + `users`."""
    db = MagicMock()

    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = data
    doc_ref = MagicMock()
    doc_ref.get.return_value = doc

    actor_doc = MagicMock()
    actor_doc.exists = True
    actor_doc.to_dict.return_value = {"name": actor_name}

    def collection(name):
        col = MagicMock()
        if name == "users":
            col.document.return_value.get.return_value = actor_doc
        else:
            col.document.return_value = doc_ref
        return col

    db.collection.side_effect = collection
    return db, doc_ref


class TestJustify:
    def test_absent_to_pending_writes_justification(self):
        db, doc_ref = _db_with_attendance({
            "projectId": "spartacus",
            "status": "absent",
            "userId": "aluno-1",
        })
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            JustificationService().justify(
                doc_id="att-1",
                project_id="spartacus",
                actor_uid="aluno-1",
                type_id="type-saude",
                type_name="Saúde",
                text="Estava doente",
            )

        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "absent_justification_pending"
        just = updated["justification"]
        assert just["typeId"] == "type-saude"
        assert just["typeName"] == "Saúde"
        assert just["text"] == "Estava doente"
        assert just["submittedBy"] == "aluno-1"
        assert just["submittedAt"]
        assert just["attachment"] is None
        assert just["reviewedAt"] is None
        assert just["rejectReason"] is None
        # No prior justification -> no history written
        assert "justificationHistory" not in updated

    def test_invalid_origin_status_raises_409(self):
        db, _ = _db_with_attendance({
            "projectId": "spartacus",
            "status": "confirmed",
            "userId": "aluno-1",
        })
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            with pytest.raises(HTTPException) as exc:
                JustificationService().justify(
                    doc_id="att-1",
                    project_id="spartacus",
                    actor_uid="aluno-1",
                    type_id="type-saude",
                    type_name="Saúde",
                    text="Estava doente",
                )
        assert exc.value.status_code == 409

    def test_pending_origin_status_raises_409(self):
        db, _ = _db_with_attendance({
            "projectId": "spartacus",
            "status": "absent_justification_pending",
            "userId": "aluno-1",
        })
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            with pytest.raises(HTTPException) as exc:
                JustificationService().justify(
                    doc_id="att-1",
                    project_id="spartacus",
                    actor_uid="aluno-1",
                    type_id="type-saude",
                    type_name="Saúde",
                    text="Estava doente",
                )
        assert exc.value.status_code == 409

    def test_reenvio_moves_prior_justification_to_history(self):
        prior_justification = {
            "typeId": "type-viagem",
            "typeName": "Viagem",
            "text": "Estava viajando",
            "attachment": None,
            "submittedAt": "2026-07-01T10:00:00+00:00",
            "submittedBy": "aluno-1",
            "reviewedAt": "2026-07-02T10:00:00+00:00",
            "reviewedBy": "staff-1",
            "reviewedByName": "Staff Um",
            "rejectReason": "Sem comprovação",
        }
        db, doc_ref = _db_with_attendance({
            "projectId": "spartacus",
            "status": "absent",
            "userId": "aluno-1",
            "justification": prior_justification,
            "justificationHistory": [{"typeId": "old-entry"}],
        })
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            JustificationService().justify(
                doc_id="att-1",
                project_id="spartacus",
                actor_uid="aluno-1",
                type_id="type-saude",
                type_name="Saúde",
                text="Reenvio com novo motivo",
            )

        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "absent_justification_pending"
        assert updated["justification"]["typeId"] == "type-saude"
        history = updated["justificationHistory"]
        assert history == [{"typeId": "old-entry"}, prior_justification]

    def test_not_found_raises_404(self):
        db = MagicMock()
        doc = MagicMock()
        doc.exists = False
        db.collection.return_value.document.return_value.get.return_value = doc
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            with pytest.raises(HTTPException) as exc:
                JustificationService().justify(
                    doc_id="att-missing",
                    project_id="spartacus",
                    actor_uid="aluno-1",
                    type_id="type-saude",
                    type_name="Saúde",
                    text="Estava doente",
                )
        assert exc.value.status_code == 404

    def test_project_mismatch_raises_403(self):
        db, _ = _db_with_attendance({
            "projectId": "outro-projeto",
            "status": "absent",
            "userId": "aluno-1",
        })
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            with pytest.raises(HTTPException) as exc:
                JustificationService().justify(
                    doc_id="att-1",
                    project_id="spartacus",
                    actor_uid="aluno-1",
                    type_id="type-saude",
                    type_name="Saúde",
                    text="Estava doente",
                )
        assert exc.value.status_code == 403


class TestApprove:
    def _pending_data(self, **overrides):
        base = {
            "projectId": "spartacus",
            "status": "absent_justification_pending",
            "userId": "aluno-1",
            "justification": {
                "typeId": "type-saude",
                "typeName": "Saúde",
                "text": "Estava doente",
                "attachment": None,
                "submittedAt": "2026-07-10T10:00:00+00:00",
                "submittedBy": "aluno-1",
                "reviewedAt": None,
                "reviewedBy": None,
                "reviewedByName": None,
                "rejectReason": None,
            },
        }
        base.update(overrides)
        return base

    def test_pending_to_justified(self):
        db, doc_ref = _db_with_attendance(self._pending_data())
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            JustificationService().approve(
                doc_id="att-1",
                project_id="spartacus",
                actor_uid="staff-1",
            )

        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "absent_justified"
        just = updated["justification"]
        assert just["typeId"] == "type-saude"  # preserved
        assert just["reviewedBy"] == "staff-1"
        assert just["reviewedByName"] == "Staff Um"
        assert just["reviewedAt"]

    def test_invalid_origin_status_raises_409(self):
        db, _ = _db_with_attendance(self._pending_data(status="absent"))
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            with pytest.raises(HTTPException) as exc:
                JustificationService().approve(
                    doc_id="att-1",
                    project_id="spartacus",
                    actor_uid="staff-1",
                )
        assert exc.value.status_code == 409

    def test_already_justified_raises_409(self):
        db, _ = _db_with_attendance(
            self._pending_data(status="absent_justified"),
        )
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            with pytest.raises(HTTPException) as exc:
                JustificationService().approve(
                    doc_id="att-1",
                    project_id="spartacus",
                    actor_uid="staff-1",
                )
        assert exc.value.status_code == 409


class TestReject:
    def _pending_data(self, **overrides):
        base = {
            "projectId": "spartacus",
            "status": "absent_justification_pending",
            "userId": "aluno-1",
            "justification": {
                "typeId": "type-saude",
                "typeName": "Saúde",
                "text": "Estava doente",
                "attachment": None,
                "submittedAt": "2026-07-10T10:00:00+00:00",
                "submittedBy": "aluno-1",
                "reviewedAt": None,
                "reviewedBy": None,
                "reviewedByName": None,
                "rejectReason": None,
            },
        }
        base.update(overrides)
        return base

    def test_pending_to_absent_with_reason(self):
        db, doc_ref = _db_with_attendance(self._pending_data())
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            JustificationService().reject(
                doc_id="att-1",
                project_id="spartacus",
                actor_uid="staff-1",
                reason="Sem comprovação",
            )

        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "absent"
        just = updated["justification"]
        assert just["rejectReason"] == "Sem comprovação"
        assert just["reviewedBy"] == "staff-1"
        assert just["reviewedByName"] == "Staff Um"
        # justification stays on the record (not moved to history yet) --
        # only a subsequent reenvio (justify) archives it.
        assert "justificationHistory" not in updated

    def test_missing_reason_raises_error(self):
        db, _ = _db_with_attendance(self._pending_data())
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            with pytest.raises(HTTPException) as exc:
                JustificationService().reject(
                    doc_id="att-1",
                    project_id="spartacus",
                    actor_uid="staff-1",
                    reason="",
                )
        assert exc.value.status_code == 422

    def test_invalid_origin_status_raises_409(self):
        db, _ = _db_with_attendance(self._pending_data(status="absent"))
        with patch("app.services.justification_service.firestore") as fs:
            fs.client.return_value = db
            with pytest.raises(HTTPException) as exc:
                JustificationService().reject(
                    doc_id="att-1",
                    project_id="spartacus",
                    actor_uid="staff-1",
                    reason="Sem comprovação",
                )
        assert exc.value.status_code == 409
