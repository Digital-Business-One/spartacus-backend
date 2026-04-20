"""Tests for AccountHistoryService — RFC-12 sub-collection."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app
    from app.services.account_history_service import AccountHistoryService

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_UID = "user123"
_ADMIN_UID = "admin1"
_VERIFY = "app.security.middleware.verify_id_token"
_FS = "app.services.account_history_service.firestore"

_HEADERS = {
    "Authorization": "Bearer tok",
    "X-Project-Id": _PID,
}
_ADMIN_CLAIMS = {
    "uid": _ADMIN_UID,
    "email": "admin@t.com",
    "projects": {_PID: ["owner"]},
}


def _build_history_doc(
    doc_id: str,
    project_id: str = _PID,
    event_type: str = "approval",
    event_subtype: str = "approve",
    description: str = "Conta aprovada por Ana",
    created_at: str = "2026-03-15T10:00:00+00:00",
):
    doc = MagicMock()
    doc.id = doc_id
    doc.to_dict.return_value = {
        "projectId": project_id,
        "eventType": event_type,
        "eventSubtype": event_subtype,
        "actorUid": _ADMIN_UID,
        "actorName": "Ana Souza",
        "actorRoles": ["owner"],
        "description": description,
        "createdAt": created_at,
    }
    return doc


class TestRecord:
    def test_record_creates_document(self):
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc_ref.id = "hist_abc"
        sub = mock_db.collection.return_value.document.return_value
        sub.collection.return_value.document.return_value = mock_doc_ref

        with patch(_FS) as fs:
            fs.client.return_value = mock_db
            new_id = AccountHistoryService().record(
                uid=_UID,
                project_id=_PID,
                event_type="creation",
                event_subtype="password",
                actor_uid=_UID,
                actor_name="João",
                description="Conta criada",
            )

        assert new_id == "hist_abc"
        mock_doc_ref.set.assert_called_once()
        payload = mock_doc_ref.set.call_args[0][0]
        assert payload["projectId"] == _PID
        assert payload["eventType"] == "creation"
        assert payload["eventSubtype"] == "password"
        assert payload["actorUid"] == _UID
        assert payload["actorName"] == "João"
        assert payload["description"] == "Conta criada"
        assert "createdAt" in payload

    def test_record_swallows_exceptions(self):
        """History writes must never break the parent operation."""
        mock_db = MagicMock()
        mock_db.collection.side_effect = RuntimeError("Firestore down")

        with patch(_FS) as fs:
            fs.client.return_value = mock_db
            new_id = AccountHistoryService().record(
                uid=_UID,
                project_id=_PID,
                event_type="creation",
                actor_uid=_UID,
                actor_name="João",
                description="...",
            )

        assert new_id == ""

    def test_record_accepts_injected_db(self):
        """When db is injected, firestore.client() is not called."""
        mock_db = MagicMock()
        mock_doc_ref = MagicMock()
        mock_doc_ref.id = "hist_xyz"
        sub = mock_db.collection.return_value.document.return_value
        sub.collection.return_value.document.return_value = mock_doc_ref

        with patch(_FS) as fs:
            new_id = AccountHistoryService().record(
                uid=_UID,
                project_id=_PID,
                event_type="approval",
                actor_uid=_ADMIN_UID,
                actor_name="Ana",
                description="aprovado",
                db=mock_db,
            )

        assert new_id == "hist_xyz"
        fs.client.assert_not_called()


class TestQuery:
    def test_query_returns_paginated(self):
        mock_db = MagicMock()
        sub_col = MagicMock()
        sub_col.where.return_value.stream.return_value = [
            _build_history_doc("h1", created_at="2026-03-15T10:00:00+00:00"),
            _build_history_doc("h2", created_at="2026-03-10T10:00:00+00:00"),
            _build_history_doc("h3", created_at="2026-02-15T10:00:00+00:00"),
        ]
        users_doc = mock_db.collection.return_value.document.return_value
        users_doc.collection.return_value = sub_col

        with patch(_FS) as fs:
            fs.client.return_value = mock_db
            page = AccountHistoryService().query(
                uid=_UID, project_id=_PID, page=1, page_size=2,
            )

        assert page.total == 3
        assert page.page == 1
        assert page.page_size == 2
        assert page.total_pages == 2
        assert len(page.items) == 2
        # Sorted newest first
        assert page.items[0].id == "h1"
        assert page.items[1].id == "h2"

    def test_query_filters_by_year_and_month(self):
        mock_db = MagicMock()
        sub_col = MagicMock()
        sub_col.where.return_value.stream.return_value = [
            _build_history_doc("h1", created_at="2026-03-15T10:00:00+00:00"),
            _build_history_doc("h2", created_at="2026-04-10T10:00:00+00:00"),
            _build_history_doc("h3", created_at="2025-03-15T10:00:00+00:00"),
        ]
        users_doc = mock_db.collection.return_value.document.return_value
        users_doc.collection.return_value = sub_col

        with patch(_FS) as fs:
            fs.client.return_value = mock_db
            page = AccountHistoryService().query(
                uid=_UID, project_id=_PID, year=2026, months=[3],
            )

        assert page.total == 1
        assert page.items[0].id == "h1"

    def test_query_filters_by_types(self):
        mock_db = MagicMock()
        sub_col = MagicMock()
        # When `types` is given, .where() is called twice (projectId + types)
        type_filtered_query = MagicMock()
        type_filtered_query.stream.return_value = [
            _build_history_doc(
                "h1",
                event_type="suspension",
                event_subtype="expel",
                created_at="2026-03-15T10:00:00+00:00",
            ),
        ]
        sub_col.where.return_value.where.return_value = type_filtered_query
        users_doc = mock_db.collection.return_value.document.return_value
        users_doc.collection.return_value = sub_col

        with patch(_FS) as fs:
            fs.client.return_value = mock_db
            page = AccountHistoryService().query(
                uid=_UID, project_id=_PID, types=["suspension"],
            )

        assert page.total == 1
        assert page.items[0].event_type == "suspension"
