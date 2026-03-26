from datetime import datetime, timezone
from typing import Optional

from firebase_admin import firestore

from app.logging.decorator import log
from app.models.classes import AgeRange, ClassCreate, ClassOut, ClassUpdate

_DAY_LABELS: dict[str, str] = {
    "mon": "Seg",
    "tue": "Ter",
    "wed": "Qua",
    "thu": "Qui",
    "fri": "Sex",
    "sat": "Sáb",
    "sun": "Dom",
}


def _format_schedule(weekly_schedule: dict) -> str:
    days = "/".join(_DAY_LABELS.get(d, d) for d in weekly_schedule.get("days", []))
    start = weekly_schedule.get("startTime", "")
    end = weekly_schedule.get("endTime", "")
    return f"{days} {start}–{end}"


def _doc_to_class_out(doc) -> ClassOut:
    data = doc.to_dict()
    age_range_data = data.get("ageRange")
    age_range = AgeRange(**age_range_data) if age_range_data else None
    return ClassOut(
        id=doc.id,
        name=data["name"],
        modality=data["modality"],
        schedule=_format_schedule(data.get("weeklySchedule", {})),
        teacher=data.get("teacherName"),
        age_range=age_range,
        icon_url=data.get("iconUrl"),
    )


class ClassService:
    _COLLECTION = "classes"

    @log
    def list_by_project(self, project_id: str) -> list[ClassOut]:
        db = firestore.client()
        docs = (
            db.collection(self._COLLECTION)
            .where("projectId", "==", project_id)
            .where("active", "==", True)
            .stream()
        )
        return [_doc_to_class_out(doc) for doc in docs]

    @log
    def create(self, project_id: str, data: ClassCreate) -> ClassOut:
        db = firestore.client()
        doc_id = f"{project_id}_{data.id}"
        now = datetime.now(timezone.utc).isoformat()
        doc_data = {
            "projectId": project_id,
            "name": data.name,
            "modality": data.modality,
            "weeklySchedule": {
                "days": data.weekly_schedule.days,
                "startTime": data.weekly_schedule.start_time,
                "endTime": data.weekly_schedule.end_time,
            },
            "teacherId": data.teacher_id,
            "teacherName": data.teacher_name,
            "ageRange": data.age_range.model_dump() if data.age_range else None,
            "iconUrl": None,
            "active": True,
            "createdAt": now,
        }
        db.collection(self._COLLECTION).document(doc_id).set(doc_data)
        return ClassOut(
            id=doc_id,
            name=data.name,
            modality=data.modality,
            schedule=_format_schedule(doc_data["weeklySchedule"]),
            teacher=data.teacher_name,
            age_range=data.age_range,
        )

    @log
    def update(
        self, project_id: str, class_id: str, data: ClassUpdate
    ) -> Optional[ClassOut]:
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(class_id)
        doc = doc_ref.get()
        if not doc.exists or doc.to_dict().get("projectId") != project_id:
            return None

        updates: dict = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.modality is not None:
            updates["modality"] = data.modality
        if data.weekly_schedule is not None:
            updates["weeklySchedule"] = {
                "days": data.weekly_schedule.days,
                "startTime": data.weekly_schedule.start_time,
                "endTime": data.weekly_schedule.end_time,
            }
        if data.teacher_id is not None:
            updates["teacherId"] = data.teacher_id
        if data.teacher_name is not None:
            updates["teacherName"] = data.teacher_name
        if data.age_range is not None:
            updates["ageRange"] = data.age_range.model_dump()

        if updates:
            doc_ref.update(updates)

        updated_doc = doc_ref.get()
        return _doc_to_class_out(updated_doc)

    @log
    def deactivate(self, project_id: str, class_id: str) -> bool:
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(class_id)
        doc = doc_ref.get()
        if not doc.exists or doc.to_dict().get("projectId") != project_id:
            return False
        doc_ref.update({"active": False})
        return True
