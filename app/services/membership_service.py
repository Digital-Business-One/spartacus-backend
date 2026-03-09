from datetime import datetime, timezone

from firebase_admin import auth, firestore

from app.logging.decorator import log
from app.models.membership import MembershipCreate, MembershipOut, MembershipUpdate


class MembershipService:
    _COLLECTION = "memberships"

    def _doc_id(self, project_id: str, user_id: str) -> str:
        return f"{project_id}_{user_id}"

    @log
    def add(self, project_id: str, data: MembershipCreate) -> MembershipOut:
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(
            self._doc_id(project_id, data.user_id)
        )
        if doc_ref.get().exists:
            raise ValueError("Usuário já é membro deste projeto")

        now = datetime.now(timezone.utc).isoformat()
        doc_data = {
            "projectId": project_id,
            "userId": data.user_id,
            "roles": data.roles,
            "status": data.status,
            "joined_at": now,
        }
        doc_ref.set(doc_data)
        self._sync_claims(data.user_id)
        return MembershipOut(
            project_id=project_id,
            user_id=data.user_id,
            roles=data.roles,
            status=data.status,
            joined_at=now,
        )

    @log
    def list_active(self, project_id: str) -> list[MembershipOut]:
        db = firestore.client()
        docs = (
            db.collection(self._COLLECTION)
            .where("projectId", "==", project_id)
            .where("status", "==", "active")
            .stream()
        )
        return [
            MembershipOut(
                project_id=d.to_dict()["projectId"],
                user_id=d.to_dict()["userId"],
                roles=d.to_dict()["roles"],
                status=d.to_dict()["status"],
                joined_at=d.to_dict()["joined_at"],
            )
            for d in docs
        ]

    @log
    def update(
        self, project_id: str, user_id: str, data: MembershipUpdate
    ) -> MembershipOut:
        db = firestore.client()
        doc_ref = db.collection(self._COLLECTION).document(
            self._doc_id(project_id, user_id)
        )
        doc = doc_ref.get()
        if not doc.exists:
            raise LookupError("Membership não encontrado")

        updates = {}
        if data.roles is not None:
            updates["roles"] = data.roles
        if data.status is not None:
            updates["status"] = data.status
        if updates:
            doc_ref.update(updates)

        current = {**doc.to_dict(), **updates}
        self._sync_claims(user_id)
        return MembershipOut(
            project_id=project_id,
            user_id=user_id,
            roles=current["roles"],
            status=current["status"],
            joined_at=current["joined_at"],
        )

    def _sync_claims(self, user_id: str) -> None:
        """Reconstrói e sincroniza as Custom Claims do Firebase para o usuário.

        Lê todos os memberships ativos do usuário em todos os projetos e
        reescreve as Custom Claims completas para refletir o estado atual.
        """
        db = firestore.client()
        docs = (
            db.collection(self._COLLECTION)
            .where("userId", "==", user_id)
            .where("status", "==", "active")
            .stream()
        )
        projects = {
            d.to_dict()["projectId"]: d.to_dict()["roles"] for d in docs
        }
        auth.set_custom_user_claims(user_id, {"projects": projects})
