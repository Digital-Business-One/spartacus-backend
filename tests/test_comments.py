"""Tests for TimelineService comment methods (add/list/delete) + can_view_entry."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models.comment import CommentCreate, CommentUpdate
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


@pytest.fixture(autouse=True)
def _default_moderation_none():
    """`ModerationService.get_level` reads via its own `firestore.client()`
    (a separate import binding from `_FS`, which only patches the one used
    by TimelineService) — so it is NOT covered by the `_mock_db` fixture's
    "moderation" collection unless a test also patches
    `app.services.moderation_service.firestore` directly.

    Default every test to an unmoderated caller (`level == "none"`) here so
    the existing `add_comment` tests keep passing without each needing to
    patch `ModerationService` individually. The two enforcement tests below
    override this by patching `app.services.timeline_service.ModerationService`
    wholesale, which takes precedence over this fixture.
    """
    mod_ref = MagicMock()
    mod_ref.get.return_value = MagicMock(exists=False)
    mod_col = MagicMock()
    mod_col.document.return_value = mod_ref
    mod_db = MagicMock()
    mod_db.collection.side_effect = (
        lambda name: mod_col if name == "moderation" else MagicMock()
    )
    with patch("app.services.moderation_service.firestore") as fs:
        fs.client.return_value = mod_db
        yield


def _ctx(uid="u1", roles=None):
    return AuthContext(
        user_id=uid,
        user_email=f"{uid}@test.com",
        project_id="spartacus-artes-marciais",
        roles=roles or [],
    )


def _entry(**over):
    base = {
        "projectId": _PROJECT_ID,
        "visibility": "public",
        "status": "active",
        "targetUid": None,
        "authorUid": "author1",
    }
    base.update(over)
    return base


def _mock_db(
    entry,
    comment_docs=None,
    user_doc=None,
    parent_doc=None,
    users_map=None,
    memberships_map=None,
):
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
    # parent-comment lookup (reply notifications) — defaults to "not found"
    parent_ref = MagicMock()
    parent_snap = MagicMock(exists=parent_doc is not None)
    parent_snap.to_dict.return_value = parent_doc or {}
    parent_ref.get.return_value = parent_snap
    comments_col.document.return_value = parent_ref
    # entries collection
    entries_col = MagicMock()
    entries_col.document.return_value = entry_ref
    # users collection (author lookup + per-recipient notification lookup)
    default_user = user_doc or {
        "name": "Autor",
        "nickname": None,
        "photoUrl": None,
        "birthDate": "01/01/1990",
    }

    def user_document(uid):
        ref = MagicMock()
        snap = MagicMock(exists=True)
        snap.to_dict.return_value = (users_map or {}).get(uid, default_user)
        ref.get.return_value = snap
        return ref

    users_col = MagicMock()
    users_col.document.side_effect = user_document

    # memberships collection (per-uid visibility checks for mention validation
    # and, historically, staff-role lookups). Doc id is "{projectId}_{uid}".
    memberships_map = memberships_map or {}

    def membership_document(doc_id):
        ref = MagicMock()
        roles = memberships_map.get(doc_id)
        snap = MagicMock(exists=roles is not None)
        snap.to_dict.return_value = {"roles": roles} if roles is not None else {}
        ref.get.return_value = snap
        return ref

    memberships_col = MagicMock()
    memberships_col.document.side_effect = membership_document

    def coll(name):
        return {
            "timeline_entries": entries_col,
            "users": users_col,
            "memberships": memberships_col,
        }.get(name, MagicMock())

    db.collection.side_effect = coll
    return db, entry_ref, comments_col


class TestCommentCreateValidation:
    """MINOR fix: whitespace-only text must be rejected at the model layer
    (422), not pass min_length=1 and become an empty persisted comment."""

    def test_whitespace_only_text_rejected(self):
        with pytest.raises(ValidationError):
            CommentCreate(text="   ")

    def test_text_is_stripped(self):
        assert CommentCreate(text="  hi  ").text == "hi"


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

    def test_comment_blocked_user_cannot_comment(self):
        db, entry_ref, comments = _mock_db(_entry())
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.ModerationService") as Mod:
            fs.client.return_value = db
            Mod.return_value.get_level.return_value = "comment_blocked"
            with pytest.raises(PermissionError):
                TimelineService().add_comment(
                    "e1", _ctx("u1"), CommentCreate(text="oi")
                )
        comments.add.assert_not_called()

    def test_app_banned_user_cannot_comment(self):
        db, entry_ref, comments = _mock_db(_entry())
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.ModerationService") as Mod:
            fs.client.return_value = db
            Mod.return_value.get_level.return_value = "app_banned"
            with pytest.raises(PermissionError):
                TimelineService().add_comment(
                    "e1", _ctx("u1"), CommentCreate(text="oi")
                )
        comments.add.assert_not_called()


class TestMentionValidation:
    """CRITICAL child-safety fix: `mentions[]` is client-supplied and must be
    re-validated server-side on the create path, not just filtered for the
    autocomplete (list_mentionable). A submitted uid only survives if it is
    BOTH an adult AND currently able to view this specific entry."""

    def _entry_and_db(self, **memberships):
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, entry_ref, comments = _mock_db(
            entry,
            users_map={
                "minor_uid": {
                    "name": "Kid", "birthDate": "01/01/2015",
                    "photoUrl": None, "dependentUids": [],
                },
                "outsider_adult_uid": {
                    "name": "Outsider", "birthDate": "01/01/1990",
                    "photoUrl": None, "dependentUids": [],
                },
                "ok_adult_uid": {
                    "name": "Staffer", "birthDate": "01/01/1985",
                    "photoUrl": None, "dependentUids": [],
                },
            },
            memberships_map={
                f"{_PROJECT_ID}_{uid}": roles for uid, roles in memberships.items()
            },
        )
        return entry, db, entry_ref, comments

    def test_minor_and_unauthorized_adult_mentions_are_stripped(self):
        """Only the adult who can view the personal card (here, via a staff
        role) survives — the minor and the outsider adult must not."""
        entry, db, entry_ref, comments = self._entry_and_db(
            ok_adult_uid=["teacher"]
        )
        published: list[tuple[str, str]] = []
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher") as pub:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            pub.publish.side_effect = (
                lambda ev, **k: published.append((ev.id, ev.payload.message))
            )
            out = TimelineService().add_comment(
                "e1", _ctx("commenter", ["teacher"]),
                CommentCreate(
                    text="oi",
                    mentions=["minor_uid", "outsider_adult_uid", "ok_adult_uid"],
                ),
            )
        # Only the adult who can view the card is kept, on the response...
        assert out.mentions == ["ok_adult_uid"]
        # ...and on what was actually persisted to Firestore.
        persisted_doc = comments.add.call_args[0][0]
        assert persisted_doc["mentions"] == ["ok_adult_uid"]
        # Exactly one comment.mention push fired (for ok_adult_uid); the card
        # owner (owner9) also gets a comment.on_card push — nothing else.
        mention_events = [eid for eid, _ in published if eid == "comment.mention"]
        assert len(mention_events) == 1
        assert {eid for eid, _ in published} <= {"comment.mention", "comment.on_card"}

    def test_adult_who_can_view_via_dependent_is_kept(self):
        """A guardian of the card's target can view a personal_and_staff
        card even without a staff role — their mention must survive too."""
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, entry_ref, comments = _mock_db(
            entry,
            users_map={
                "guardian_uid": {
                    "name": "Guardian", "birthDate": "01/01/1980",
                    "photoUrl": None, "dependentUids": ["owner9"],
                },
            },
            # The guardian is a project member (role guardian); their mention
            # survives via dependent-visibility, not a staff role.
            memberships_map={f"{_PROJECT_ID}_guardian_uid": ["guardian"]},
        )
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher"):
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            out = TimelineService().add_comment(
                "e1", _ctx("commenter", ["teacher"]),
                CommentCreate(text="oi", mentions=["guardian_uid"]),
            )
        assert out.mentions == ["guardian_uid"]

    def test_duplicate_mentions_are_deduped(self):
        entry, db, entry_ref, comments = self._entry_and_db(
            ok_adult_uid=["teacher"]
        )
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher"):
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            out = TimelineService().add_comment(
                "e1", _ctx("commenter", ["teacher"]),
                CommentCreate(
                    text="oi", mentions=["ok_adult_uid", "ok_adult_uid"]
                ),
            )
        assert out.mentions == ["ok_adult_uid"]

    def test_nonexistent_mentioned_uid_is_stripped(self):
        entry = _entry(visibility="public")
        db, entry_ref, comments = _mock_db(entry, users_map={})
        # Redefine the users() lookup so that "ghost_uid" resolves to a
        # non-existent user doc, unlike the default author lookup.
        users_col = db.collection("users")

        def user_document(uid):
            ref = MagicMock()
            if uid == "ghost_uid":
                snap = MagicMock(exists=False)
                snap.to_dict.return_value = {}
            else:
                snap = MagicMock(exists=True)
                snap.to_dict.return_value = {
                    "name": "Autor", "photoUrl": None, "birthDate": "01/01/1990",
                }
            ref.get.return_value = snap
            return ref

        users_col.document.side_effect = user_document
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher"):
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            out = TimelineService().add_comment(
                "e1", _ctx("commenter"),
                CommentCreate(text="oi", mentions=["ghost_uid"]),
            )
        assert out.mentions == []

    def test_valid_mention_resolves_display_string(self):
        """Bug fix: a valid mention persists + returns its display (nickname→
        name) so the client can highlight the full '@Nome Completo' span."""
        entry, db, entry_ref, comments = self._entry_and_db(
            ok_adult_uid=["teacher"])
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher"):
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            out = TimelineService().add_comment(
                "e1", _ctx("commenter", ["teacher"]),
                CommentCreate(text="oi @Staffer", mentions=["ok_adult_uid"]),
            )
        assert out.mention_displays == ["Staffer"]
        assert comments.add.call_args[0][0]["mentionDisplays"] == ["Staffer"]

    def test_public_card_non_member_mention_is_stripped(self):
        """Multi-tenant leak fix: on a PUBLIC card, an adult who is NOT a
        member of the token's project must not survive mention validation,
        even though the card itself is publicly visible."""
        entry = _entry(visibility="public")
        # default user doc is an adult; no membership doc for "other_project_uid"
        db, entry_ref, comments = _mock_db(entry, memberships_map={})
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher"):
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            out = TimelineService().add_comment(
                "e1", _ctx("commenter"),
                CommentCreate(text="oi", mentions=["other_project_uid"]),
            )
        assert out.mentions == []


class TestProjectScoping:
    """Multi-tenant guard: comment endpoints load the entry globally by id,
    so an entry belonging to another project must be unreachable regardless
    of visibility."""

    def test_add_comment_cross_project_entry_raises_permission(self):
        entry = _entry(projectId="another-project", visibility="public")
        db, *_ = _mock_db(entry)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().add_comment(
                    "e1", _ctx("u1"), CommentCreate(text="oi"))

    def test_list_comments_cross_project_entry_raises_permission(self):
        entry = _entry(projectId="another-project", visibility="public")
        db, *_ = _mock_db(entry)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().list_comments("e1", _ctx("u1"))

    def test_mentionable_cross_project_entry_raises_permission(self):
        entry = _entry(projectId="another-project", visibility="public")
        db, *_ = _mock_db(entry)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().list_mentionable("e1", _ctx("u1"))


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
            "text": "oi @João da Silva",
            "parentId": None,
            "mentions": ["joao_uid"],
            "mentionDisplays": ["João da Silva"],
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
        assert len(page.items) == 1 and page.items[0].text == "oi @João da Silva"
        # multi-word display round-trips so the client can highlight it
        assert page.items[0].mention_displays == ["João da Silva"]

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

    def test_delete_missing_entry_raises_lookup(self):
        """MINOR fix: delete must also 404 when the parent entry is gone."""
        db, entry_ref, comments = _mock_db(None)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(LookupError):
                TimelineService().delete_comment("e1", "c1", _ctx())

    def test_delete_on_blocked_entry_raises_permission(self):
        """MINOR fix: delete must gate on entry visibility, consistent with
        list/create/mentionable — even the comment's own author can't
        delete a comment on a card they can no longer see (prevents
        404-vs-403 probing on unseen entries)."""
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=True)
        # "stranger" is even the comment's own author — ownership alone must
        # not be enough once entry visibility is enforced.
        csnap.to_dict.return_value = {"authorUid": "stranger", "deleted": False}
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().delete_comment("e1", "c1", _ctx("stranger"))
        cref.update.assert_not_called()

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


class TestCommentNotifications:
    def test_mention_and_owner_events_published(self):
        entry = _entry(authorUid="owner1", targetUid=None)
        db, entry_ref, comments = _mock_db(
            entry, memberships_map={f"{_PROJECT_ID}_adult1": ["student"]})
        published = []
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher") as pub:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            pub.publish.side_effect = lambda ev, **k: published.append(ev.id)
            TimelineService().add_comment(
                "e1", _ctx("commenter"),
                CommentCreate(text="oi @a", mentions=["adult1"]))
        assert "comment.mention" in published
        assert "comment.on_card" in published   # dono do card (owner1) != autor

    def test_reply_notifies_parent_comment_author(self):
        entry = _entry(authorUid="commenter")  # commenter is also card owner
        db, entry_ref, comments = _mock_db(
            entry, parent_doc={"authorUid": "parentAuthor"}
        )
        published = []
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher") as pub:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            pub.publish.side_effect = lambda ev, **k: published.append(ev.id)
            TimelineService().add_comment(
                "e1", _ctx("commenter"),
                CommentCreate(text="valeu!", parent_id="c-parent"))
        # card owner == commenter → no comment.on_card; only the reply event
        assert published == ["comment.reply"]

    def test_commenter_never_notifies_self(self):
        entry = _entry(authorUid="commenter", targetUid=None)
        db, entry_ref, comments = _mock_db(entry)
        published = []
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher") as pub:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            pub.publish.side_effect = lambda ev, **k: published.append(ev.id)
            TimelineService().add_comment(
                "e1", _ctx("commenter"),
                CommentCreate(text="oi", mentions=["commenter"]))
        assert published == []

    def test_dedup_prefers_mention_label_when_owner_also_mentioned(self):
        entry = _entry(authorUid="owner1", targetUid=None)
        db, entry_ref, comments = _mock_db(
            entry, memberships_map={f"{_PROJECT_ID}_owner1": ["student"]})
        published = []
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher") as pub:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            pub.publish.side_effect = lambda ev, **k: published.append(ev.id)
            TimelineService().add_comment(
                "e1", _ctx("commenter"),
                CommentCreate(text="oi @owner", mentions=["owner1"]))
        # owner1 qualifies both as card owner and as mentioned — one push,
        # mention label wins.
        assert published == ["comment.mention"]

    def test_notification_uses_recipient_email_and_author_name(self):
        entry = _entry(authorUid="owner1", targetUid=None)
        db, entry_ref, comments = _mock_db(
            entry,
            user_doc={"name": "Comentarista", "photoUrl": None,
                      "birthDate": "01/01/1990", "nickname": None},
            users_map={"owner1": {"name": "Dono", "email": "dono@test.com"}},
        )
        published_payloads = []
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher") as pub:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            pub.publish.side_effect = (
                lambda ev, **k: published_payloads.append(ev.payload)
            )
            TimelineService().add_comment(
                "e1", _ctx("commenter"), CommentCreate(text="oi"))
        assert len(published_payloads) == 1
        payload = published_payloads[0]
        assert payload.to == "dono@test.com"
        assert payload.name == "Dono"
        assert payload.message == "Comentarista: oi"

    def test_notification_failure_never_breaks_comment_creation(self):
        """Task 6 fix: a Firestore error inside _notify_comment (the
        per-recipient user lookup) must be swallowed — the comment write and
        commentsCount increment already happened and must not be undone or
        surfaced as a hard error to the caller.

        The author lookup (uid="commenter", ~line 302 of add_comment, BEFORE
        the comment is persisted) succeeds normally; only the recipient
        lookup inside _notify_comment (uid="owner1", the card owner) raises.
        """
        entry = _entry(authorUid="owner1", targetUid=None)
        db, entry_ref, comments = _mock_db(entry)

        author_snap = MagicMock(exists=True)
        author_snap.to_dict.return_value = {"name": "Comentarista", "photoUrl": None}

        def users_document(uid):
            if uid == "commenter":
                ref = MagicMock()
                ref.get.return_value = author_snap
                return ref
            raise RuntimeError("boom: transient Firestore error")

        entries_col = MagicMock()
        entries_col.document.return_value = entry_ref
        users_col = MagicMock()
        users_col.document.side_effect = users_document

        def coll(name):
            return {"timeline_entries": entries_col, "users": users_col}.get(
                name, MagicMock()
            )

        db.collection.side_effect = coll

        with patch(_FS) as fs, \
             patch("app.services.timeline_service.publisher") as pub:
            fs.client.return_value = db
            fs.Increment = MagicMock(return_value="INC")
            out = TimelineService().add_comment(
                "e1", _ctx("commenter"), CommentCreate(text="oi")
            )
        assert out.text == "oi"
        assert out.id == "c-new"
        comments.add.assert_called_once()
        entry_ref.update.assert_called_with({"commentsCount": "INC"})
        pub.publish.assert_not_called()


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

    def test_post_whitespace_only_text_422(self):
        """MINOR fix: a whitespace-only text must be rejected at validation
        time (422), not silently stripped into an empty comment that still
        increments commentsCount."""
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.post(
                "/timeline/e1/comments", headers=_HEADERS, json={"text": "   "}
            )
        assert r.status_code == 422
        comments.add.assert_not_called()
        entry_ref.update.assert_not_called()

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

    def test_delete_comment_blocked_entry_403(self):
        """MINOR fix: delete gates on entry visibility at the route level too."""
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, entry_ref, comments = _mock_db(entry)
        cref = MagicMock()
        csnap = MagicMock(exists=True)
        csnap.to_dict.return_value = {"authorUid": "u1", "deleted": False}
        cref.get.return_value = csnap
        comments.document.return_value = cref
        with patch(_VERIFY, return_value=_VALID_CLAIMS), patch(_FS) as fs:
            fs.client.return_value = db
            r = client.delete("/timeline/e1/comments/c1", headers=_HEADERS)
        assert r.status_code == 403

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


class TestEditComment:
    def _comment_doc(self, author="u1", deleted=False):
        return {
            "authorUid": author,
            "authorName": "A",
            "authorPhotoUrl": None,
            "text": "original",
            "parentId": None,
            "mentions": [],
            "mentionDisplays": [],
            "createdAt": "2026-07-13T00:00:00+00:00",
            "deleted": deleted,
            "deletedBy": None,
        }

    def _db_with_comment(self, entry, comment, **mock_db_kwargs):
        db, entry_ref, comments = _mock_db(entry, **mock_db_kwargs)
        cref = MagicMock()
        csnap = MagicMock(exists=comment is not None)
        csnap.to_dict.return_value = comment or {}
        cref.get.return_value = csnap
        comments.document.return_value = cref
        return db, entry_ref, comments, cref

    def test_author_edits_own_comment(self):
        db, entry_ref, comments, cref = self._db_with_comment(
            _entry(), self._comment_doc())
        with patch(_FS) as fs:
            fs.client.return_value = db
            out = TimelineService().edit_comment(
                "e1", "c1", _ctx("u1"), CommentUpdate(text="novo texto"))
        assert out.text == "novo texto"
        assert out.edited_at is not None
        updated = cref.update.call_args[0][0]
        assert updated["text"] == "novo texto"
        assert updated["editedAt"] == out.edited_at
        # created_at preservado; nenhuma notificação em edição
        assert out.created_at == "2026-07-13T00:00:00+00:00"

    def test_staff_cannot_edit_others_comment(self):
        db, *_ = self._db_with_comment(
            _entry(), self._comment_doc(author="someone_else"))
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().edit_comment(
                    "e1", "c1", _ctx("staff", ["teacher"]),
                    CommentUpdate(text="x"))

    def test_deleted_comment_raises_lookup(self):
        db, *_ = self._db_with_comment(
            _entry(), self._comment_doc(deleted=True))
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(LookupError):
                TimelineService().edit_comment(
                    "e1", "c1", _ctx("u1"), CommentUpdate(text="x"))

    def test_missing_comment_raises_lookup(self):
        db, *_ = self._db_with_comment(_entry(), None)
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(LookupError):
                TimelineService().edit_comment(
                    "e1", "c1", _ctx("u1"), CommentUpdate(text="x"))

    def test_missing_entry_raises_lookup(self):
        db, *_ = self._db_with_comment(None, self._comment_doc())
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(LookupError):
                TimelineService().edit_comment(
                    "e1", "c1", _ctx("u1"), CommentUpdate(text="x"))

    def test_blocked_entry_raises_permission(self):
        entry = _entry(visibility="personal_and_staff", targetUid="owner9")
        db, *_ = self._db_with_comment(entry, self._comment_doc(author="stranger"))
        with patch(_FS) as fs:
            fs.client.return_value = db
            with pytest.raises(PermissionError):
                TimelineService().edit_comment(
                    "e1", "c1", _ctx("stranger"), CommentUpdate(text="x"))

    def test_moderated_author_cannot_edit(self):
        db, *_ = self._db_with_comment(_entry(), self._comment_doc())
        with patch(_FS) as fs, \
             patch("app.services.timeline_service.ModerationService") as Mod:
            fs.client.return_value = db
            Mod.return_value.get_level.return_value = "comment_blocked"
            with pytest.raises(PermissionError):
                TimelineService().edit_comment(
                    "e1", "c1", _ctx("u1"), CommentUpdate(text="x"))

    def test_mentions_revalidated_on_edit(self):
        """Menção a menor enviada na edição é descartada (mesma regra do POST)."""
        db, entry_ref, comments, cref = self._db_with_comment(
            _entry(), self._comment_doc(),
            users_map={
                "minor_uid": {"name": "Kid", "birthDate": "01/01/2015",
                              "photoUrl": None, "dependentUids": []},
            },
            memberships_map={f"{_PROJECT_ID}_minor_uid": ["student"]},
        )
        with patch(_FS) as fs:
            fs.client.return_value = db
            out = TimelineService().edit_comment(
                "e1", "c1", _ctx("u1"),
                CommentUpdate(text="oi @Kid", mentions=["minor_uid"]))
        assert out.mentions == []
        assert cref.update.call_args[0][0]["mentions"] == []

    def test_whitespace_only_text_rejected(self):
        with pytest.raises(ValidationError):
            CommentUpdate(text="   ")


class TestListEditedAt:
    def test_list_returns_edited_at(self):
        entry = _entry()
        db, entry_ref, comments = _mock_db(entry)
        c1 = MagicMock()
        c1.id = "c1"
        c1.to_dict.return_value = {
            "authorUid": "u1", "authorName": "A", "authorPhotoUrl": None,
            "text": "hi", "parentId": None, "mentions": [],
            "mentionDisplays": [], "createdAt": "2026-07-13T00:00:00+00:00",
            "editedAt": "2026-07-16T00:00:00+00:00",
            "deleted": False, "deletedBy": None,
        }
        q = MagicMock()
        q.stream.return_value = [c1]
        comments.order_by.return_value.limit.return_value = q
        with patch(_FS) as fs:
            fs.client.return_value = db
            page = TimelineService().list_comments("e1", _ctx("u1"))
        assert page.items[0].edited_at == "2026-07-16T00:00:00+00:00"
