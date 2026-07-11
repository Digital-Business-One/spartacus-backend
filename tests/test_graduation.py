"""Tests for graduation progression, approval and promotion."""

from unittest.mock import MagicMock, patch

import pytest

from app.models.graduation_system import AgeBand, Belt, GraduationSystem
from app.services.graduation_service import GraduationService

JIU = GraduationSystem(
    modality_slug="jiu-jitsu",
    modality_name="Jiu-Jitsu",
    type="age_banded",
    age_bands=[
        AgeBand(min_age=4, max_age=15, belts=[
            Belt(order=0, slug="branca", name="Branca", color="#f2f2f2", max_degree=4),
            Belt(order=1, slug="cinza", name="Cinza", color="#a8a8a8", max_degree=4),
            Belt(order=2, slug="amarela", name="Amarela", color="#f0c83b", max_degree=4),
        ]),
        AgeBand(min_age=16, max_age=200, belts=[
            Belt(order=0, slug="branca", name="Branca", color="#f2f2f2", max_degree=4),
            Belt(order=1, slug="azul", name="Azul", color="#1f4fa0", max_degree=4),
            Belt(order=2, slug="preta", name="Preta", color="#111111", max_degree=4),
        ]),
    ],
)

CAPOEIRA = GraduationSystem(
    modality_slug="capoeira", modality_name="Capoeira", type="linear",
    age_bands=[AgeBand(min_age=0, max_age=200, belts=[
        Belt(order=0, slug="crua", name="Crua", color="#dac6a5", max_degree=0),
        Belt(order=1, slug="amarela", name="Amarela", color="#f0c83b", max_degree=0),
    ])],
)


class TestResolveProgression:
    def setup_method(self):
        self.svc = GraduationService()

    def test_age_selects_band(self):
        # 10yo on white → kids band, next is cinza
        p = self.svc.resolve_progression(JIU, 10, "branca", 0)
        assert p["max_degree"] == 4
        assert p["can_add_degree"] is True
        assert p["next_belt"].slug == "cinza"
        # 30yo on white → adult band, next is azul
        p2 = self.svc.resolve_progression(JIU, 30, "branca", 0)
        assert p2["next_belt"].slug == "azul"

    def test_degree_cap(self):
        p = self.svc.resolve_progression(JIU, 30, "azul", 4)
        assert p["can_add_degree"] is False  # já no 4º grau

    def test_last_belt_has_no_next(self):
        p = self.svc.resolve_progression(JIU, 30, "preta", 0)
        assert p["next_belt"] is None

    def test_out_of_band_belt(self):
        # adult on a kids-only belt (amarela not in adult band)
        p = self.svc.resolve_progression(JIU, 30, "amarela", 0)
        assert p["out_of_band"] is True
        assert p["can_add_degree"] is False
        assert p["next_belt"] is None

    def test_no_current_belt_next_is_first(self):
        p = self.svc.resolve_progression(JIU, 30, None, 0)
        assert p["next_belt"].slug == "branca"

    def test_linear_ignores_age(self):
        p = self.svc.resolve_progression(CAPOEIRA, None, "crua", 0)
        assert p["next_belt"].slug == "amarela"
        assert p["max_degree"] == 0
        assert p["can_add_degree"] is False


def _user(uid, name, birth, graduation):
    d = MagicMock()
    d.id = uid
    d.exists = True
    d.to_dict.return_value = {
        "name": name, "birthDate": birth, "graduation": graduation,
    }
    return d


def _mock_db_for_action(user_doc, system_doc_data):
    db = MagicMock()
    user_ref = MagicMock()
    user_ref.get.return_value = user_doc
    actor_doc = MagicMock(exists=True, to_dict=MagicMock(return_value={"name": "Prof"}))
    sys_doc = MagicMock(exists=True, to_dict=MagicMock(return_value=system_doc_data))

    def collection(name):
        col = MagicMock()
        if name == "users":
            col.document.side_effect = lambda u: (
                user_ref if u == user_doc.id
                else MagicMock(get=MagicMock(return_value=actor_doc))
            )
        elif name == "graduation_systems":
            col.document.return_value.get.return_value = sys_doc
        return col

    db.collection.side_effect = collection
    return db, user_ref


_SYS_DATA = {
    "modalitySlug": "jiu-jitsu", "modalityName": "Jiu-Jitsu",
    "type": "age_banded",
    "ageBands": [
        {"minAge": 16, "maxAge": 200, "belts": [
            {"order": 0, "slug": "branca", "name": "Branca", "color": "#f2f2f2", "maxDegree": 4},
            {"order": 1, "slug": "azul", "name": "Azul", "color": "#1f4fa0", "maxDegree": 4},
        ]},
    ],
}


