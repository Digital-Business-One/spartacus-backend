from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from firebase_admin import firestore

from app.logging.decorator import log
from app.models.classes import (
    AgeRange,
    ClassCreate,
    ClassOut,
    ClassUpdate,
    ScheduleItem,
)

_DAY_LABELS: dict[str, str] = {
    "mon": "Seg",
    "tue": "Ter",
    "wed": "Qua",
    "thu": "Qui",
    "fri": "Sex",
    "sat": "Sáb",
    "sun": "Dom",
}


def _format_schedule(schedule: list[dict]) -> str:
    """Build human-readable schedule from array of schedule items."""
    if not schedule:
        return ""

    # Group by time range to collapse days
    groups: dict[str, list[str]] = {}
    for item in schedule:
        key = f"{item.get('startTime', '')}–{item.get('endTime', '')}"
        day = _DAY_LABELS.get(item.get("day", ""), item.get("day", ""))
        groups.setdefault(key, []).append(day)

    parts = []
    for time_range, days in groups.items():
        parts.append(f"{'/'.join(days)} {time_range}")
    return " | ".join(parts)


def _parse_schedule(raw: list | dict | None) -> list[dict]:
    """Normalize schedule from Firestore (supports old and new format)."""
    if raw is None:
        return []
    # New format: list of {day, startTime, endTime}
    if isinstance(raw, list):
        return raw
    # Old format: {days: [...], startTime, endTime}
    if isinstance(raw, dict) and "days" in raw:
        return [
            {
                "day": d,
                "startTime": raw.get("startTime", ""),
                "endTime": raw.get("endTime", ""),
            }
            for d in raw["days"]
        ]
    return []


def _resolve_modality_names(
    db, modality_ids: set[str]
) -> dict[str, str]:
    """Batch-resolve modality IDs to names."""
    if not modality_ids:
        return {}
    refs = [
        db.collection("modalities").document(mid)
        for mid in modality_ids
    ]
    docs = db.get_all(refs)
    return {
        doc.id: doc.to_dict().get("name", doc.id)
        for doc in docs
        if doc.exists
    }


def _doc_to_class_out(
    doc, modality_names: dict[str, str], student_count: int = 0
) -> ClassOut:
    data = doc.to_dict()
    age_range_data = data.get("ageRange")
    age_range = AgeRange(**age_range_data) if age_range_data else None

    schedule_raw = _parse_schedule(
        data.get("schedule") or data.get("weeklySchedule")
    )

    modality_id = data.get("modalityId", "")
    modality_name = modality_names.get(
        modality_id, data.get("modality", "")
    )

    schedule_items = [
        ScheduleItem(
            day=s.get("day", ""),
            start_time=s.get("startTime", ""),
            end_time=s.get("endTime", ""),
        )
        for s in schedule_raw
    ]

    return ClassOut(
        id=doc.id,
        name=data["name"],
        modality_id=modality_id,
        modality_name=modality_name,
        schedule=_format_schedule(schedule_raw),
        schedule_items=schedule_items,
        teacher=data.get("teacherName"),
        location=data.get("location"),
        age_range=age_range,
        icon_url=data.get("iconUrl"),
        active=data.get("active", True),
        student_count=student_count,
        attendance_engine_enabled=data.get("attendanceEngineEnabled", False),
        attendance_start_date=data.get("attendanceStartDate"),
    )


def _count_students_by_class(db, class_ids: set[str]) -> dict[str, int]:
    """Return map of class_id -> number of users that have it in their classIds."""
    counts: dict[str, int] = {cid: 0 for cid in class_ids}
    if not class_ids:
        return counts
    # array-contains-any supports up to 30 values; chunk just in case
    ids = list(class_ids)
    for i in range(0, len(ids), 30):
        chunk = ids[i : i + 30]
        docs = (
            db.collection("users")
            .where("classIds", "array_contains_any", chunk)
            .stream()
        )
        for doc in docs:
            for cid in doc.to_dict().get("classIds", []):
                if cid in counts:
                    counts[cid] += 1
    return counts


