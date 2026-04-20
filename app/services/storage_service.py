import io
import os

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

        # Public URL (bucket has allUsers objectViewer)
        return (
            f"https://storage.googleapis.com/"
            f"{bucket.name}/{path}"
        )

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