class TestApprove:
    def test_approve_sets_status_and_event(self):
        udoc = _user("aluno-1", "João", "01/01/2000",
                     {"jiu-jitsu": {"belt": "branca", "degree": 0, "status": "pending"}})
        db, user_ref = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            event = GraduationService().approve("spartacus", "aluno-1", "jiu-jitsu", "staff-1")
        written = user_ref.update.call_args[0][0]["graduation"]["jiu-jitsu"]
        assert written["status"] == "approved"
        assert written["gradedBy"] == "staff-1"
        assert event.id == "graduation.approved"

    def test_approve_without_entry_raises(self):
        udoc = _user("aluno-1", "João", "01/01/2000", {})
        db, _ = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            with pytest.raises(ValueError):
                GraduationService().approve("spartacus", "aluno-1", "jiu-jitsu", "staff-1")


class TestPromote:
    def test_promote_degree(self):
        udoc = _user("aluno-1", "João", "01/01/2000",
                     {"jiu-jitsu": {"belt": "azul", "degree": 1, "status": "approved"}})
        db, user_ref = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            event = GraduationService().promote("spartacus", "aluno-1", "jiu-jitsu", "staff-1", "degree")
        written = user_ref.update.call_args[0][0]["graduation"]["jiu-jitsu"]
        assert written["degree"] == 2
        assert event.id == "graduation.promoted"

    def test_promote_belt_resets_degree(self):
        udoc = _user("aluno-1", "João", "01/01/2000",
                     {"jiu-jitsu": {"belt": "branca", "degree": 4, "status": "approved"}})
        db, user_ref = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            GraduationService().promote("spartacus", "aluno-1", "jiu-jitsu", "staff-1", "belt")
        written = user_ref.update.call_args[0][0]["graduation"]["jiu-jitsu"]
        assert written["belt"] == "azul"
        assert written["degree"] == 0

    def test_promote_snapshots_prev_for_undo(self):
        udoc = _user("aluno-1", "João", "01/01/2000",
                     {"jiu-jitsu": {"belt": "branca", "degree": 2, "status": "approved"}})
        db, user_ref = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            GraduationService().promote("spartacus", "aluno-1", "jiu-jitsu", "staff-1", "degree")
        written = user_ref.update.call_args[0][0]["graduation"]["jiu-jitsu"]
        assert written["degree"] == 3
        assert written["prev"] == {"belt": "branca", "degree": 2, "status": "approved"}


class TestReject:
    def test_reject_unlocks_and_emits_event(self):
        udoc = _user("aluno-1", "João", "01/01/2000",
                     {"jiu-jitsu": {"belt": "preta", "degree": 0, "status": "pending",
                                    "lockedByStudent": True}})
        db, user_ref = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            ev = GraduationService().reject("spartacus", "aluno-1", "jiu-jitsu", "staff-1")
        written = user_ref.update.call_args[0][0]["graduation"]["jiu-jitsu"]
        assert written["status"] == "rejected"
        assert written["lockedByStudent"] is False
        assert ev.id == "graduation.rejected"

    def test_reject_only_pending(self):
        udoc = _user("aluno-1", "João", "01/01/2000",
                     {"jiu-jitsu": {"belt": "azul", "degree": 0, "status": "approved"}})
        db, _ = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            with pytest.raises(ValueError):
                GraduationService().reject("spartacus", "aluno-1", "jiu-jitsu", "staff-1")


class TestRejectedAllowsReedit:
    def test_student_can_resubmit_rejected(self):
        from app.models.profile import GraduationEntry, GraduationUpdate
        from app.services.profile_service import ProfileService

        snap = MagicMock(exists=True, to_dict=MagicMock(return_value={
            "graduation": {"jiu-jitsu": {"belt": "preta", "degree": 0,
                                         "status": "rejected"}},
        }))
        ref = MagicMock()
        ref.get.return_value = snap
        db = MagicMock()
        db.collection.return_value.document.return_value = ref
        with patch("app.services.profile_service.firestore") as fs:
            fs.client.return_value = db
            ProfileService().update_graduation(
                uid="aluno-1", acting_as=None,
                data=GraduationUpdate(graduation={
                    "jiu-jitsu": GraduationEntry(belt="branca", degree=0),
                }),
            )
        written = ref.update.call_args[0][0]["graduation"]["jiu-jitsu"]
        assert written["belt"] == "branca"          # overwritten
        assert written["status"] == "pending"       # back to pending
        assert written["lockedByStudent"] is True


