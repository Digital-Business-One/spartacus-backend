from datetime import datetime, timezone
from typing import Optional

from firebase_admin import firestore

from app.logging.decorator import log
from app.models.modality import ModalityCreate, ModalityOut, ModalityUpdate


class ModalityService:
    _COLLECTION = "modalities"

    @log
    def list_by_project(self, project_id: str) -> list[ModalityOut]:
        db = firestore.client()
        docs = (
            db.collection(self._COLLECTION)
            .where("projectId", "==", project_id)
            .where("active", "==", True)
            .stream()
        )
        return [self._doc_to_out(doc) for doc in docs]

    @log
    def create(
        self, project_id: str, data: ModalityCreate
    ) -> ModalityOut:
        db = firestore.client()
        doc_id = f"{project_id}_{data.slug}"
        now = datetime.now(timezone.utc).isoformat()
        db.collection(self._COLLECTION).document(doc_id).set({
            "projectId": project_id,
            "name": data.name,
            "slug": data.slug,
            "iconUrl": None,
            "active": True,
            "createdAt": now,
        })
        return ModalityOut(
            id=doc_id,
            name=data.name,
            slug=data.slug,
        )

    @log
    def update(
        self, project_id: str, modality_id: str, data: ModalityUpdate
    ) -> Optional[ModalityOut]:
        db = firestore.client()
        ref = db.collection(self._COLLECTION).document(modality_id)
        doc = ref.get()
        if not doc.exists:
            return None
        if doc.to_dict().get("projectId") != project_id:
            return None

        updates: dict = {}
        if data.name is not None:
            updates["name"] = data.name
        if updates:
            ref.update(updates)

        return self._doc_to_out(ref.get())

    @log
    def deactivate(
        self, project_id: str, modality_id: str
    ) -> bool:
        db = firestore.client()
        ref = db.collection(self._COLLECTION).document(modality_id)
        doc = ref.get()
        if not doc.exists:
            return False
        if doc.to_dict().get("projectId") != project_id:
            return False
        ref.update({"active": False})
        return True

    @staticmethod
    def _doc_to_out(doc) -> ModalityOut:
        data = doc.to_dict()
        return ModalityOut(
            id=doc.id,
            name=data["name"],
            slug=data.get("slug", ""),
            icon_url=data.get("iconUrl"),
        )
