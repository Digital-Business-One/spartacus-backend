from typing import Optional

from firebase_admin import auth, firestore

from app.domain.account_states import (
    AccountStatus,
    find_transition,
    get_available_actions,
)
from app.logging.decorator import log
from app.models.account import AccountAction, AccountOut, TransitionResponse
from app.notifications.models import DomainEvent


class AccountService:
    _USERS = "users"
    _MEMBERSHIPS = "memberships"

    @log
    def get_account(
        self, project_id: str, uid: str
    ) -> Optional[AccountOut]:
        db = firestore.client()
        user_doc = db.collection(self._USERS).document(uid).get()
        if not user_doc.exists:
            return None

        user = user_doc.to_dict()
        roles = self._get_roles(db, project_id, uid)
        status = user.get("approvalStatus", "")

        actions = get_available_actions(status, roles, executed_by_filter="team")
        return self._build_account_out(uid, user, roles, status, actions)

    @log
    def list_accounts(
        self,
        project_id: str,
        status_filter: str | None = None,
        role_filter: str | None = None,
        search: str | None = None,
    ) -> list[AccountOut]:
        db = firestore.client()

        # Read all memberships for this project
        query = db.collection(self._MEMBERSHIPS).where(
            "projectId", "==", project_id
        )
        memberships = list(query.stream())
        if not memberships:
            return []

        # Build uid → roles map
        uid_roles: dict[str, list[str]] = {}
        for m in memberships:
            data = m.to_dict()
            uid_roles[data["userId"]] = data.get("roles", [])

        # Apply role filter
        if role_filter:
            uid_roles = {
                uid: roles
                for uid, roles in uid_roles.items()
                if role_filter in roles
            }

        if not uid_roles:
            return []

        # Batch-read user docs
        refs = [
            db.collection(self._USERS).document(uid)
            for uid in uid_roles
        ]
        user_docs = db.get_all(refs)

        results: list[AccountOut] = []
        for doc in user_docs:
            if not doc.exists:
                continue
            user = doc.to_dict()
            uid = doc.id
            roles = uid_roles.get(uid, [])
            status = user.get("approvalStatus", "")

            # Apply status filter
            if status_filter and status != status_filter:
                continue

            # Apply search filter (name contains, case-insensitive)
            if search:
                name = user.get("name", "").lower()
                if search.lower() not in name:
                    continue

            actions = get_available_actions(
                status, roles, executed_by_filter="team"
            )
            results.append(
                self._build_account_out(uid, user, roles, status, actions)
            )

        return results

    @log
    def execute_transition(
        self,
        project_id: str,
        uid: str,
        action: str,
        actor_uid: str,
    ) -> tuple[TransitionResponse, DomainEvent | None]:
        db = firestore.client()
        user_ref = db.collection(self._USERS).document(uid)
        user_doc = user_ref.get()
        if not user_doc.exists:
            raise ValueError("Conta não encontrada")

        user = user_doc.to_dict()
        current_status = user.get("approvalStatus", "")
        roles = self._get_roles(db, project_id, uid)

        transition = find_transition(current_status, action, roles)
        if transition is None:
            raise ValueError(
                f"Transição '{action}' não permitida "
                f"do estado '{current_status}'"
            )

        new_status = transition.target.value

        # Atomic write
        user_ref.update({"approvalStatus": new_status})

        # If approved: activate membership + sync claims
        if new_status == AccountStatus.APPROVED:
            self._activate_membership(db, project_id, uid)
            # Auto-approve dependents if user is guardian
            if "guardian" in roles:
                self._auto_approve_dependents(db, project_id, uid)

        # Build event for notification
        event = self._build_event(
            action, user, new_status, current_status
        )

        return (
            TransitionResponse(
                uid=uid,
                previous_status=current_status,
                new_status=new_status,
                action=action,
            ),
            event,
        )

    # ── Private helpers ────────────────────────────────────────────────────

    def _get_roles(
        self, db, project_id: str, uid: str
    ) -> list[str]:
        mem_doc = (
            db.collection(self._MEMBERSHIPS)
            .document(f"{project_id}_{uid}")
            .get()
        )
        if not mem_doc.exists:
            return []
        return mem_doc.to_dict().get("roles", [])

    def _activate_membership(
        self, db, project_id: str, uid: str
    ) -> None:
        mem_ref = db.collection(self._MEMBERSHIPS).document(
            f"{project_id}_{uid}"
        )
        mem_doc = mem_ref.get()
        if not mem_doc.exists:
            return
        mem_ref.update({"status": "active"})

        # Sync Firebase Custom Claims
        all_memberships = (
            db.collection(self._MEMBERSHIPS)
            .where("userId", "==", uid)
            .where("status", "==", "active")
            .stream()
        )
        projects_claims: dict[str, list[str]] = {}
        for m in all_memberships:
            d = m.to_dict()
            projects_claims[d["projectId"]] = d.get("roles", [])
        auth.set_custom_user_claims(uid, {"projects": projects_claims})

    def _auto_approve_dependents(
        self, db, project_id: str, guardian_uid: str
    ) -> None:
        deps = (
            db.collection(self._USERS)
            .where("guardianUid", "==", guardian_uid)
            .where("isDependent", "==", True)
            .stream()
        )
        for dep_doc in deps:
            dep = dep_doc.to_dict()
            dep_status = dep.get("approvalStatus", "")
            # Only auto-approve if in an approvable state
            if dep_status in (
                AccountStatus.PENDING_APPROVAL,
                AccountStatus.PENDING_MEDICAL_HISTORY_APPROVAL,
            ):
                dep_doc.reference.update(
                    {"approvalStatus": AccountStatus.APPROVED}
                )
                self._activate_membership(
                    db, project_id, dep_doc.id
                )

    def _build_account_out(
        self,
        uid: str,
        user: dict,
        roles: list[str],
        status: str,
        transitions: list,
    ) -> AccountOut:
        return AccountOut(
            uid=uid,
            name=user.get("name", ""),
            email=user.get("email", ""),
            roles=roles,
            status=status,
            birth_date=user.get("birthDate"),
            gender=user.get("gender"),
            phone=user.get("phone"),
            created_at=user.get("createdAt"),
            is_dependent=user.get("isDependent", False),
            guardian_uid=user.get("guardianUid"),
            available_actions=[
                AccountAction(
                    action=t.action,
                    label=t.label,
                    target_status=t.target.value,
                )
                for t in transitions
            ],
        )

    def _build_event(
        self,
        action: str,
        user: dict,
        new_status: str,
        previous_status: str,
    ) -> DomainEvent | None:
        email = user.get("email")
        if not email:
            return None
        from app.notifications.models import AccountReceivedPayload

        # Reuse existing payload for now — specific templates later
        return DomainEvent(
            id=f"account.{action}",
            payload=AccountReceivedPayload(
                to=email,
                name=user.get("name", ""),
            ),
        )
