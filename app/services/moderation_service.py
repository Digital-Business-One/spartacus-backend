from datetime import datetime, timezone

from firebase_admin import firestore

from app.domain.enums import STAFF_ROLES
from app.events.models import AccountModerationPayload, DomainEvent
from app.logging.decorator import log
from app.security.context import ADMIN_ROLES, AuthContext
from app.services.account_history_service import AccountHistoryService

_MODERATION = "moderation"
_USERS = "users"
_MEMBERSHIPS = "memberships"

_EVENT_BY_LEVEL = {
    "comment_blocked": "account.comment_blocked",
    "app_banned": "account.app_banned",
}


class ModerationService:
    def _doc_id(self, project_id: str, uid: str) -> str:
        return f"{project_id}_{uid}"

    @log
    def get_status(self, project_id: str, uid: str) -> tuple[str, str | None]:
        """Single-read variant of `get_level` that also returns the
        moderation doc's `reason`, so callers (e.g. `/auth/me`) don't need
        a second Firestore read — or their own `firestore` import — just to
        surface why a user was banned."""
        db = firestore.client()
        snap = (
            db.collection(_MODERATION)
            .document(self._doc_id(project_id, uid))
            .get()
        )
        if not snap.exists:
            return "none", None
        data = snap.to_dict() or {}
        return data.get("level", "none"), data.get("reason")

    @log
    def get_level(self, project_id: str, uid: str) -> str:
        return self.get_status(project_id, uid)[0]

    def _roles_of(self, db, project_id: str, uid: str) -> list[str]:
        mem = db.collection(_MEMBERSHIPS).document(self._doc_id(project_id, uid)).get()
        return (mem.to_dict() or {}).get("roles", []) if mem.exists else []

    def _name_of(self, db, uid: str) -> str:
        u = db.collection(_USERS).document(uid).get()
        return (u.to_dict() or {}).get("name", "") if u.exists else ""

    def _can_moderate(
        self, db, project_id: str, ctx: AuthContext, target_uid: str
    ) -> None:
        if target_uid == ctx.user_id:
            raise PermissionError("Não é possível moderar a si mesmo")
        target_roles = set(self._roles_of(db, project_id, target_uid))
        if (target_roles & STAFF_ROLES) and "owner" not in set(ctx.roles):
            raise PermissionError("Apenas o owner pode moderar membros da equipe")

    @log
    def apply(
        self, project_id: str, target_uid: str, ctx: AuthContext,
        level: str, reason: str,
    ) -> DomainEvent:
        if level == "app_banned" and not (ADMIN_ROLES & set(ctx.roles)):
            raise PermissionError("Apenas owner/assistant podem banir do app")
        db = firestore.client()
        self._can_moderate(db, project_id, ctx, target_uid)

        now = datetime.now(timezone.utc).isoformat()
        db.collection(_MODERATION).document(self._doc_id(project_id, target_uid)).set({
            "projectId": project_id,
            "userId": target_uid,
            "level": level,
            "reason": reason,
            "moderatedBy": ctx.user_id,
            "moderatedAt": now,
        })

        target_name = self._name_of(db, target_uid)
        actor_name = self._name_of(db, ctx.user_id)
        AccountHistoryService().record(
            uid=target_uid, project_id=project_id,
            event_type="moderation", event_subtype=level,
            actor_uid=ctx.user_id, actor_name=actor_name,
            actor_roles=ctx.roles,
            description=f"Moderação ({level}): {reason}", db=db,
        )
        return DomainEvent(
            id=_EVENT_BY_LEVEL[level],
            payload=AccountModerationPayload(
                entity_id=target_uid, target_uid=target_uid,
                target_name=target_name, author_uid=ctx.user_id,
                author_name=actor_name, reason=reason,
            ),
        )

    @log
    def lift(self, project_id: str, target_uid: str, ctx: AuthContext) -> DomainEvent:
        db = firestore.client()
        ref = db.collection(_MODERATION).document(
            self._doc_id(project_id, target_uid)
        )
        current = ref.get()
        current_level = (
            (current.to_dict() or {}).get("level", "none")
            if current.exists else "none"
        )
        if current_level == "app_banned" and not (ADMIN_ROLES & set(ctx.roles)):
            raise PermissionError("Apenas owner/assistant podem desbanir")
        self._can_moderate(db, project_id, ctx, target_uid)

        ref.delete()
        target_name = self._name_of(db, target_uid)
        actor_name = self._name_of(db, ctx.user_id)
        AccountHistoryService().record(
            uid=target_uid, project_id=project_id,
            event_type="moderation", event_subtype="lifted",
            actor_uid=ctx.user_id, actor_name=actor_name,
            actor_roles=ctx.roles,
            description="Moderação removida (acesso restaurado)", db=db,
        )
        return DomainEvent(
            id="account.moderation_lifted",
            payload=AccountModerationPayload(
                entity_id=target_uid, target_uid=target_uid,
                target_name=target_name, author_uid=ctx.user_id,
                author_name=actor_name, reason="",
            ),
        )

    @log
    def list_users(self, ctx: AuthContext, q: str = "", status: str = ""):
        from app.models.moderation import ModeratedUserOut, ModeratedUsersPage

        db = firestore.client()
        mships = (
            db.collection(_MEMBERSHIPS)
            .where("projectId", "==", ctx.project_id)
            .where("status", "==", "active")
            .stream()
        )
        ql = q.strip().lower()
        items = []
        for m in mships:
            md = m.to_dict()
            uid = md["userId"]
            roles = md.get("roles", []) or []
            u = db.collection(_USERS).document(uid).get()
            if not u.exists:
                continue
            d = u.to_dict()
            name = d.get("name", "")
            if ql and ql not in name.lower():
                continue
            level = self.get_level(ctx.project_id, uid)
            if status and status != level:
                continue
            items.append(ModeratedUserOut(
                uid=uid, name=name,
                role_label=roles[0] if roles else None,
                photo_url=d.get("photoUrl"),
                level=level, is_staff=bool(set(roles) & STAFF_ROLES),
            ))
        items.sort(key=lambda x: x.name.lower())
        return ModeratedUsersPage(items=items)
