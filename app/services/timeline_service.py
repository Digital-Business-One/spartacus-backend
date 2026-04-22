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

        collected: list[dict] = []
        last_created_at: str | None = None

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

            collected.append(entry)
            last_created_at = entry.get("createdAt")

            if len(collected) >= limit:
                break

        # Batch-fetch photos for all authors + targets in a single request.
        # Timeline entries only denormalize name/roles, not photo — resolving
        # live keeps the feed fresh when a user updates their avatar.
        photo_uids: set[str] = set()
        for e in collected:
            if e.get("authorUid"):
                photo_uids.add(e["authorUid"])
            if e.get("targetUid"):
                photo_uids.add(e["targetUid"])
        photos = self._fetch_user_photos(db, photo_uids)

        visible = [self._to_out(e, photos) for e in collected]
        next_cursor = last_created_at if len(visible) >= limit else None
        return visible, next_cursor

    def _fetch_user_photos(
        self, db, uids: set[str]
    ) -> dict[str, str]:
        """Return ``{uid: photoUrl}`` for the given uids (skipping empty)."""
        if not uids:
            return {}
        refs = [db.collection("users").document(uid) for uid in uids]
        docs = db.get_all(refs)
        out: dict[str, str] = {}
        for doc in docs:
            if not doc.exists:
                continue
            url = doc.to_dict().get("photoUrl")
            if url:
                out[doc.id] = url
        return out

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

    @log
    def list_reactions(
        self, entry_id: str, project_id: str
    ) -> list[dict]:
        """Return users who liked the entry, with their name + primary role.

        Role is resolved from the user's membership in the current project.
        If no membership is found, role is left empty.
        """
        db = firestore.client()
        entry_ref = db.collection(self._COLLECTION).document(entry_id)
        if not entry_ref.get().exists:
            raise LookupError("Timeline entry não encontrada")

        reaction_docs = list(entry_ref.collection("reactions").stream())
        if not reaction_docs:
            return []

        uids = [doc.id for doc in reaction_docs]
        # Batch fetch users
        user_refs = [db.collection("users").document(uid) for uid in uids]
        user_docs = db.get_all(user_refs)
        users_by_uid: dict[str, dict] = {
            doc.id: doc.to_dict()
            for doc in user_docs
            if doc.exists
        }
        # Batch fetch memberships for role resolution
        membership_refs = [
            db.collection("memberships").document(f"{project_id}_{uid}")
            for uid in uids
        ]
        membership_docs = db.get_all(membership_refs)
        roles_by_uid: dict[str, str] = {}
        for doc in membership_docs:
            if not doc.exists:
                continue
            # Membership doc id is "{projectId}_{userId}" — recover uid
            uid = doc.id.removeprefix(f"{project_id}_")
            roles = doc.to_dict().get("roles", []) or []
            roles_by_uid[uid] = roles[0] if roles else ""

        result: list[dict] = []
        for uid in uids:
            udata = users_by_uid.get(uid, {})
            result.append({
                "uid": uid,
                "name": udata.get("name", "Usuário"),
                "role": roles_by_uid.get(uid, ""),
                "photo_url": udata.get("photoUrl"),
            })
        # Sort by name for a stable UI
        result.sort(key=lambda u: u["name"].lower())
        return result

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

    def _to_out(
        self,
        entry: dict,
        photos: dict[str, str] | None = None,
    ) -> TimelineEntryOut:
        photos = photos or {}
        author_uid = entry.get("authorUid", "")
        target_uid = entry.get("targetUid")
        return TimelineEntryOut(
            id=entry.get("id", ""),
            project_id=entry.get("projectId", ""),
            type=entry.get("type", ""),
            origin=entry.get("origin", ""),
            visibility=entry.get("visibility", ""),
            author_uid=author_uid,
            author_name=entry.get("authorName", ""),
            author_roles=entry.get("authorRoles", []),
            author_photo_url=photos.get(author_uid),
            target_uid=target_uid,
            target_name=entry.get("targetName"),
            target_photo_url=photos.get(target_uid) if target_uid else None,
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
            roles_label=entry.get("rolesLabel"),
            classes=entry.get("classes"),
            guardian_name=entry.get("guardianName"),
            created_at=entry.get("createdAt", ""),
            updated_at=entry.get("updatedAt"),
        )
