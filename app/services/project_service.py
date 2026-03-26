from datetime import datetime, timezone
from typing import Optional

from firebase_admin import firestore

from app.logging.decorator import log
from app.models.project import MyProjectOut, ProjectCreate, ProjectOut, ProjectUpdate


class ProjectService:
    _COLLECTION = "projects"
    _MEMBERSHIPS = "memberships"

    @log
    def list_all(self) -> list[ProjectOut]:
        db = firestore.client()
        docs = db.collection(self._COLLECTION).stream()
        return [ProjectOut(**d.to_dict()) for d in docs if d.exists]

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

    @log
    def update(self, project_id: str, data: ProjectUpdate) -> Optional[ProjectOut]:
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(project_id)
        doc = doc_ref.get()
        if not doc.exists:
            return None
        updates = data.model_dump(exclude_none=True)
        if updates:
            doc_ref.update(updates)
        current = {**doc.to_dict(), **updates}
        return ProjectOut(**current)

    @log
    def list_by_user(self, user_id: str) -> list[MyProjectOut]:
        db = firestore.client()
        memberships = (
            db.collection(self._MEMBERSHIPS)
            .where("userId", "==", user_id)
            .where("status", "==", "active")
            .stream()
        )
        membership_map: dict[str, list[str]] = {}
        for m in memberships:
            d = m.to_dict()
            membership_map[d["projectId"]] = d.get("roles", [])

        if not membership_map:
            return []

        refs = [
            db.collection(self._COLLECTION).document(pid)
            for pid in membership_map
        ]
        docs = db.get_all(refs)
        result = []
        for doc in docs:
            if not doc.exists:
                continue
            data = doc.to_dict()
            result.append(
                MyProjectOut(
                    id=data.get("id", doc.id),
                    name=data["name"],
                    logo_url=data.get("logo_url"),
                    roles=membership_map.get(doc.id, []),
                )
            )
        return result
