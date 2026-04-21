"""TimelineService — feed with visibility filtering (RFC-11)."""

from firebase_admin import firestore

from app.domain.enums import STAFF_ROLES, TimelineVisibility
from app.logging.decorator import log
from app.models.timeline import TimelineEntryOut
from app.security.context import AuthContext


class TimelineService:
    _COLLECTION = "timeline_entries"

    @log
    def get_feed(
        self,
        project_id: str,
        user: AuthContext,
        cursor: str | None = None,
        type_filter: str | None = None,
        limit: int = 5,
        acting_as: str | None = None,
    ) -> tuple[list[TimelineEntryOut], str | None]:
        db = firestore.client()

        # Over-fetch to compensate for in-memory visibility filtering
        fetch_limit = limit * 4
        query = (
            db.collection(self._COLLECTION)
            .where("projectId", "==", project_id)
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(fetch_limit)
        )

        if type_filter:
            query = query.where("type", "==", type_filter)

        if cursor:
            query = query.start_after({"createdAt": cursor})

        my_dependents = self._get_dependents(db, user.user_id, project_id)
        is_staff = bool(set(user.roles) & STAFF_ROLES)

        # When acting as a dependent, only show that dependent's entries

        visible = []
        last_created_at = None

        for doc in query.stream():
            entry = doc.to_dict()
            entry["id"] = doc.id

            if not self._is_visible(entry, user.user_id, my_dependents, is_staff):
                continue

            # If acting as dependent, filter to only that dependent's entries
            if acting_as:
                target = entry.get("targetUid")
                if target and target != acting_as:
                    continue

            # Check if user liked this entry
            reaction_ref = (
                db.collection(self._COLLECTION)
                .document(doc.id)
                .collection("reactions")
                .document(user.user_id)
            )
            entry["userLiked"] = reaction_ref.get().exists

            visible.append(self._to_out(entry))
            last_created_at = entry.get("createdAt")

            if len(visible) >= limit:
                break

        next_cursor = last_created_at if len(visible) >= limit else None
        return visible, next_cursor

    @log
    def like(self, entry_id: str, user_id: str) -> None:
        db = firestore.client()
        entry_ref = db.collection(self._COLLECTION).document(entry_id)

        if not entry_ref.get().exists:
            raise LookupError("Timeline entry não encontrada")

        reaction_ref = entry_ref.collection("reactions").document(user_id)
        if reaction_ref.get().exists:
            return  # Already liked — idempotent

        from datetime import datetime, timezone

        reaction_ref.set({
            "type": "like",
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        entry_ref.update({"likesCount": firestore.Increment(1)})

    @log
    def unlike(self, entry_id: str, user_id: str) -> None:
        db = firestore.client()
        entry_ref = db.collection(self._COLLECTION).document(entry_id)
        reaction_ref = entry_ref.collection("reactions").document(user_id)

        if not reaction_ref.get().exists:
            return  # Not liked — idempotent

        reaction_ref.delete()
        entry_ref.update({"likesCount": firestore.Increment(-1)})

    def _get_dependents(
        self, db, user_id: str, project_id: str
    ) -> list[str]:
        """Resolve dependent UIDs for a guardian."""
        user_doc = db.collection("users").document(user_id).get()
        if not user_doc.exists:
            return []
        return user_doc.to_dict().get("dependentUids", [])

    def _is_visible(
        self,
        entry: dict,
        user_id: str,
        dependents: list[str],
        is_staff: bool,
    ) -> bool:
        visibility = entry.get("visibility", "")
        status = entry.get("status")

        # Skip deleted entries
        if status == "deleted":
            return False

        if visibility == TimelineVisibility.PUBLIC:
            return True
        if visibility == TimelineVisibility.STAFF_ONLY:
            return is_staff
        if visibility == TimelineVisibility.PERSONAL_AND_STAFF:
            if is_staff:
                return True
            target = entry.get("targetUid")
            return target == user_id or target in dependents
        return False

    def _to_out(self, entry: dict) -> TimelineEntryOut:
        return TimelineEntryOut(
            id=entry.get("id", ""),
            project_id=entry.get("projectId", ""),
            type=entry.get("type", ""),
            origin=entry.get("origin", ""),
            visibility=entry.get("visibility", ""),
            author_uid=entry.get("authorUid", ""),
            author_name=entry.get("authorName", ""),
            author_roles=entry.get("authorRoles", []),
            target_uid=entry.get("targetUid"),
            target_name=entry.get("targetName"),
            title=entry.get("title"),
            description=entry.get("description"),
            attachments=entry.get("attachments"),
            link_preview=entry.get("linkPreview"),
            event_date=entry.get("eventDate"),
            event_location=entry.get("eventLocation"),
            validation_status=entry.get("validationStatus"),
            validated_by=entry.get("validatedBy"),
            validated_at=entry.get("validatedAt"),
            review_requested=entry.get("reviewRequested", False),
            review_resolved=entry.get("reviewResolved", False),
            likes_count=entry.get("likesCount", 0),
            user_liked=entry.get("userLiked", False),
            turma_name=entry.get("turmaName"),
            modalidade_name=entry.get("modalidadeName"),
            class_date=entry.get("classDate"),
            donation_amount=entry.get("donationAmount"),
            created_at=entry.get("createdAt", ""),
            updated_at=entry.get("updatedAt"),
        )
