"""PostService — CRUD for posts with event emission (RFC-11)."""

from datetime import datetime, timezone

from firebase_admin import firestore

from app.events.models import DomainEvent, PostCreatedPayload
from app.logging.decorator import log
from app.models.post import PostCreate, PostOut, PostUpdate


class PostService:
    _COLLECTION = "posts"

    @log
    def create(
        self,
        project_id: str,
        data: PostCreate,
        author_uid: str,
        author_name: str,
        author_roles: list[str],
    ) -> tuple[PostOut, DomainEvent]:
        db = firestore.client()
        now = datetime.now(timezone.utc).isoformat()

        doc_data = {
            "projectId": project_id,
            "type": data.type,
            "authorUid": author_uid,
            "authorName": author_name,
            "authorRoles": author_roles,
            "title": data.title,
            "description": data.description,
            "attachments": [a.model_dump() for a in data.attachments],
            "linkPreview": (
                data.link_preview.model_dump()
                if data.link_preview
                else None
            ),
            "eventDate": data.event_date,
            "eventEndDate": data.event_end_date,
            "eventLocation": data.event_location,
            "eventCategory": data.event_category,
            "modalityId": data.modality_id,
            "organizer": data.organizer,
            "registrationLink": data.registration_link,
            "status": "active",
            "createdAt": now,
            "updatedAt": None,
        }

        _, doc_ref = db.collection(self._COLLECTION).add(doc_data)
        post_id = doc_ref.id

        event = DomainEvent(
            id="post.created",
            payload=PostCreatedPayload(
                entity_id=post_id,
                source_entity_ref=f"posts/{post_id}",
                source_entity_type="posts",
                type=data.type,
                origin="timeline_wizard",
                title=data.title,
                description=data.description,
                author_uid=author_uid,
                author_name=author_name,
                author_roles=author_roles,
                attachments=doc_data["attachments"] or None,
                link_preview=doc_data["linkPreview"],
                event_date=data.event_date,
                event_end_date=data.event_end_date,
                event_location=data.event_location,
                event_category=data.event_category,
                modality_id=data.modality_id,
                organizer=data.organizer,
                registration_link=data.registration_link,
            ),
        )

        out = PostOut(
            id=post_id,
            project_id=project_id,
            type=data.type,
            author_uid=author_uid,
            author_name=author_name,
            title=data.title,
            description=data.description,
            attachments=doc_data["attachments"],
            link_preview=doc_data["linkPreview"],
            event_date=data.event_date,
            event_end_date=data.event_end_date,
            event_location=data.event_location,
            event_category=data.event_category,
            modality_id=data.modality_id,
            organizer=data.organizer,
            registration_link=data.registration_link,
            status="active",
            created_at=now,
        )
        return out, event

    @log
    def update(
        self,
        project_id: str,
        post_id: str,
        data: PostUpdate,
        actor_uid: str,
    ) -> tuple[PostOut | None, DomainEvent | None]:
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(post_id)
        doc = doc_ref.get()

        if not doc.exists:
            return None, None

        current = doc.to_dict()
        if current.get("projectId") != project_id:
            return None, None
        if current.get("authorUid") != actor_uid:
            return None, None

        now = datetime.now(timezone.utc).isoformat()
        updates = {"updatedAt": now}

        for field, firestore_key in [
            ("title", "title"),
            ("description", "description"),
            ("event_date", "eventDate"),
            ("event_end_date", "eventEndDate"),
            ("event_location", "eventLocation"),
        ]:
            val = getattr(data, field, None)
            if val is not None:
                updates[firestore_key] = val

        if data.attachments is not None:
            updates["attachments"] = [a.model_dump() for a in data.attachments]
        if data.link_preview is not None:
            updates["linkPreview"] = data.link_preview.model_dump()

        doc_ref.update(updates)
        merged = {**current, **updates}

        event = DomainEvent(
            id="post.updated",
            payload=PostCreatedPayload(
                entity_id=post_id,
                source_entity_ref=f"posts/{post_id}",
                source_entity_type="posts",
                type=current["type"],
                origin=current.get("origin", "timeline_wizard"),
                title=merged.get("title", ""),
                description=merged.get("description", ""),
                author_uid=current["authorUid"],
                author_name=current["authorName"],
                author_roles=current.get("authorRoles", []),
                attachments=merged.get("attachments"),
                link_preview=merged.get("linkPreview"),
                event_date=merged.get("eventDate"),
                event_end_date=merged.get("eventEndDate"),
                event_location=merged.get("eventLocation"),
            ),
        )

        out = PostOut(
            id=post_id,
            project_id=project_id,
            type=current["type"],
            author_uid=current["authorUid"],
            author_name=current["authorName"],
            title=merged.get("title", ""),
            description=merged.get("description", ""),
            attachments=merged.get("attachments", []),
            link_preview=merged.get("linkPreview"),
            event_date=merged.get("eventDate"),
            event_end_date=merged.get("eventEndDate"),
            event_location=merged.get("eventLocation"),
            status=merged.get("status", "active"),
            created_at=current.get("createdAt", ""),
            updated_at=now,
        )
        return out, event

    @log
    def delete(
        self,
        project_id: str,
        post_id: str,
        actor_uid: str,
    ) -> DomainEvent | None:
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(post_id)
        doc = doc_ref.get()

        if not doc.exists:
            return None

        current = doc.to_dict()
        if current.get("projectId") != project_id:
            return None
        if current.get("authorUid") != actor_uid:
            return None

        now = datetime.now(timezone.utc).isoformat()
        doc_ref.update({"status": "deleted", "updatedAt": now})

        return DomainEvent(
            id="post.deleted",
            payload=PostCreatedPayload(
                entity_id=post_id,
                source_entity_ref=f"posts/{post_id}",
                source_entity_type="posts",
                type=current["type"],
                origin=current.get("origin", "timeline_wizard"),
                title=current.get("title", ""),
                description=current.get("description", ""),
                author_uid=current["authorUid"],
                author_name=current["authorName"],
                author_roles=current.get("authorRoles", []),
            ),
        )
