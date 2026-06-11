"""
Tests for the donations dashboard (kanban) — RFC dashboard de doações.

Covers: GET /donations/dashboard aggregation (status mapping, guardian
links) and POST /donations/register-received (update vs create + event).
Firestore is mocked following the project test conventions.
"""

from unittest.mock import MagicMock, patch

from app.models.donation import DonationRegisterReceived
from app.services.donation_service import DonationService


def _doc(doc_id: str, data: dict, exists: bool = True) -> MagicMock:
    d = MagicMock()
    d.id = doc_id
    d.exists = exists
    d.to_dict.return_value = data
    return d


def _query(docs: list) -> MagicMock:
    """Mock a query whose chained .where()/.limit() ends in .stream()."""
    q = MagicMock()
    q.where.return_value = q
    q.limit.return_value = q
    q.stream.return_value = docs
    return q


class TestDonationDashboard:
    def _make_db(
        self,
        memberships: list,
        donations: list,
        users_by_uid: dict,
        guardians_by_uid: dict | None = None,
    ) -> MagicMock:
        db = MagicMock()
        collections = {
            "memberships": _query(memberships),
            "donations": _query(donations),
        }
        users_col = MagicMock()
        users_col.document.side_effect = lambda uid: uid  # ref == uid
        collections["users"] = users_col
        db.collection.side_effect = lambda name: collections[name]

        all_users = {**users_by_uid, **(guardians_by_uid or {})}
        db.get_all.side_effect = lambda refs: [
            all_users.get(r, _doc(str(r), {}, exists=False)) for r in refs
        ]
        return db

    def test_status_mapping_and_columns(self):
        memberships = [
            _doc("m1", {"userId": "adult-1", "roles": ["student"], "status": "active"}),
            _doc("m2", {"userId": "kid-1", "roles": ["student"], "status": "active"}),
            _doc("m3", {"userId": "kid-2", "roles": ["student"], "status": "active"}),
            _doc("m4", {"userId": "prof-1", "roles": ["teacher"], "status": "active"}),
        ]
        donations = [
            _doc("d1", {"userId": "kid-1", "status": "pledged",
                        "item": "cookies", "createdAt": "2026-06-01"}),
            _doc("d2", {"userId": "kid-2", "status": "received",
                        "item": "juice", "validatedAt": "2026-06-02"}),
        ]
        users = {
            "adult-1": _doc("adult-1", {"name": "Adulto Um", "birthDate": "01/01/2000"}),
            "kid-1": _doc("kid-1", {"name": "Kid Um", "guardianUid": "resp-1"}),
            "kid-2": _doc("kid-2", {"name": "Kid Dois", "guardianUid": "resp-1"}),
        }
        guardians = {"resp-1": _doc("resp-1", {"name": "Responsável Um"})}

        db = self._make_db(memberships, donations, users, guardians)
        with patch("app.services.donation_service.firestore") as fs:
            fs.client.return_value = db
            out = DonationService().dashboard("spartacus")

        by_uid = {s.user_id: s for s in out.students}
        assert set(by_uid) == {"adult-1", "kid-1", "kid-2"}  # teacher fora
        assert by_uid["adult-1"].status == "none"
        assert by_uid["kid-1"].status == "pledged"
        assert by_uid["kid-1"].item_label == "1 pacote de bolacha"
        assert by_uid["kid-2"].status == "received"
        # Dependentes linkados ao responsável
        assert by_uid["kid-1"].is_dependent is True
        assert by_uid["kid-1"].guardian_name == "Responsável Um"
        assert by_uid["adult-1"].is_dependent is False
        assert by_uid["adult-1"].age is not None and by_uid["adult-1"].age >= 18

    def test_rejected_donation_appears_as_none(self):
        memberships = [
            _doc("m1", {"userId": "u1", "roles": ["student"], "status": "active"}),
        ]
        donations = [
            _doc("d1", {"userId": "u1", "status": "absent", "item": "coffee"}),
        ]
        users = {"u1": _doc("u1", {"name": "User Um"})}

        db = self._make_db(memberships, donations, users)
        with patch("app.services.donation_service.firestore") as fs:
            fs.client.return_value = db
            out = DonationService().dashboard("spartacus")

        assert out.students[0].status == "none"
        # donation_id preservado para o force-register atualizar o doc
        assert out.students[0].donation_id == "d1"


class TestRegisterReceived:
    def _make_db(self, existing_donations: list) -> tuple[MagicMock, MagicMock]:
        db = MagicMock()
        users_col = MagicMock()
        user_doc = _doc("aluno-1", {"name": "Aluno Um"})
        actor_doc = _doc("staff-1", {"name": "Prof. Staff"})
        users_col.document.side_effect = lambda uid: MagicMock(
            get=MagicMock(return_value=user_doc if uid == "aluno-1" else actor_doc),
        )
        donations_col = _query(existing_donations)
        new_ref = MagicMock()
        new_ref.id = "new-don-1"
        donations_col.add.return_value = (None, new_ref)
        db.collection.side_effect = lambda name: {
            "users": users_col,
            "donations": donations_col,
        }[name]
        return db, donations_col

    def test_creates_received_doc_when_none_exists(self):
        db, donations_col = self._make_db(existing_donations=[])
        data = DonationRegisterReceived(user_id="aluno-1", item="cookies")

        with patch("app.services.donation_service.firestore") as fs, \
             patch("app.services.donation_service.AccountHistoryService") as hist:
            fs.client.return_value = db
            result, event = DonationService().register_received(
                project_id="spartacus", actor_uid="staff-1", data=data,
            )

        added = donations_col.add.call_args[0][0]
        assert added["status"] == "received"
        assert added["validatedBy"] == "staff-1"
        assert added["userId"] == "aluno-1"
        assert result.status == "received"
        assert event.id == "donation.received"
        p = event.payload.personalization()
        assert p["target_uid"] == "aluno-1"
        assert p["donation_amount"] == "1 pacote de bolacha"
        hist.return_value.record.assert_called_once()

    def test_updates_existing_doc_for_month(self):
        existing = _doc("don-1", {"createdAt": "2026-06-01", "status": "absent"})
        existing.reference = MagicMock()
        existing.reference.id = "don-1"
        db, donations_col = self._make_db(existing_donations=[existing])
        data = DonationRegisterReceived(
            user_id="aluno-1", item="other", item_description="Ajudou na obra",
        )

        with patch("app.services.donation_service.firestore") as fs, \
             patch("app.services.donation_service.AccountHistoryService"):
            fs.client.return_value = db
            result, event = DonationService().register_received(
                project_id="spartacus", actor_uid="staff-1", data=data,
            )

        donations_col.add.assert_not_called()
        updated = existing.reference.update.call_args[0][0]
        assert updated["status"] == "received"
        assert updated["item"] == "other"
        assert result.id == "don-1"
        p = event.payload.personalization()
        assert p["donation_amount"] == "Outra forma de apoio: Ajudou na obra"
