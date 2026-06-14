"""Tests for timeline pin — single pinned entry per project (toggle)."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.timeline_service import TimelineService


def _make_db(entry_project_id="spartacus", entry_exists=True, current_pin=None):
    """Build a firestore mock routing by collection name.

    Returns (db, proj_ref) so tests can assert what was written to the
    project doc via proj_ref.set.
    """
    db = MagicMock()

    entry_doc = MagicMock()
    entry_doc.exists = entry_exists
    entry_doc.to_dict.return_value = {"projectId": entry_project_id}
    entry_ref = MagicMock()
    entry_ref.get.return_value = entry_doc

    proj_doc = MagicMock()
    proj_doc.exists = True
    proj_doc.to_dict.return_value = {"pinnedTimelineEntryId": current_pin}
    proj_ref = MagicMock()
    proj_ref.get.return_value = proj_doc

    def collection(name):
        col = MagicMock()
        if name == "timeline_entries":
            col.document.return_value = entry_ref
        elif name == "projects":
            col.document.return_value = proj_ref
        return col

    db.collection.side_effect = collection
    return db, proj_ref


def test_pin_sets_entry():
    db, proj_ref = _make_db(current_pin=None)
    with patch("app.services.timeline_service.firestore") as fs:
        fs.client.return_value = db
        result = TimelineService().toggle_pin("spartacus", "post_1")
    assert result is True
    proj_ref.set.assert_called_once_with(
        {"pinnedTimelineEntryId": "post_1"}, merge=True
    )


def test_pin_replaces_previous():
    db, proj_ref = _make_db(current_pin="post_old")
    with patch("app.services.timeline_service.firestore") as fs:
        fs.client.return_value = db
        result = TimelineService().toggle_pin("spartacus", "post_new")
    assert result is True
    proj_ref.set.assert_called_once_with(
        {"pinnedTimelineEntryId": "post_new"}, merge=True
    )


def test_pin_same_entry_unpins():
    db, proj_ref = _make_db(current_pin="post_1")
    with patch("app.services.timeline_service.firestore") as fs:
        fs.client.return_value = db
        result = TimelineService().toggle_pin("spartacus", "post_1")
    assert result is False
    proj_ref.set.assert_called_once_with(
        {"pinnedTimelineEntryId": None}, merge=True
    )


def test_pin_unknown_entry_raises():
    db, _ = _make_db(entry_exists=False)
    with patch("app.services.timeline_service.firestore") as fs:
        fs.client.return_value = db
        with pytest.raises(LookupError):
            TimelineService().toggle_pin("spartacus", "missing")


def test_pin_other_project_entry_raises():
    db, _ = _make_db(entry_project_id="other")
    with patch("app.services.timeline_service.firestore") as fs:
        fs.client.return_value = db
        with pytest.raises(LookupError):
            TimelineService().toggle_pin("spartacus", "post_1")
