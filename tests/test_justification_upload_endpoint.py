"""TDD tests for Task B4 — `POST /attendance/justification-upload`.

Multipart upload of a justification attachment (jpeg/png/webp/pdf, <=10MB),
stored at `justifications/{uid}/{ts}_{rand}.{ext}` via the shared
`storage_service.upload_media_attachment` helper (also used by
`POST /posts/upload`). Gate: any authenticated project member — no `social`
role required (a plain student/guardian must be able to attach an atestado).

See: docs/superpowers/specs/2026-07-18-justificativa-faltas-design.md
(section "Endpoints" -> POST /attendance/justification-upload).
"""

import io
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

from fastapi.testclient import TestClient

with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app, raise_server_exceptions=False)

_PID = "spartacus"
_UID = "student1"

_VERIFY = "app.security.middleware.verify_id_token"
_STORAGE = "app.services.storage_service"

_HEADERS = {"Authorization": "Bearer tok", "X-Project-Id": _PID}
# Plain student — no "social" role. Gate for this endpoint must not require it.
_STUDENT_CLAIMS = {
    "uid": _UID, "email": "s@t.com", "projects": {_PID: ["student"]},
}


def _upload(filename: str, content: bytes, content_type: str):
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_bucket.name = "test-bucket"

    with (
        patch(_VERIFY, return_value=_STUDENT_CLAIMS),
        patch(f"{_STORAGE}.storage") as mock_storage,
    ):
        mock_storage.bucket.return_value = mock_bucket
        resp = client.post(
            "/attendance/justification-upload",
            headers=_HEADERS,
            files={"file": (filename, io.BytesIO(content), content_type)},
        )
    return resp, mock_blob


class TestJustificationUploadEndpoint:
    def test_uploads_valid_jpeg(self):
        resp, mock_blob = _upload("atestado.jpg", b"fake-jpeg-bytes", "image/jpeg")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "image"
        assert body["name"] == "atestado.jpg"
        assert body["size"] == len(b"fake-jpeg-bytes")
        assert "justifications/student1/" in unquote(body["url"])
        mock_blob.upload_from_string.assert_called_once()

    def test_uploads_valid_png(self):
        resp, _ = _upload("atestado.png", b"fake-png-bytes", "image/png")
        assert resp.status_code == 200, resp.text
        assert resp.json()["type"] == "image"

    def test_uploads_valid_webp(self):
        resp, _ = _upload("atestado.webp", b"fake-webp-bytes", "image/webp")
        assert resp.status_code == 200, resp.text
        assert resp.json()["type"] == "image"

    def test_uploads_valid_pdf(self):
        resp, _ = _upload("atestado.pdf", b"%PDF-fake-bytes", "application/pdf")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "file"
        assert "justifications/student1/" in unquote(body["url"])

    def test_rejects_invalid_content_type(self):
        resp, _ = _upload("clip.mp4", b"fake-video-bytes", "video/mp4")
        assert resp.status_code == 415

    def test_rejects_oversized_file(self):
        big_data = b"x" * (11 * 1024 * 1024)  # 11 MB
        resp, _ = _upload("big.jpg", big_data, "image/jpeg")
        assert resp.status_code == 413

    def test_plain_student_can_upload_no_social_role_required(self):
        # Sanity re-assertion: _STUDENT_CLAIMS above intentionally carries
        # only the "student" role — this must NOT 403 like /posts/upload
        # would for a non-"social" member.
        resp, _ = _upload("atestado.jpg", b"fake-jpeg-bytes", "image/jpeg")
        assert resp.status_code == 200, resp.text
