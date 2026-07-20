import io
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from firebase_admin import storage
from PIL import Image

from app.logging.decorator import log

_MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/jpg"}
_MIN_DIMENSION = 200
_TARGET_SIZE = (400, 400)
_JPEG_QUALITY = 80


def _get_bucket():
    bucket_name = os.getenv(
        "FIREBASE_STORAGE_BUCKET",
        "spartacus-artes-marciais.firebasestorage.app",
    )
    return storage.bucket(bucket_name)


def build_blob_public_url(bucket_name: str, blob_name: str) -> str:
    """Return a public URL for ``blob_name`` that works in both prod and local.

    - Prod: canonical ``https://storage.googleapis.com/{bucket}/{path}``.
      Requires the bucket to have ``allUsers`` objectViewer at IAM level
      (our setup does).
    - Local (Storage emulator): the public URL from prod does not resolve,
      so return the emulator's Firebase Storage REST URL. The host is
      rewritten so it works when served to the browser (internal compose
      hostnames like ``emulators:9199`` are replaced with ``localhost``).
    """
    emulator_host = os.getenv("FIREBASE_STORAGE_EMULATOR_HOST")
    if emulator_host:
        # Docker-compose internal hostname → browser-reachable host
        public_host = emulator_host.replace("emulators:", "localhost:")
        encoded = quote(blob_name, safe="")
        return (
            f"http://{public_host}/v0/b/{bucket_name}"
            f"/o/{encoded}?alt=media"
        )
    return f"https://storage.googleapis.com/{bucket_name}/{blob_name}"


@log
async def upload_media_attachment(
    *,
    uid: str,
    file: UploadFile,
    prefix: str,
    allowed_types: set[str],
    max_size: int,
    invalid_type_status: int = 400,
    oversize_status: int = 400,
) -> dict:
    """Validate + upload a generic media attachment, returning the shared
    `{type, url, name, size}` shape.

    Shared by `POST /posts/upload` and `POST /attendance/justification-upload`
    — callers differ only in prefix, allowed content-types, size limit and
    the HTTP status used for validation failures (posts keeps its original
    400/400; attendance uploads use 415/413 per the design spec).
    """
    content_type = (file.content_type or "").lower()
    if content_type == "image/jpg":
        content_type = "image/jpeg"
    if content_type not in allowed_types:
        raise HTTPException(
            status_code=invalid_type_status,
            detail=f"Tipo de arquivo não permitido: {content_type}",
        )

    contents = await file.read()
    if len(contents) > max_size:
        raise HTTPException(
            status_code=oversize_status,
            detail=f"Arquivo muito grande (max {max_size // (1024 * 1024)} MB)",
        )

    ext = (file.filename or "file").rsplit(".", 1)[-1] if file.filename else "bin"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    blob_name = f"{prefix}/{uid}/{ts}_{uuid.uuid4().hex[:8]}.{ext}"

    bucket_name = os.getenv(
        "FIREBASE_STORAGE_BUCKET",
        "spartacus-artes-marciais.firebasestorage.app",
    )
    bucket = storage.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(contents, content_type=content_type)
    # Bucket has allUsers objectViewer at the IAM level (UBLA enabled),
    # so per-object make_public() is redundant and would fail.

    if content_type.startswith("image"):
        att_type = "image"
    elif content_type.startswith("audio"):
        att_type = "voice"
    else:
        att_type = "file"

    return {
        "type": att_type,
        "url": build_blob_public_url(bucket_name, blob_name),
        "name": file.filename or f"attachment.{ext}",
        "size": len(contents),
    }


class StorageService:

    @log
    async def upload_avatar(
        self, uid: str, file: UploadFile
    ) -> str:
        if file.content_type not in _ALLOWED_TYPES:
            raise HTTPException(
                status_code=400,
                detail="Formato inválido. Use JPEG ou PNG.",
            )

        contents = await file.read()
        if len(contents) > _MAX_SIZE_BYTES:
            raise HTTPException(
                status_code=400,
                detail="Arquivo muito grande. Máximo 5 MB.",
            )

        try:
            img = Image.open(io.BytesIO(contents))
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Arquivo não é uma imagem válida.",
            )

        width, height = img.size
        if width < _MIN_DIMENSION or height < _MIN_DIMENSION:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Resolução mínima: {_MIN_DIMENSION}x"
                    f"{_MIN_DIMENSION} px."
                ),
            )

        # Resize to 400x400 (cover crop from center)
        img = self._crop_center_square(img)
        img = img.resize(_TARGET_SIZE, Image.LANCZOS)

        # Convert to JPEG
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
        buf.seek(0)

        # Upload to Firebase Storage
        path = f"profiles/{uid}/avatar.jpg"
        bucket = _get_bucket()
        blob = bucket.blob(path)
        blob.upload_from_file(buf, content_type="image/jpeg")

        return build_blob_public_url(bucket.name, path)

    @log
    def delete_avatar(self, uid: str) -> None:
        path = f"profiles/{uid}/avatar.jpg"
        bucket = _get_bucket()
        blob = bucket.blob(path)
        if blob.exists():
            blob.delete()

    @staticmethod
    def _crop_center_square(img: Image.Image) -> Image.Image:
        width, height = img.size
        side = min(width, height)
        left = (width - side) // 2
        top = (height - side) // 2
        return img.crop((left, top, left + side, top + side))
