"""Tests for undo of confirmed validations (attendance + donations)."""

import pytest
from unittest.mock import MagicMock, patch

from app.services.validation_service import ValidationService


def _db_with_doc(data: dict) -> tuple[MagicMock, MagicMock]:
    db = MagicMock()
    doc = MagicMock()
    doc.exists = True
    doc.to_dict.return_value = data
    doc_ref = MagicMock()
    doc_ref.get.return_value = doc

    actor_doc = MagicMock()
    actor_doc.exists = True
    actor_doc.to_dict.return_value = {"name": "Staff Um"}

    def collection(name):
        col = MagicMock()
        if name == "users":
            col.document.return_value.get.return_value = actor_doc
        else:
            col.document.return_value = doc_ref
        return col

    db.collection.side_effect = collection
    return db, doc_ref


class TestUndoValidation:
    def test_attendance_confirmed_back_to_registered(self):
        db, doc_ref = _db_with_doc({
            "projectId": "spartacus",
            "status": "confirmed",
            "userId": "aluno-1",
            "userName": "Aluno Um",
            "turmaName": "Jiu Kids",
        })
        with patch("app.services.validation_service.firestore") as fs, \
             patch("app.services.validation_service.AccountHistoryService"):
            fs.client.return_value = db
            event = ValidationService().undo_validation(
                collection="attendance",
                doc_id="att-1",
                project_id="spartacus",
                actor_uid="staff-1",
            )

        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "registered"
        assert updated["validatedBy"] is None
        assert updated["validatedAt"] is None
        assert event.id == "checkin.validation_undone"

    def test_donation_received_back_to_pledged(self):
        db, doc_ref = _db_with_doc({
            "projectId": "spartacus",
            "status": "received",
            "userId": "aluno-1",
            "userName": "Aluno Um",
            "item": "cookies",
            "itemLabel": "1 pacote de bolacha",
        })
        with patch("app.services.validation_service.firestore") as fs, \
             patch("app.services.validation_service.AccountHistoryService"):
            fs.client.return_value = db
            event = ValidationService().undo_validation(
                collection="support",
                doc_id="don-1",
                project_id="spartacus",
                actor_uid="staff-1",
            )

        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "pledged"
        assert updated["receivedBy"] is None
        assert updated["receivedAt"] is None
        assert event.id == "support.validation_undone"
        assert (
            event.payload.personalization()["donation_amount"]
            == "1 pacote de bolacha"
        )

    def test_attendance_staff_registered_returns_to_column1(self):
        # previousStatus="absent" → staff registrou da coluna 1 (Ausente)
        db, doc_ref = _db_with_doc({
            "projectId": "spartacus",
            "status": "confirmed",
            "previousStatus": "absent",
            "userId": "aluno-1",
        })
        with patch("app.services.validation_service.firestore") as fs, \
             patch("app.services.validation_service.AccountHistoryService"):
            fs.client.return_value = db
            ValidationService().undo_validation(
                collection="attendance",
                doc_id="att-1",
                project_id="spartacus",
                actor_uid="staff-1",
            )
        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "absent"  # volta para coluna 1
        assert updated["previousStatus"] is None

    def test_donation_staff_registered_returns_to_column1(self):
        db, doc_ref = _db_with_doc({
            "projectId": "spartacus",
            "status": "received",
            "previousStatus": "absent",
            "userId": "aluno-1",
            "item": "juice",
        })
        with patch("app.services.validation_service.firestore") as fs, \
             patch("app.services.validation_service.AccountHistoryService"):
            fs.client.return_value = db
            ValidationService().undo_validation(
                collection="donations",
                doc_id="don-1",
                project_id="spartacus",
                actor_uid="staff-1",
            )
        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "absent"  # volta para "Sem doação"
        assert updated["receivedBy"] is None

    def test_rejects_when_not_confirmed(self):
        db, _ = _db_with_doc({
            "projectId": "spartacus",
            "status": "registered",
            "userId": "aluno-1",
        })
        with patch("app.services.validation_service.firestore") as fs, \
             patch("app.services.validation_service.AccountHistoryService"):
            fs.client.return_value = db
            with pytest.raises(ValueError):
                ValidationService().undo_validation(
                    collection="attendance",
                    doc_id="att-1",
                    project_id="spartacus",
                    actor_uid="staff-1",
                )
