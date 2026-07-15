"""TimelineService — feed with visibility filtering (RFC-11)."""

from datetime import datetime, timezone

import structlog
from firebase_admin import firestore

from app.domain.age import is_adult
from app.domain.enums import STAFF_ROLES, TimelineVisibility
from app.events import publisher
from app.events.models import AccountNotificationPayload, DomainEvent
from app.logging.decorator import log
from app.models.comment import CommentCreate, CommentOut, CommentsPage, MentionableOut
from app.models.timeline import TimelineEntryOut
from app.security.context import AuthContext
from app.services.moderation_service import ModerationService

logger = structlog.get_logger()

_COMMENT_NOTIFICATION_TITLES = {
    "comment.mention": "Você foi mencionado",
    "comment.reply": "Responderam você",
    "comment.on_card": "Novo comentário",
}

# Alias kept for parity with the RFC-11 task brief naming.
_STAFF_ROLES = STAFF_ROLES


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

        # Pinned entry is resolved (and prepended) only on the first page.
        pinned_id: str | None = None
        if not cursor:
            proj = db.collection("projects").document(project_id).get()
            if proj.exists:
                pinned_id = proj.to_dict().get("pinnedTimelineEntryId") or None

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

            # The pinned entry is prepended separately — skip it here so it
            # never appears twice (top + natural chronological position).
            if entry["id"] == pinned_id:
                continue

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

        # Resolve the pinned entry and prepend it on the first page, applying
        # the same visibility / acting-as rules as the main feed.
        pinned_entry: dict | None = None
        if pinned_id:
            pdoc = db.collection(self._COLLECTION).document(pinned_id).get()
            if pdoc.exists:
                pe = pdoc.to_dict()
                pe["id"] = pdoc.id
                acting_ok = True
                if acting_as:
                    target = pe.get("targetUid")
                    acting_ok = not target or target == acting_as
                if (
                    pe.get("projectId") == project_id
                    and acting_ok
                    and self._is_visible(
                        pe, user.user_id, my_dependents, is_staff
                    )
                ):
                    reaction_ref = (
                        db.collection(self._COLLECTION)
                        .document(pe["id"])
                        .collection("reactions")
                        .document(user.user_id)
                    )
                    pe["userLiked"] = reaction_ref.get().exists
                    pe["isPinned"] = True
                    pinned_entry = pe

        ordered = ([pinned_entry] if pinned_entry else []) + collected

        # Batch-fetch photos for all authors + targets in a single request.
        # Timeline entries only denormalize name/roles, not photo — resolving
        # live keeps the feed fresh when a user updates their avatar.
        photo_uids: set[str] = set()
        for e in ordered:
            if e.get("authorUid"):
                photo_uids.add(e["authorUid"])
            if e.get("targetUid"):
                photo_uids.add(e["targetUid"])
        photos = self._fetch_user_photos(db, photo_uids)

        visible = [self._to_out(e, photos) for e in ordered]
        # Pagination is driven by the non-pinned page contents only.
        next_cursor = last_created_at if len(collected) >= limit else None
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
    def toggle_pin(self, project_id: str, entry_id: str) -> bool:
        """Pin/unpin a timeline entry for the project.

        Only one entry can be pinned per project — pinning a new entry
        replaces the previous one. Pinning the already-pinned entry unpins it.
        Returns the resulting pinned state.
        """
        db = firestore.client()
        entry_ref = db.collection(self._COLLECTION).document(entry_id)
        doc = entry_ref.get()
        if not doc.exists or doc.to_dict().get("projectId") != project_id:
            raise LookupError("Timeline entry não encontrada")

        proj_ref = db.collection("projects").document(project_id)
        proj = proj_ref.get()
        current = (
            proj.to_dict().get("pinnedTimelineEntryId") if proj.exists else None
        )

        new_value = None if current == entry_id else entry_id
        proj_ref.set({"pinnedTimelineEntryId": new_value}, merge=True)
        return new_value is not None

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

    def _is_staff(self, ctx: AuthContext) -> bool:
        return bool(_STAFF_ROLES & set(ctx.roles))

    def can_view_entry(self, entry: dict, ctx: AuthContext) -> bool:
        """Reuse the feed visibility rule (RFC-11) for a single entry.

        Multi-tenant guard: a comment endpoint loads the entry globally by
        id, so it must first reject any entry that does not belong to the
        project in the caller's token (X-Project-Id). Without this, a card
        from another project would be reachable via the comment routes.
        """
        if entry.get("projectId") != ctx.project_id:
            return False
        db = firestore.client()
        is_staff = self._is_staff(ctx)
        dependents = (
            [] if is_staff else self._get_dependents(db, ctx.user_id, ctx.project_id)
        )
        return self._is_visible(entry, ctx.user_id, dependents, is_staff)

    def _uid_can_view(self, db, entry: dict, project_id: str, uid: str) -> bool:
        # Multi-tenant scoping: a uid is only considered if it is a member of
        # THIS project. This also closes a leak on PUBLIC cards, where
        # _is_visible would otherwise return True for any adult uid — even one
        # from another project — letting a client mention a non-member.
        mem = db.collection("memberships").document(f"{project_id}_{uid}").get()
        if not mem.exists:
            return False
        roles = mem.to_dict().get("roles", []) or []
        is_staff = bool(_STAFF_ROLES & set(roles))
        deps = [] if is_staff else self._get_dependents(db, uid, project_id)
        return self._is_visible(entry, uid, deps, is_staff)

    def _validate_mentions(
        self, db, entry: dict, project_id: str, mentions: list[str]
    ) -> list[str]:
        """Server-side mention validation (child safety — RFC-11).

        A client-supplied uid is only kept if it is BOTH an adult (a minor
        must never receive a mention push or be pulled into a thread) AND
        currently able to view THIS entry (an adult who can't see a
        personal card must never have its text leaked to them via a
        mention). Mirrors the autocomplete filtering in list_mentionable,
        but re-checked here because the create path cannot trust
        client-supplied mentions[].
        """
        valid: list[str] = []
        seen: set[str] = set()
        for uid in mentions:
            if not uid or uid in seen:
                continue
            seen.add(uid)
            user_snap = db.collection("users").document(uid).get()
            if not user_snap.exists:
                continue
            if not is_adult((user_snap.to_dict() or {}).get("birthDate")):
                continue
            if not self._uid_can_view(db, entry, project_id, uid):
                continue
            valid.append(uid)
        return valid

    @log
    def add_comment(
        self, entry_id: str, ctx: AuthContext, data: CommentCreate
    ) -> CommentOut:
        db = firestore.client()
        entry_ref = db.collection(self._COLLECTION).document(entry_id)
        snap = entry_ref.get()
        if not snap.exists:
            raise LookupError("Timeline entry não encontrada")
        entry = snap.to_dict()
        if not self.can_view_entry(entry, ctx):
            raise PermissionError("Sem acesso a este card")

        if ModerationService().get_level(ctx.project_id, ctx.user_id) != "none":
            raise PermissionError("Você está impedido de comentar neste projeto")

        author = db.collection("users").document(ctx.user_id).get().to_dict() or {}
        valid_mentions = self._validate_mentions(
            db, entry, ctx.project_id, data.mentions
        )
        mention_displays = self._resolve_displays(db, valid_mentions)
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "authorUid": ctx.user_id,
            "authorName": author.get("name", ""),
            "authorPhotoUrl": author.get("photoUrl"),
            "text": data.text.strip(),
            "parentId": data.parent_id,
            "mentions": valid_mentions,
            "mentionDisplays": mention_displays,
            "createdAt": now,
            "deleted": False,
            "deletedBy": None,
            "deletedAt": None,
        }
        _, ref = entry_ref.collection("comments").add(doc)
        entry_ref.update({"commentsCount": firestore.Increment(1)})

        self._notify_comment(db, ctx, entry, entry_ref, data, doc)

        return CommentOut(
            id=ref.id,
            author_uid=doc["authorUid"],
            author_name=doc["authorName"],
            author_photo_url=doc["authorPhotoUrl"],
            text=doc["text"],
            parent_id=doc["parentId"],
            mentions=doc["mentions"],
            mention_displays=doc["mentionDisplays"],
            created_at=now,
            deleted=False,
            deleted_by=None,
        )

    def _resolve_displays(self, db, uids: list[str]) -> list[str]:
        """Resolve each uid's display (nickname→name) — same rule as
        list_mentionable — so the client can highlight the full mention span.
        """
        displays: list[str] = []
        for uid in uids:
            u = db.collection("users").document(uid).get()
            d = u.to_dict() or {} if u.exists else {}
            display = (d.get("nickname") or "").strip() or (d.get("name") or "").strip()
            if display:
                displays.append(display)
        return displays

    def _notify_comment(
        self, db, ctx: AuthContext, entry: dict, entry_ref, data, doc: dict
    ) -> None:
        """Best-effort notifications for a new comment.

        Notifies (at most once each, never the commenter): the card owner,
        the author of the parent comment (on a reply), and any mentioned
        users. When a recipient qualifies for more than one reason, the
        mention label takes priority — mentions are resolved last so they
        overwrite any earlier owner/reply entry for the same uid.

        The comment doc + commentsCount increment are already durably
        persisted by the time this runs (called unguarded from
        add_comment), so nothing here — Firestore reads included — may ever
        propagate an exception back to the caller. Any failure is logged
        and swallowed.
        """
        try:
            author_name = doc["authorName"] or "Alguém"

            recipients: dict[str, str] = {}  # uid → event_id (dedup, 1 push/pessoa)

            owner = entry.get("targetUid") or entry.get("authorUid")
            if owner and owner != ctx.user_id:
                recipients[owner] = "comment.on_card"

            if data.parent_id:
                parent = (
                    entry_ref.collection("comments").document(data.parent_id).get()
                )
                if parent.exists:
                    parent_author = (parent.to_dict() or {}).get("authorUid")
                    if parent_author and parent_author != ctx.user_id:
                        recipients[parent_author] = "comment.reply"

            for mentioned_uid in doc.get("mentions", []):
                if mentioned_uid and mentioned_uid != ctx.user_id:
                    recipients[mentioned_uid] = "comment.mention"

            for uid, event_id in recipients.items():
                recipient = db.collection("users").document(uid).get().to_dict() or {}
                publisher.publish(
                    DomainEvent(
                        id=event_id,
                        payload=AccountNotificationPayload(
                            to=recipient.get("email", ""),
                            name=recipient.get("name", ""),
                            title=_COMMENT_NOTIFICATION_TITLES[event_id],
                            message=f"{author_name}: {doc['text'][:80]}",
                        ),
                    ),
                    project_id=ctx.project_id,
                    source="timeline_comment",
                )
        except Exception as exc:  # noqa: BLE001 — must never break comment creation
            logger.error(
                "comment_notify_failed",
                entry_id=entry_ref.id,
                user_id=ctx.user_id,
                project_id=ctx.project_id,
                error=str(exc),
                exc_info=exc,
            )
            return None

    @log
    def list_comments(
        self,
        entry_id: str,
        ctx: AuthContext,
        cursor: str | None = None,
        limit: int = 30,
    ) -> CommentsPage:
        db = firestore.client()
        entry_ref = db.collection(self._COLLECTION).document(entry_id)
        snap = entry_ref.get()
        if not snap.exists:
            raise LookupError("Timeline entry não encontrada")
        if not self.can_view_entry(snap.to_dict(), ctx):
            raise PermissionError("Sem acesso a este card")

        query = entry_ref.collection("comments").order_by("createdAt").limit(limit)
        docs = list(query.stream())
        items = [
            CommentOut(
                id=doc.id,
                author_uid=c["authorUid"],
                author_name=c["authorName"],
                author_photo_url=c.get("authorPhotoUrl"),
                text="" if c.get("deleted") else c["text"],
                parent_id=c.get("parentId"),
                mentions=c.get("mentions", []),
                mention_displays=c.get("mentionDisplays", []),
                created_at=c["createdAt"],
                deleted=c.get("deleted", False),
                deleted_by=c.get("deletedBy"),
            )
            for doc in docs
            for c in [doc.to_dict()]
        ]
        return CommentsPage(items=items, next_cursor=None)

    @log
    def delete_comment(self, entry_id: str, comment_id: str, ctx: AuthContext) -> None:
        db = firestore.client()
        entry_ref = db.collection(self._COLLECTION).document(entry_id)
        entry_snap = entry_ref.get()
        if not entry_snap.exists:
            raise LookupError("Timeline entry não encontrada")
        if not self.can_view_entry(entry_snap.to_dict(), ctx):
            raise PermissionError("Sem acesso a este card")

        comment_ref = entry_ref.collection("comments").document(comment_id)
        comment_snap = comment_ref.get()
        if not comment_snap.exists:
            raise LookupError("Comentário não encontrado")
        comment = comment_snap.to_dict()
        if comment.get("deleted"):
            return
        if comment["authorUid"] != ctx.user_id and not self._is_staff(ctx):
            raise PermissionError("Só o autor ou a equipe podem remover")
        comment_ref.update({
            "deleted": True,
            "deletedBy": ctx.user_id,
            "deletedAt": datetime.now(timezone.utc).isoformat(),
        })
        entry_ref.update({"commentsCount": firestore.Increment(-1)})

    def _project_member_docs(self, project_id: str) -> list[dict]:
        """UIDs+dados dos membros do projeto (para o autocomplete). Lê
        memberships ativas → users. Retorna dicts
        {uid,name,nickname,birthDate,photoUrl}."""
        db = firestore.client()
        mships = (
            db.collection("memberships")
            .where("projectId", "==", project_id)
            .where("status", "==", "active")
            .stream()
        )
        uids = [m.to_dict()["userId"] for m in mships]
        out = []
        for uid in uids:
            u = db.collection("users").document(uid).get()
            if not u.exists:
                continue
            d = u.to_dict()
            out.append({
                "uid": uid,
                "name": d.get("name", ""),
                "nickname": d.get("nickname"),
                "birthDate": d.get("birthDate"),
                "photoUrl": d.get("photoUrl"),
            })
        return out

    @log
    def list_mentionable(
        self, entry_id: str, ctx: AuthContext, q: str = ""
    ) -> list[MentionableOut]:
        """Autocomplete de `@`-mention: membros adultos que podem ver o card.

        Menores nunca são mencionáveis (child safety), mesmo que sejam
        membros visíveis do card — filtrados via ``is_adult``.
        """
        db = firestore.client()
        snap = db.collection(self._COLLECTION).document(entry_id).get()
        if not snap.exists:
            raise LookupError("Timeline entry não encontrada")
        entry = snap.to_dict()
        if not self.can_view_entry(entry, ctx):
            raise PermissionError("Sem acesso a este card")

        ql = q.strip().lower()
        results: list[MentionableOut] = []
        for m in self._project_member_docs(ctx.project_id):
            if not is_adult(m.get("birthDate")):
                continue  # menor: nunca mencionável
            nick = (m.get("nickname") or "").strip()
            name = (m.get("name") or "").strip()
            display = nick or name
            if ql and ql not in display.lower() and ql not in name.lower():
                continue
            words = display.split()
            if len(words) >= 2:
                initials = (words[0][0] + words[1][0]).upper()
            elif len(words) == 1:
                initials = words[0][:2].upper()
            else:
                initials = "?"
            results.append(MentionableOut(
                uid=m["uid"],
                display=display,
                subtitle=name if nick else None,
                photo_url=m.get("photoUrl"),
                initials=initials,
            ))
        results.sort(key=lambda x: x.display.lower())
        return results

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
            comments_count=entry.get("commentsCount", 0),
            user_liked=entry.get("userLiked", False),
            is_pinned=entry.get("isPinned", False),
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
