from firebase_admin import firestore

from app.logging.decorator import log
from app.models.classes import AgeRange, ClassOut

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
        result = []
        for doc in docs:
            data = doc.to_dict()
            age_range_data = data.get("ageRange")
            age_range = AgeRange(**age_range_data) if age_range_data else None
            result.append(
                ClassOut(
                    id=doc.id,
                    name=data["name"],
                    modality=data["modality"],
                    schedule=_format_schedule(data.get("weeklySchedule", {})),
                    teacher=data.get("teacherName"),
                    age_range=age_range,
                    icon_url=data.get("iconUrl"),
                )
            )
        return result