class TestUndo:
    def test_undo_restores_previous(self):
        udoc = _user("aluno-1", "João", "01/01/2000", {
            "jiu-jitsu": {
                "belt": "azul", "degree": 0, "status": "approved",
                "prev": {"belt": "branca", "degree": 4, "status": "approved"},
            },
        })
        db, user_ref = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            GraduationService().undo("spartacus", "aluno-1", "jiu-jitsu", "staff-1")
        written = user_ref.update.call_args[0][0]["graduation"]["jiu-jitsu"]
        assert written["belt"] == "branca"
        assert written["degree"] == 4
        assert "prev" not in written

    def test_undo_from_nothing_removes_entry(self):
        udoc = _user("aluno-1", "João", "01/01/2000", {
            "jiu-jitsu": {
                "belt": "branca", "degree": 0, "status": "approved",
                "prev": {"belt": None, "degree": 0, "status": None},
            },
        })
        db, user_ref = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            GraduationService().undo("spartacus", "aluno-1", "jiu-jitsu", "staff-1")
        written = user_ref.update.call_args[0][0]["graduation"]
        assert "jiu-jitsu" not in written

    def test_undo_without_prev_raises(self):
        udoc = _user("aluno-1", "João", "01/01/2000",
                     {"jiu-jitsu": {"belt": "azul", "degree": 0, "status": "approved"}})
        db, _ = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            with pytest.raises(ValueError):
                GraduationService().undo("spartacus", "aluno-1", "jiu-jitsu", "staff-1")


class TestPromoteBeyondMax:
    def test_promote_degree_beyond_max_raises(self):
        udoc = _user("aluno-1", "João", "01/01/2000",
                     {"jiu-jitsu": {"belt": "azul", "degree": 4, "status": "approved"}})
        db, _ = _mock_db_for_action(udoc, _SYS_DATA)
        with patch("app.services.graduation_service.firestore") as fs, \
             patch("app.services.graduation_service.AccountHistoryService"):
            fs.client.return_value = db
            with pytest.raises(ValueError):
                GraduationService().promote("spartacus", "aluno-1", "jiu-jitsu", "staff-1", "degree")


class TestStudentLock:
    """profile_service.update_graduation: student may re-edit at any time; a
    real change re-opens approval (status→pending); unchanged is preserved."""

    def test_first_insert_is_pending_and_locked(self):
        from app.models.profile import GraduationEntry, GraduationUpdate
        from app.services.profile_service import ProfileService

        snap = MagicMock(exists=True, to_dict=MagicMock(return_value={"graduation": {}}))
        ref = MagicMock()
        ref.get.return_value = snap
        db = MagicMock()
        db.collection.return_value.document.return_value = ref

        with patch("app.services.profile_service.firestore") as fs:
            fs.client.return_value = db
            changed = ProfileService().update_graduation(
                uid="aluno-1", acting_as=None,
                data=GraduationUpdate(graduation={
                    "Jiu-Jitsu": GraduationEntry(belt="azul", degree=0),
                }),
            )
        assert changed is True
        written = ref.update.call_args[0][0]["graduation"]
        # normalized key + pending + locked
        assert "jiu-jitsu" in written
        assert written["jiu-jitsu"]["status"] == "pending"
        assert written["jiu-jitsu"]["lockedByStudent"] is True

    def test_real_edit_overwrites_and_resets_to_pending(self):
        """Editing an approved modality re-opens staff approval."""
        from app.models.profile import GraduationEntry, GraduationUpdate
        from app.services.profile_service import ProfileService

        snap = MagicMock(exists=True, to_dict=MagicMock(return_value={
            "graduation": {"jiu-jitsu": {
                "belt": "preta", "degree": 0, "status": "approved",
                "gradedBy": "staff1", "gradedByName": "Prof", "gradedAt": "x",
            }},
        }))
        ref = MagicMock()
        ref.get.return_value = snap
        db = MagicMock()
        db.collection.return_value.document.return_value = ref

        with patch("app.services.profile_service.firestore") as fs:
            fs.client.return_value = db
            changed = ProfileService().update_graduation(
                uid="aluno-1", acting_as=None,
                data=GraduationUpdate(graduation={
                    "jiu-jitsu": GraduationEntry(belt="branca", degree=0),
                }),
            )
        assert changed is True
        written = ref.update.call_args[0][0]["graduation"]["jiu-jitsu"]
        assert written["belt"] == "branca"        # overwritten
        assert written["status"] == "pending"     # approval re-opened
        assert written["gradedBy"] is None         # prior grading cleared

    def test_unchanged_modality_is_preserved_and_no_write(self):
        """Submitting values identical to stored is a no-op — status kept,
        no Firestore write, changed=False (no phantom success)."""
        from app.models.profile import GraduationEntry, GraduationUpdate
        from app.services.profile_service import ProfileService

        snap = MagicMock(exists=True, to_dict=MagicMock(return_value={
            "graduation": {"jiu-jitsu": {
                "belt": "azul", "degree": 2, "prajied": None, "status": "approved",
            }},
        }))
        ref = MagicMock()
        ref.get.return_value = snap
        db = MagicMock()
        db.collection.return_value.document.return_value = ref

        with patch("app.services.profile_service.firestore") as fs:
            fs.client.return_value = db
            changed = ProfileService().update_graduation(
                uid="aluno-1", acting_as=None,
                data=GraduationUpdate(graduation={
                    "jiu-jitsu": GraduationEntry(belt="azul", degree=2),
                }),
            )
        assert changed is False
        ref.update.assert_not_called()
