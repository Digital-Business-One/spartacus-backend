from datetime import datetime, timezone

from firebase_admin import auth, firestore

from app.logging.decorator import log
from app.models.membership import (
    EligibleUserOut,
    MembershipCreate,
    MembershipOut,
    MembershipUpdate,
)


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
            if len(data.roles) == 0:
                raise ValueError(
                    "Usuário não pode ficar sem perfil"
                )
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

    @log
    def list_eligible(
        self,
        project_id: str,
        role: str,
        search: str | None = None,
    ) -> list[EligibleUserOut]:
        """Return approved accounts that do NOT have the given role."""
        db = firestore.client()

        # Get all memberships for the project
        memberships = list(
            db.collection(self._COLLECTION)
            .where("projectId", "==", project_id)
            .stream()
        )

        # Build maps: uid → roles, and collect uids that already have the role
        uid_roles: dict[str, list[str]] = {}
        has_role: set[str] = set()
        for m in memberships:
            data = m.to_dict()
            uid = data["userId"]
            roles = data.get("roles", [])
            uid_roles[uid] = roles
            if role in roles:
                has_role.add(uid)

        # Eligible = all members minus those who already have the role
        eligible_uids = set(uid_roles.keys()) - has_role
        if not eligible_uids:
            return []

        # Batch-read user docs
        refs = [
            db.collection("users").document(uid)
            for uid in eligible_uids
        ]
        results: list[EligibleUserOut] = []
        for doc in db.get_all(refs):
            if not doc.exists:
                continue
            user = doc.to_dict()
            # Only approved accounts
            if user.get("approvalStatus") != "approved":
                continue
            # Search filter
            if search:
                name = user.get("name", "").lower()
                if search.lower() not in name:
                    continue
            results.append(
                EligibleUserOut(
                    uid=doc.id,
                    name=user.get("name", ""),
                    email=user.get("email", ""),
                    photo_url=user.get("photoUrl"),
                    birth_date=user.get("birthDate"),
                    roles=uid_roles.get(doc.id, []),
                )
            )

        results.sort(key=lambda u: u.name.lower())
        return results

    @log
    def assign_role(
        self,
        project_id: str,
        role: str,
        user_ids: list[str],
    ) -> tuple[int, int]:
        """Add a role to multiple users. Returns (assigned, skipped)."""
        db = firestore.client()
        assigned = 0
        skipped = 0

        for uid in user_ids:
            doc_ref = db.collection(self._COLLECTION).document(
                self._doc_id(project_id, uid)
            )
            doc = doc_ref.get()
            if not doc.exists:
                skipped += 1
                continue
            data = doc.to_dict()
            current_roles = data.get("roles", [])
            if role in current_roles:
                skipped += 1
                continue
            new_roles = current_roles + [role]
            doc_ref.update({"roles": new_roles})
            self._sync_claims(uid)
            assigned += 1

        return assigned, skipped

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
