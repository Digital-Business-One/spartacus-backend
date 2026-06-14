"""Tests for Apoio (support) — donations + services, no monthly lock."""

from unittest.mock import MagicMock, patch

from app.models.support import (
    SupportConfigUpdate,
    SupportCreate,
    SupportRegisterReceived,
)
from app.services.event_service import EventService  # noqa: F401 (sanity import)
from app.services.support_service import SupportService


def _project_doc(support_config=None, donation_config=None):
    d = MagicMock()
    d.exists = True
    data = {}
    if support_config is not None:
        data["supportConfig"] = support_config
    if donation_config is not None:
        data["donationConfig"] = donation_config
    d.to_dict.return_value = data
    return d


class TestConfig:
    def _svc_with_project(self, proj_doc):
        db = MagicMock()
        proj_ref = MagicMock()
        proj_ref.get.return_value = proj_doc
        db.collection.return_value.document.return_value = proj_ref
        return db, proj_ref

    def test_defaults_when_missing(self):
        db, _ = self._svc_with_project(_project_doc())
        with patch("app.services.support_service.firestore") as fs:
            fs.client.return_value = db
            cfg = SupportService().get_config("spartacus")
        assert any(i.code == "cookies" for i in cfg.donations)
        assert any(i.code == "class" for i in cfg.services)
        assert cfg.thank_you_message

    def test_migrates_legacy_donation_config(self):
        legacy = {
            "items": [{"code": "x", "label": "Item X", "active": True}],
            "thankYouMessage": "Valeu!",
        }
        db, _ = self._svc_with_project(_project_doc(donation_config=legacy))
        with patch("app.services.support_service.firestore") as fs:
            fs.client.return_value = db
            cfg = SupportService().get_config("spartacus")
        assert [i.code for i in cfg.donations] == ["x"]
        assert cfg.thank_you_message == "Valeu!"
        assert len(cfg.services) > 0  # services seeded with defaults

    def test_update_writes_nested_fields(self):
        proj = _project_doc(support_config={"donations": [], "services": []})
        db, ref = self._svc_with_project(proj)
        with patch("app.services.support_service.firestore") as fs:
            fs.client.return_value = db
            SupportService().update_config(
                "spartacus",
                SupportConfigUpdate(services=[]),
            )
        upd = ref.update.call_args[0][0]
        assert "supportConfig.services" in upd


class TestCreateNoLock:
    def _make_db(self):
        db = MagicMock()
        # config read (project doc) + user doc + add
        proj = _project_doc(support_config={
            "donations": [{"code": "cookies", "label": "Bolacha", "active": True}],
            "services": [{"code": "class", "label": "Dar aula", "active": True}],
        })
        user = MagicMock(exists=True, to_dict=MagicMock(return_value={"name": "Aluno"}))
        added = []

        def collection(name):
            col = MagicMock()
            if name == "projects":
                col.document.return_value.get.return_value = proj
            elif name == "users":
                col.document.return_value.get.return_value = user
            elif name == "support":
                ref = MagicMock(); ref.id = f"sup-{len(added)+1}"
                def add(doc):
                    added.append(doc); return (None, ref)
                col.add.side_effect = add
            return col

        db.collection.side_effect = collection
        return db, added

    def test_create_donation_and_service_no_duplicate_check(self):
        db, added = self._make_db()
        with patch("app.services.support_service.firestore") as fs, \
             patch("app.services.support_service.AccountHistoryService"):
            fs.client.return_value = db
            svc = SupportService()
            o1, e1 = svc.create("spartacus", "u1", None,
                                SupportCreate(supportType="donation", item="cookies"))
            o2, e2 = svc.create("spartacus", "u1", None,
                                SupportCreate(supportType="donation", item="cookies"))
            o3, e3 = svc.create("spartacus", "u1", None,
                                SupportCreate(supportType="service", item="class"))
        # 3 records created, no 409 — no lock
        assert len(added) == 3
        assert added[0]["supportType"] == "donation"
        assert added[0]["itemLabel"] == "Bolacha"
        assert added[2]["supportType"] == "service"
        assert added[2]["itemLabel"] == "Dar aula"
        assert e1.id == "support.registered"
        assert e3.payload.personalization()["support_type"] == "service"


class TestDashboardPerRecord:
    def test_lists_records_with_type_filter(self):
        recs = [
            MagicMock(id="s1", to_dict=MagicMock(return_value={
                "userId": "u1", "supportType": "donation", "item": "cookies",
                "itemLabel": "Bolacha", "status": "pledged", "month": "2026-06",
            })),
            MagicMock(id="s2", to_dict=MagicMock(return_value={
                "userId": "u1", "supportType": "service", "item": "class",
                "itemLabel": "Dar aula", "status": "received", "month": "2026-06",
            })),
        ]
        db = MagicMock()
        support_q = MagicMock()
        support_q.where.return_value.where.return_value.stream.return_value = recs
        users_col = MagicMock()
        udoc = MagicMock(id="u1", exists=True,
                         to_dict=MagicMock(return_value={"name": "Aluno Um"}))
        db.get_all.return_value = [udoc]
        db.collection.side_effect = lambda n: support_q if n == "support" else users_col

        with patch("app.services.support_service.firestore") as fs:
            fs.client.return_value = db
            out = SupportService().dashboard("spartacus", "2026-06", "service")
        assert len(out.items) == 1
        assert out.items[0].support_type == "service"
        assert out.items[0].item_label == "Dar aula"
        assert out.items[0].status == "received"


class TestRegisterReceived:
    def test_creates_received_service(self):
        db = MagicMock()
        proj = _project_doc(support_config={
            "donations": [], "services": [{"code": "class", "label": "Aula", "active": True}],
        })
        user = MagicMock(exists=True, to_dict=MagicMock(return_value={"name": "Aluno"}))
        ref = MagicMock(); ref.id = "sup-x"
        added = {}

        def collection(name):
            col = MagicMock()
            if name == "projects":
                col.document.return_value.get.return_value = proj
            elif name == "users":
                col.document.return_value.get.return_value = user
            elif name == "support":
                def add(doc): added.update(doc); return (None, ref)
                col.add.side_effect = add
            return col
        db.collection.side_effect = collection

        with patch("app.services.support_service.firestore") as fs, \
             patch("app.services.support_service.AccountHistoryService"):
            fs.client.return_value = db
            out, event = SupportService().register_received(
                "spartacus", "staff-1",
                SupportRegisterReceived(userId="u1", supportType="service", item="class"),
            )
        assert added["status"] == "received"
        assert added["supportType"] == "service"
        assert out.status == "received"
        assert event.id == "support.received"
