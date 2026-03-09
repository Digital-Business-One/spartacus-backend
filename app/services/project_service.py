from datetime import datetime, timezone
from typing import Optional

from firebase_admin import firestore

from app.logging.decorator import log
from app.models.project import ProjectCreate, ProjectOut


class ProjectService:
    _COLLECTION = "projects"

    @log
    def create(self, data: ProjectCreate) -> ProjectOut:
        db = firestore.client()
        now = datetime.now(timezone.utc).isoformat()
        doc_data = {**data.model_dump(), "is_root": False, "created_at": now}
        db.collection(self._COLLECTION).document(data.id).set(doc_data)
        return ProjectOut(**doc_data)

    @log
    def get(self, project_id: str) -> Optional[ProjectOut]:
        db = firestore.client()
        doc = db.collection(self._COLLECTION).document(project_id).get()
        if not doc.exists:
            return None
        return ProjectOut(**doc.to_dict())