class ClassService:
    _COLLECTION = "classes"

    @log
    def list_by_project(
        self, project_id: str, include_inactive: bool = False
    ) -> list[ClassOut]:
        db = firestore.client()
        query = db.collection(self._COLLECTION).where(
            "projectId", "==", project_id
        )
        if not include_inactive:
            query = query.where("active", "==", True)
        docs = list(query.stream())

        modality_ids = {
            d.to_dict().get("modalityId", "")
            for d in docs
            if d.to_dict().get("modalityId")
        }
        modality_names = _resolve_modality_names(db, modality_ids)

        class_ids = {doc.id for doc in docs}
        counts = _count_students_by_class(db, class_ids) if include_inactive else {}

        return [
            _doc_to_class_out(doc, modality_names, counts.get(doc.id, 0))
            for doc in docs
        ]

    @log
    def create(self, project_id: str, data: ClassCreate) -> ClassOut:
        db = firestore.client()
        doc_id = f"{project_id}_{data.id}"
        now = datetime.now(timezone.utc).isoformat()

        schedule_raw = [
            {
                "day": s.day,
                "startTime": s.start_time,
                "endTime": s.end_time,
            }
            for s in data.schedule
        ]

        doc_data = {
            "projectId": project_id,
            "name": data.name,
            "modalityId": data.modality_id,
            "schedule": schedule_raw,
            "teacherId": data.teacher_id,
            "teacherName": data.teacher_name,
            "location": data.location,
            "ageRange": (
                data.age_range.model_dump() if data.age_range else None
            ),
            "iconUrl": None,
            "active": True,
            "createdAt": now,
            "attendanceEngineEnabled": data.attendance_engine_enabled,
            "attendanceStartDate": data.attendance_start_date,
        }
        db.collection(self._COLLECTION).document(doc_id).set(doc_data)

        modality_names = _resolve_modality_names(
            db, {data.modality_id}
        )

        return ClassOut(
            id=doc_id,
            name=data.name,
            modality_id=data.modality_id,
            modality_name=modality_names.get(
                data.modality_id, ""
            ),
            schedule=_format_schedule(schedule_raw),
            schedule_items=data.schedule,
            teacher=data.teacher_name,
            location=data.location,
            age_range=data.age_range,
            attendance_engine_enabled=data.attendance_engine_enabled,
            attendance_start_date=data.attendance_start_date,
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
        if data.modality_id is not None:
            updates["modalityId"] = data.modality_id
        if data.schedule is not None:
            updates["schedule"] = [
                {
                    "day": s.day,
                    "startTime": s.start_time,
                    "endTime": s.end_time,
                }
                for s in data.schedule
            ]
        if data.teacher_id is not None:
            updates["teacherId"] = data.teacher_id
        if data.teacher_name is not None:
            updates["teacherName"] = data.teacher_name
        if data.location is not None:
            updates["location"] = data.location
        if data.age_range is not None:
            updates["ageRange"] = data.age_range.model_dump()
        if data.attendance_engine_enabled is not None:
            updates["attendanceEngineEnabled"] = data.attendance_engine_enabled
        if data.attendance_start_date is not None:
            updates["attendanceStartDate"] = data.attendance_start_date

        # Enforce the invariant against the *merged* state (persisted doc +
        # this partial update), not just the payload — a PATCH may enable
        # the engine alone, relying on a start date set in an earlier call.
        existing_data = doc.to_dict()
        final_enabled = updates.get(
            "attendanceEngineEnabled",
            existing_data.get("attendanceEngineEnabled", False),
        )
        final_start_date = updates.get(
            "attendanceStartDate", existing_data.get("attendanceStartDate")
        )
        if final_enabled and not final_start_date:
            raise HTTPException(
                status_code=422,
                detail=(
                    "attendanceStartDate é obrigatória quando "
                    "attendanceEngineEnabled=true"
                ),
            )

        if updates:
            doc_ref.update(updates)

        updated_doc = doc_ref.get()
        updated_data = updated_doc.to_dict()
        mid = updated_data.get("modalityId", "")
        modality_names = _resolve_modality_names(
            db, {mid} if mid else set()
        )
        return _doc_to_class_out(updated_doc, modality_names)

    @log
    def reactivate(self, project_id: str, class_id: str) -> bool:
        """Set active=True. Returns False if class doesn't exist in this project."""
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(class_id)
        doc = doc_ref.get()
        if not doc.exists or doc.to_dict().get("projectId") != project_id:
            return False
        doc_ref.update({"active": True})
        return True

    @log
    def delete_or_deactivate(
        self, project_id: str, class_id: str
    ) -> Optional[str]:
        """
        Hard-delete a class if it has no enrolled students; otherwise mark as
        inactive. Returns:
          - "deleted" if the document was removed
          - "deactivated" if the class has students and was set active=False
          - None if the class doesn't exist in this project
        """
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(class_id)
        doc = doc_ref.get()
        if not doc.exists or doc.to_dict().get("projectId") != project_id:
            return None

        counts = _count_students_by_class(db, {class_id})
        if counts.get(class_id, 0) > 0:
            doc_ref.update({"active": False})
            return "deactivated"

        doc_ref.delete()
        return "deleted"
