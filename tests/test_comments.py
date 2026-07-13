"""Tests for TimelineService comment methods (add/list/delete) + can_view_entry."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.comment import CommentCreate
from app.security.context import AuthContext
from app.services.timeline_service import TimelineService

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_FS = "app.services.timeline_service.firestore"
_VERIFY = "app.security.middleware.verify_id_token"
_PROJECT_ID = "spartacus-artes-marciais"
_VALID_CLAIMS = {
    "uid": "u1",
    "email": "u1@test.com",
    "projects": {_PROJECT_ID: ["student"]},
}
_HEADERS = {"Authorization": "Bearer valid-token", "X-Project-Id": _PROJECT_ID}


def _ctx(uid="u1", roles=None):
    return AuthContext(
        user_id=uid,
        user_email=f"{uid}@test.com",
        project_id="spartacus-artes-marciais",
        roles=roles or [],
    )


def _entry(**over):
    base = {
        "visibility": "public",
        "status": "active",
        "targetUid": None,
        "authorUid": "author1",
    }
    base.update(over)
    return base


def _mock_db(entry, comment_docs=None, user_doc=None):
    db = MagicMock()
    entry_ref = MagicMock()
    entry_snap = MagicMock(exists=bool(entry))
    entry_snap.to_dict.return_value = entry
    entry_ref.get.return_value = entry_snap
    # comments subcollection
    comments_col = MagicMock()
    entry_ref.collection.return_value = comments_col
    added_ref = MagicMock()
    added_ref.id = "c-new"
    comments_col.add.return_value = (None, added_ref)
    comments_col.document.return_value = MagicMock()
    # entries collection
    entries_col = MagicMock()
    entries_col.document.return_value = entry_ref
    # users collection (author lookup)
    users_col = MagicMock()
    u_ref = MagicMock()
    u_snap = MagicMock(exists=True)
    u_snap.to_dict.return_value = user_doc or {
        "name": "Autor",
        "nickname": None,
        "photoUrl": None,
        "birthDate": "01/01/1990",
    }
    u_ref.get.return_value = u_snap
    users_col.document.return_value = u_ref

    def coll(name):
        return {"timeline_entries": entries_col, "users": users_col}.get(
            name, MagicMock()
        )

    db.collection.side_effect = coll
    return db, entry_ref, comments_col


class TestAddComment:
    def test_public_entry_any_member_can_comment(self):
        db, entry_ref, comments = _mock_db(_entry())
        with patch(_FS) as fs:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            out = TimelineService().add_comment(
                "e1", _ctx("u1"), CommentCreate(text="Oi!")
            )
        assert out.text == "Oi!"
        assert out.id == "c-new"
        comments.add.assert_called_once()
        entry_ref.update.assert_called_with({"commentsCount": "INC"})

    def test_personal_entry_blocks_outsider(self):
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, *_ = _mock_db(entry)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().add_comment(
                    "e1", _ctx("stranger"), CommentCreate(text="x")
                )

    def test_missing_entry_raises_lookup(self):
        db, *_ = _mock_db(None)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(LookupError):
                TimelineService().add_comment("e1", _ctx(), CommentCreate(text="x"))


class TestListDelete:
    def test_list_returns_items_visible_entry(self):
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        c1 = MagicMock()
        c1.id = "c1"
        c1.to_dict.return_value = {
            "authorUid": "u1",
            "authorName": "A",
            "authorPhotoUrl": None,
            "text": "hi",
            "parentId": None,
            "mentions": [],
            "createdAt": "2026-07-13T00:00:00+00:00",
            "deleted": False,
            "deletedBy": None,
        }
        q = MagicMock()
        q.stream.return_value = [c1]
        comments.order_by.return_value.limit.return_value = q
        with patch(_FS) as fs:
            fs.client.return_value = db
            page = TimelineService().list_comments("e1", _ctx("u1"))
        assert len(page.items) == 1 and page.items[0].text == "hi"

    def test_delete_by_staff_soft_deletes(self):
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=True)
        csnap.to_dict.return_value = {"authorUid": "someone", "deleted": False}
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_FS) as fs:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="DEC")
            TimelineService().delete_comment("e1", "c1", _ctx("staff", ["teacher"]))
        cref.update.assert_called_once()
        assert cref.update.call_args[0][0]["deleted"] is True
        # Safety-critical: successful staff soft-delete must decrement the
        # entry's denormalized commentsCount counter.
        entry_ref.update.assert_called_with({"commentsCount": "DEC"})

    def test_delete_by_stranger_raises_permission(self):
        """A non-author, non-staff user must not be able to delete a comment."""
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=True)
        csnap.to_dict.return_value = {"authorUid": "someone_else", "deleted": False}
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().delete_comment("e1", "c1", _ctx("stranger"))

    def test_delete_already_deleted_is_noop(self):
        """Deleting an already-deleted comment must be a silent no-op."""
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=True)
        csnap.to_dict.return_value = {"authorUid": "someone", "deleted": True}
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_FS) as fs:
            fs.client.return_value = db
            TimelineService().delete_comment("e1", "c1", _ctx("staff", ["teacher"]))
        cref.update.assert_not_called()

    def test_delete_missing_comment_raises_lookup(self):
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=False)
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(LookupError):
                TimelineService().delete_comment("e1", "c1", _ctx())

    def test_list_on_blocked_entry_raises_permission(self):
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, *_ = _mock_db(entry)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().list_comments("e1", _ctx("stranger"))


class TestMentionable:
    def _members(self):
        # (uid, name, nickname, birthDate, photoUrl)
        return [
            {"uid": "adult1", "name": "Maratona JJ", "nickname": "Maratona",
             "birthDate": "01/01/1990", "photoUrl": None},
            {"uid": "minor1", "name": "Pedro Kid", "nickname": None,
             "birthDate": "01/01/2015", "photoUrl": None},
        ]

    def test_only_adults_returned_and_ordered(self):
        entry = _entry()
        db, entry_ref, _ = _mock_db(entry)
        # membership query → uids; users batch → docs
        with patch(_FS) as fs, \
             patch.object(TimelineService, "_project_member_docs",
                          return_value=self._members()):
            fs.client.return_value = db
            res = TimelineService().list_mentionable("e1", _ctx("u1"), q="mar")
        assert [m.uid for m in res] == ["adult1"]     # minor excluído
        assert res[0].display == "Maratona"           # apelido tem prioridade
        assert res[0].subtitle == "Maratona JJ"
        assert res[0].initials == "MA"

    def test_missing_entry_raises_lookup(self):
        db, *_ = _mock_db(None)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(LookupError):
                TimelineService().list_mentionable("e1", _ctx("u1"))

    def test_blocked_entry_raises_permission(self):
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, *_ = _mock_db(entry)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().list_mentionable("e1", _ctx("stranger"))

    def test_no_query_returns_all_adults_sorted(self):
        entry = _entry()
        db, *_ = _mock_db(entry)
        members = self._members() + [
            {"uid": "adult2", "name": "Ana Silva", "nickname": None,
             "birthDate": "01/01/1980", "photoUrl": None},
        ]
        with patch(_FS) as fs, \
             patch.object(TimelineService, "_project_member_docs",
                          return_value=members):
            fs.client.return_value = db
            res = TimelineService().list_mentionable("e1", _ctx("u1"))
        assert [m.uid for m in res] == ["adult2", "adult1"]
        assert res[1].initials == "MA"


class TestCommentRoutes:
    """TestClient-level tests for the REST endpoints (Task 5)."""

    def test_post_comment_201(self):
        entry = _entry()
        db, *_ = _mock_db(entry)
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            r = client.post(
                "/timeline/e1/comments", headers=_HEADERS, json={"text": "Olá!"}
            )
        assert r.status_code == 201
        assert r.json()["text"] == "Olá!"

    def test_post_outsider_403(self):
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, *_ = _mock_db(entry)
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.post(
                "/timeline/e1/comments", headers=_HEADERS, json={"text": "x"}
            )
        assert r.status_code == 403

    def test_post_missing_entry_404(self):
        db, *_ = _mock_db(None)
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.post(
                "/timeline/e1/comments", headers=_HEADERS, json={"text": "x"}
            )
        assert r.status_code == 404

    def test_list_comments_200(self):
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        c1 = MagicMock()
        c1.id = "c1"
        c1.to_dict.return_value = {
            "authorUid": "u1",
            "authorName": "A",
            "authorPhotoUrl": None,
            "text": "hi",
            "parentId": None,
            "mentions": [],
            "createdAt": "2026-07-13T00:00:00+00:00",
            "deleted": False,
            "deletedBy": None,
        }
        q = MagicMock()
        q.stream.return_value = [c1]
        comments.order_by.return_value.limit.return_value = q
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.get("/timeline/e1/comments", headers=_HEADERS)
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1

    def test_delete_comment_204(self):
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=True)
        csnap.to_dict.return_value = {"authorUid": "u1", "deleted": False}
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="DEC")
            r = client.delete("/timeline/e1/comments/c1", headers=_HEADERS)
        assert r.status_code == 204

    def test_delete_comment_stranger_403(self):
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=True)
        csnap.to_dict.return_value = {"authorUid": "someone_else", "deleted": False}
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.delete("/timeline/e1/comments/c1", headers=_HEADERS)
        assert r.status_code == 403

    def test_delete_comment_missing_404(self):
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=False)
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.delete("/timeline/e1/comments/c1", headers=_HEADERS)
        assert r.status_code == 404

    def test_list_mentionable_200(self):
        entry = _entry()
        db, *_ = _mock_db(entry)
        members = [
            {"uid": "adult1", "name": "Maratona JJ", "nickname": "Maratona",
             "birthDate": "01/01/1990", "photoUrl": None},
        ]
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs, \
             patch.object(TimelineService, "_project_member_docs",
                          return_value=members):
            fs.client.return_value = db
            r = client.get(
                "/timeline/e1/mentionable", headers=_HEADERS, params={"q": "mar"}
            )
        assert r.status_code == 200
        assert [m["uid"] for m in r.json()] == ["adult1"]

    def test_list_mentionable_missing_entry_404(self):
        db, *_ = _mock_db(None)
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.get("/timeline/e1/mentionable", headers=_HEADERS)
        assert r.status_code == 404

    def test_list_comments_missing_entry_404(self):
        db, *_ = _mock_db(None)
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.get("/timeline/e1/comments", headers=_HEADERS)
        assert r.status_code == 404

    def test_list_comments_blocked_entry_403(self):
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, *_ = _mock_db(entry)
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.get("/timeline/e1/comments", headers=_HEADERS)
        assert r.status_code == 403

    def test_list_mentionable_blocked_entry_403(self):
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, *_ = _mock_db(entry)
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.get("/timeline/e1/mentionable", headers=_HEADERS)
        assert r.status_code == 403
