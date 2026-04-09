from datetime import datetime, timezone
from typing import Optional

from firebase_admin import auth, firestore

from app.domain.account_states import (
    AccountStatus,
    find_transition,
    get_available_actions,
)
from app.events.models import DomainEvent
from app.logging.decorator import log
from app.models.account import (
    AccountAction,
    AccountDetailOut,
    AccountListPage,
    AccountOut,
    AddressOut,
    ClassDetailOut,
    CompetitionOut,
    GraduationEntry,
    TransitionResponse,
)
from app.services.account_history_service import AccountHistoryService


class AccountService:
    _USERS = "users"
    _MEMBERSHIPS = "memberships"

    @log
    def get_account(
        self, project_id: str, uid: str
    ) -> Optional[AccountOut]:
        """Slim representation — used internally and by listings."""
        db = firestore.client()
        user_doc = db.collection(self._USERS).document(uid).get()
        if not user_doc.exists:
            return None

        user = user_doc.to_dict()
        roles = self._get_roles(db, project_id, uid)
        status = user.get("approvalStatus", "")
        class_names = self._fetch_class_names(db, user.get("classIds", []))

        actions = get_available_actions(status, roles, executed_by_filter="team")
        return self._build_account_out(
            uid, user, roles, status, actions, class_names
        )

    @log
    def get_account_detail(
        self, project_id: str, uid: str
    ) -> Optional[AccountDetailOut]:
        """Rich representation — used by GET /accounts/{uid} (RFC-12)."""
        db = firestore.client()
        user_doc = db.collection(self._USERS).document(uid).get()
        if not user_doc.exists:
            return None

        user = user_doc.to_dict()
        roles = self._get_roles(db, project_id, uid)
        status = user.get("approvalStatus", "")
        actions = get_available_actions(
            status, roles, executed_by_filter="team",
        )

        # Address
        addr_data = user.get("address")
        address = None
        if addr_data and isinstance(addr_data, dict):
            address = AddressOut(
                postal_code=addr_data.get("postalCode"),
                street=addr_data.get("street"),
                number=addr_data.get("number"),
                complement=addr_data.get("complement"),
                neighborhood=addr_data.get("neighborhood"),
                city=addr_data.get("city"),
                state=addr_data.get("state"),
            )

        # Classes (expanded)
        classes = self._fetch_classes_detail(db, user.get("classIds", []))

        # Graduation
        graduation_raw = user.get("graduation") or {}
        graduation: Optional[dict[str, GraduationEntry]] = None
        if graduation_raw:
            graduation = {
                k: GraduationEntry(
                    belt=v.get("belt", ""),
                    degree=v.get("degree", 0),
                    prajied=v.get("prajied"),
                )
                for k, v in graduation_raw.items()
                if isinstance(v, dict)
            }

        # Competition
        competition_raw = user.get("competition") or {}
        competition: Optional[CompetitionOut] = None
        if competition_raw:
            competition = CompetitionOut(
                weight_kg=competition_raw.get("weightKg"),
                target_categories=competition_raw.get("targetCategories", []),
                calculated_age_category=competition_raw.get(
                    "calculatedAgeCategory",
                ),
                calculated_weight_category=competition_raw.get(
                    "calculatedWeightCategory",
                ),
            )

        # Guardian info (denormalized for the "dependente de" card)
        guardian_name = None
        guardian_phone = None
        guardian_uid = user.get("guardianUid")
        if user.get("isDependent") and guardian_uid:
            guardian_doc = (
                db.collection(self._USERS).document(guardian_uid).get()
            )
            if guardian_doc.exists:
                gdata = guardian_doc.to_dict()
                guardian_name = gdata.get("name")
                guardian_phone = gdata.get("phone")

        # Resolve names for audit fields (best-effort)
        approved_by = user.get("approvedBy")
        approved_by_name = self._resolve_user_name(db, approved_by)
        last_updated_by = user.get("lastUpdatedBy")
        last_updated_by_name = self._resolve_user_name(db, last_updated_by)

        # Derived: age category (Infantil if <18)
        age_category = self._derive_age_category(user.get("birthDate"))

        return AccountDetailOut(
            uid=uid,
            name=user.get("name", ""),
            email=user.get("email"),
            email_verified=user.get("emailVerified", False),
            tax_id=user.get("taxId"),
            photo_url=user.get("photoUrl"),
            birth_date=user.get("birthDate"),
            gender=user.get("gender"),
            phone=user.get("phone"),
            whatsapp=user.get("whatsapp"),
            roles=roles,
            status=status,
            is_dependent=user.get("isDependent", False),
            guardian_uid=guardian_uid,
            guardian_name=guardian_name,
            guardian_phone=guardian_phone,
            guardian_relationship=user.get("guardianRelationship"),
            address=address,
            classes=classes,
            graduation=graduation,
            competition=competition,
            created_at=user.get("createdAt"),
            updated_at=user.get("updatedAt"),
            last_updated_by=last_updated_by,
            last_updated_by_name=last_updated_by_name,
            approved_by=approved_by,
            approved_by_name=approved_by_name,
            approved_at=user.get("approvedAt"),
            auth_provider=user.get("authProvider"),
            age_category=age_category,
            available_actions=[
                AccountAction(
                    action=t.action,
                    label=t.label,
                    target_status=t.target.value,
                )
                for t in actions
            ],
        )

    @log
    def list_accounts_paginated(
        self,
        project_id: str,
        status_filter: str | None = None,
        role_filter: str | None = None,
        search: str | None = None,
        sort: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AccountListPage:
        """Paginated version of list_accounts (RFC-12)."""
        all_items = self.list_accounts(
            project_id=project_id,
            status_filter=status_filter,
            role_filter=role_filter,
            search=search,
        )

        # Sort
        if sort == "name" or sort is None:
            all_items.sort(key=lambda a: a.name.lower())
        elif sort == "name_desc":
            all_items.sort(key=lambda a: a.name.lower(), reverse=True)
        elif sort == "age":
            all_items.sort(
                key=lambda a: self._birth_sort_key(a.birth_date),
            )
        elif sort == "age_desc":
            all_items.sort(
                key=lambda a: self._birth_sort_key(a.birth_date),
                reverse=True,
            )

        total = len(all_items)
        total_pages = (total + page_size - 1) // page_size if page_size else 1
        start = (page - 1) * page_size
        end = start + page_size
        return AccountListPage(
            items=all_items[start:end],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    @staticmethod
    def _birth_sort_key(birth_date: str | None) -> str:
        """Sort key for birthDate (DD/MM/YYYY → YYYY-MM-DD for chronological)."""
        if not birth_date:
            return "9999-99-99"
        try:
            d, m, y = birth_date.split("/")
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
        except (ValueError, AttributeError):
            return "9999-99-99"

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

        # Collect all class IDs for batch fetch
        all_class_ids: set[str] = set()
        user_data_list: list[tuple[str, dict]] = []
        for doc in user_docs:
            if not doc.exists:
                continue
            user = doc.to_dict()
            all_class_ids.update(user.get("classIds", []))
            user_data_list.append((doc.id, user))

        class_info = self._fetch_class_info(db, list(all_class_ids))
        class_names_map = {cid: v["name"] for cid, v in class_info.items()}

        # Status filter accepts CSV (e.g. "rejected,expelled,archived")
        status_set: set[str] | None = None
        if status_filter:
            status_set = {
                s.strip() for s in status_filter.split(",") if s.strip()
            }

        results: list[AccountOut] = []
        for uid, user in user_data_list:
            roles = uid_roles.get(uid, [])
            status = user.get("approvalStatus", "")

            # Apply status filter
            if status_set and status not in status_set:
                continue

            # Apply search filter (name contains, case-insensitive)
            if search:
                name = user.get("name", "").lower()
                if search.lower() not in name:
                    continue

            # Resolve unique modality names from class info
            user_class_ids = user.get("classIds", [])
            modality_names = list(dict.fromkeys(
                class_info[cid]["modality_name"]
                for cid in user_class_ids
                if cid in class_info and class_info[cid]["modality_name"]
            ))

            actions = get_available_actions(
                status, roles, executed_by_filter="team"
            )
            results.append(
                self._build_account_out(
                    uid, user, roles, status, actions, class_names_map,
                    modality_names=modality_names,
                )
            )

        # ── Enrich: guardian info for dependents ──────────────────────
        guardian_uids = {
            r.guardian_uid for r in results
            if r.is_dependent and r.guardian_uid
        }
        guardian_map: dict[str, dict] = {}
        if guardian_uids:
            # Try to resolve from already-loaded user data
            for uid, user in user_data_list:
                if uid in guardian_uids:
                    guardian_map[uid] = {
                        "name": user.get("name"),
                        "photo_url": user.get("photoUrl"),
                    }
            # Fetch any guardians not in user_data_list
            missing = guardian_uids - set(guardian_map.keys())
            if missing:
                refs = [
                    db.collection(self._USERS).document(uid)
                    for uid in missing
                ]
                for doc in db.get_all(refs):
                    if doc.exists:
                        d = doc.to_dict()
                        guardian_map[doc.id] = {
                            "name": d.get("name"),
                            "photo_url": d.get("photoUrl"),
                        }

        for r in results:
            if r.is_dependent and r.guardian_uid and r.guardian_uid in guardian_map:
                info = guardian_map[r.guardian_uid]
                r.guardian_name = info.get("name")
                r.guardian_photo_url = info.get("photo_url")

        # ── Enrich: dependents for guardians ──────────────────────────
        deps_by_guardian: dict[str, list[AccountOut]] = {}
        for r in results:
            if r.is_dependent and r.guardian_uid:
                deps_by_guardian.setdefault(r.guardian_uid, []).append(r)

        for r in results:
            if "guardian" in r.roles:
                r.dependents = deps_by_guardian.get(r.uid, [])

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
        now_iso = datetime.now(timezone.utc).isoformat()

        # Build update payload — track auditoria
        updates: dict = {
            "approvalStatus": new_status,
            "updatedAt": now_iso,
            "lastUpdatedBy": actor_uid,
        }
        if new_status == AccountStatus.APPROVED:
            updates["approvedBy"] = actor_uid
            updates["approvedAt"] = now_iso

        user_ref.update(updates)

        # Resolve actor name for history (best-effort, single doc read)
        actor_name = ""
        actor_doc = db.collection(self._USERS).document(actor_uid).get()
        if actor_doc.exists:
            actor_name = actor_doc.to_dict().get("name", "")

        # Record in history sub-collection
        AccountHistoryService().record(
            uid=uid,
            project_id=project_id,
            event_type=("suspension" if action == "expel" else "approval"),
            event_subtype=action,
            actor_uid=actor_uid,
            actor_name=actor_name,
            actor_roles=self._get_roles(db, project_id, actor_uid),
            description=self._describe_transition(action, actor_name),
        )

        # If approved: activate membership + sync claims
        if new_status == AccountStatus.APPROVED:
            self._activate_membership(db, project_id, uid)
            # Auto-approve dependents if user is guardian
            if "guardian" in roles:
                self._auto_approve_dependents(
                    db, project_id, uid, actor_uid, actor_name,
                )

        # If guardian goes to waiting_medical_history: send dependents along.
        # This lets the guardian fill all anamneses (own + dependents) in
        # the same session, instead of waiting for guardian's own anamnese
        # to be approved by the team first.
        if (
            new_status == AccountStatus.WAITING_MEDICAL_HISTORY
            and "guardian" in roles
        ):
            self._send_student_dependents_to_anamnese(
                db, project_id, uid, actor_uid, actor_name,
            )

        # If guardian is approved without anamnese (action="approve"),
        # students dependents still need anamnese — handled by
        # _auto_approve_dependents above which moves them to
        # waiting_medical_history.

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

    _ACTION_DESCRIPTIONS: dict[str, str] = {
        "approve": "Conta aprovada",
        "approve_to_medical": "Cadastro encaminhado para anamnese",
        "approve_medical": "Anamnese aprovada",
        "request_revision": "Revisão cadastral solicitada",
        "reject": "Cadastro rejeitado",
        "expel": "Conta suspensa",
        "archive": "Conta arquivada",
        "reactivate": "Conta reativada",
        "submit_revision": "Revisão enviada pelo usuário",
        "submit_medical_history": "Anamnese enviada pelo usuário",
        "complete_registration": "Cadastro completado",
    }

    @classmethod
    def _describe_transition(cls, action: str, actor_name: str) -> str:
        base = cls._ACTION_DESCRIPTIONS.get(action, action)
        return f"{base} por {actor_name}" if actor_name else base

    def _fetch_class_names(
        self, db, class_ids: list[str]
    ) -> dict[str, str]:
        """Legacy helper — returns {class_id: class_name}."""
        info = self._fetch_class_info(db, class_ids)
        return {cid: v["name"] for cid, v in info.items()}

    def _fetch_class_info(
        self, db, class_ids: list[str]
    ) -> dict[str, dict]:
        """Returns {class_id: {"name": str, "modality_name": str}}."""
        if not class_ids:
            return {}
        refs = [
            db.collection("classes").document(cid)
            for cid in class_ids
        ]
        docs = list(db.get_all(refs))

        # Collect modality IDs for batch resolution
        modality_ids: set[str] = set()
        for doc in docs:
            if doc.exists:
                mid = doc.to_dict().get("modalityId", "")
                if mid:
                    modality_ids.add(mid)

        modality_names: dict[str, str] = {}
        if modality_ids:
            mod_refs = [
                db.collection("modalities").document(mid)
                for mid in modality_ids
            ]
            for mod_doc in db.get_all(mod_refs):
                if mod_doc.exists:
                    modality_names[mod_doc.id] = mod_doc.to_dict().get(
                        "name", mod_doc.id,
                    )

        result: dict[str, dict] = {}
        for doc in docs:
            if not doc.exists:
                continue
            data = doc.to_dict()
            mid = data.get("modalityId", "")
            result[doc.id] = {
                "name": data.get("name", doc.id),
                "modality_name": modality_names.get(mid, mid),
            }
        return result

    def _fetch_classes_detail(
        self, db, class_ids: list[str]
    ) -> list[ClassDetailOut]:
        """Resolve class IDs into expanded class info for the detail page."""
        if not class_ids:
            return []
        refs = [
            db.collection("classes").document(cid)
            for cid in class_ids
        ]
        docs = db.get_all(refs)
        # Resolve modality names in batch
        modality_ids = {
            doc.to_dict().get("modalityId", "")
            for doc in docs if doc.exists
        }
        modality_ids.discard("")
        modality_names: dict[str, str] = {}
        if modality_ids:
            mod_refs = [
                db.collection("modalities").document(mid)
                for mid in modality_ids
            ]
            for mod_doc in db.get_all(mod_refs):
                if mod_doc.exists:
                    modality_names[mod_doc.id] = mod_doc.to_dict().get(
                        "name", mod_doc.id,
                    )

        result: list[ClassDetailOut] = []
        for doc in docs:
            if not doc.exists:
                continue
            data = doc.to_dict()
            modality_id = data.get("modalityId", "")
            modality_name = (
                modality_names.get(modality_id)
                or data.get("modality")
                or modality_id
            )
            schedule_items = data.get("schedule") or []
            schedule_str = self._format_schedule(schedule_items)
            result.append(
                ClassDetailOut(
                    id=doc.id,
                    name=data.get("name", doc.id),
                    modality_id=modality_id,
                    modality_name=modality_name,
                    schedule=schedule_str,
                    schedule_items=schedule_items,
                    teacher=data.get("teacherName") or data.get("teacher"),
                    location=data.get("location"),
                    active=data.get("active", True),
                )
            )
        return result

    @staticmethod
    def _format_schedule(items: list[dict]) -> str:
        if not items:
            return ""
        day_short = {
            "mon": "Seg", "tue": "Ter", "wed": "Qua", "thu": "Qui",
            "fri": "Sex", "sat": "Sáb", "sun": "Dom",
        }
        days = "/".join(day_short.get(i.get("day", ""), "") for i in items)
        first = items[0]
        return f"{days} {first.get('startTime', '')}–{first.get('endTime', '')}"

    def _resolve_user_name(self, db, uid: Optional[str]) -> Optional[str]:
        if not uid:
            return None
        if uid == "system":
            return "Sistema"
        doc = db.collection(self._USERS).document(uid).get()
        if doc.exists:
            return doc.to_dict().get("name")
        return None

    @staticmethod
    def _derive_age_category(birth_date: Optional[str]) -> Optional[str]:
        """Returns 'child' if age < 18, 'adult' otherwise."""
        if not birth_date:
            return None
        try:
            from datetime import date
            birth = datetime.strptime(birth_date, "%d/%m/%Y").date()
            today = date.today()
            age = (
                today.year
                - birth.year
                - ((today.month, today.day) < (birth.month, birth.day))
            )
            return "child" if age < 18 else "adult"
        except (ValueError, TypeError):
            return None

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

    def _send_student_dependents_to_anamnese(
        self,
        db,
        project_id: str,
        guardian_uid: str,
        actor_uid: str,
        actor_name: str,
    ) -> None:
        """When guardian transitions to waiting_medical_history, also send
        any student dependents to waiting_medical_history so the guardian
        can fill all anamneses in the same session."""
        now_iso = datetime.now(timezone.utc).isoformat()
        history_service = AccountHistoryService()
        deps = (
            db.collection(self._USERS)
            .where("guardianUid", "==", guardian_uid)
            .where("isDependent", "==", True)
            .stream()
        )
        for dep_doc in deps:
            dep = dep_doc.to_dict()
            if dep.get("approvalStatus") == AccountStatus.PENDING_APPROVAL:
                dep_doc.reference.update(
                    {
                        "approvalStatus": AccountStatus.WAITING_MEDICAL_HISTORY,
                        "updatedAt": now_iso,
                        "lastUpdatedBy": actor_uid,
                    }
                )
                history_service.record(
                    uid=dep_doc.id,
                    project_id=project_id,
                    event_type="approval",
                    event_subtype="approve_to_medical",
                    actor_uid=actor_uid,
                    actor_name=actor_name,
                    actor_roles=[],
                    description=self._describe_transition(
                        "approve_to_medical", actor_name,
                    ),
                )

    def _auto_approve_dependents(
        self,
        db,
        project_id: str,
        guardian_uid: str,
        actor_uid: str,
        actor_name: str,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        history_service = AccountHistoryService()
        deps = (
            db.collection(self._USERS)
            .where("guardianUid", "==", guardian_uid)
            .where("isDependent", "==", True)
            .stream()
        )
        for dep_doc in deps:
            dep = dep_doc.to_dict()
            dep_status = dep.get("approvalStatus", "")
            if dep_status == AccountStatus.PENDING_APPROVAL:
                # Send student dependents to anamnese (not straight to approved)
                dep_doc.reference.update(
                    {
                        "approvalStatus": AccountStatus.WAITING_MEDICAL_HISTORY,
                        "updatedAt": now_iso,
                        "lastUpdatedBy": actor_uid,
                    }
                )
                history_service.record(
                    uid=dep_doc.id,
                    project_id=project_id,
                    event_type="approval",
                    event_subtype="approve_to_medical",
                    actor_uid=actor_uid,
                    actor_name=actor_name,
                    actor_roles=[],
                    description=self._describe_transition(
                        "approve_to_medical", actor_name,
                    ),
                )
            elif dep_status == AccountStatus.PENDING_MEDICAL_HISTORY_APPROVAL:
                # Already submitted anamnese — approve and activate
                dep_doc.reference.update(
                    {
                        "approvalStatus": AccountStatus.APPROVED,
                        "approvedBy": actor_uid,
                        "approvedAt": now_iso,
                        "updatedAt": now_iso,
                        "lastUpdatedBy": actor_uid,
                    }
                )
                self._activate_membership(
                    db, project_id, dep_doc.id
                )
                history_service.record(
                    uid=dep_doc.id,
                    project_id=project_id,
                    event_type="approval",
                    event_subtype="approve_medical",
                    actor_uid=actor_uid,
                    actor_name=actor_name,
                    actor_roles=[],
                    description=self._describe_transition(
                        "approve_medical", actor_name,
                    ),
                )

    def _build_account_out(
        self,
        uid: str,
        user: dict,
        roles: list[str],
        status: str,
        transitions: list,
        class_names_map: dict[str, str] | None = None,
        *,
        modality_names: list[str] | None = None,
        guardian_name: str | None = None,
        guardian_photo_url: str | None = None,
        dependents: list | None = None,
    ) -> AccountOut:
        class_ids = user.get("classIds", [])
        names_map = class_names_map or {}
        class_names = [
            names_map.get(cid, cid) for cid in class_ids
        ]
        addr_data = user.get("address")
        address = None
        if addr_data and isinstance(addr_data, dict):
            address = AddressOut(
                postal_code=addr_data.get("postalCode"),
                street=addr_data.get("street"),
                number=addr_data.get("number"),
                complement=addr_data.get("complement"),
                neighborhood=addr_data.get("neighborhood"),
                city=addr_data.get("city"),
                state=addr_data.get("state"),
            )

        # Graduation (raw dict, not typed like detail page)
        graduation_raw = user.get("graduation")
        graduation = graduation_raw if graduation_raw else None

        return AccountOut(
            uid=uid,
            name=user.get("name", ""),
            email=user.get("email", ""),
            roles=roles,
            status=status,
            email_verified=user.get("emailVerified", False),
            photo_url=user.get("photoUrl"),
            birth_date=user.get("birthDate"),
            gender=user.get("gender"),
            phone=user.get("phone"),
            whatsapp=user.get("whatsapp"),
            address=address,
            created_at=user.get("createdAt"),
            is_dependent=user.get("isDependent", False),
            guardian_uid=user.get("guardianUid"),
            guardian_name=guardian_name,
            guardian_photo_url=guardian_photo_url,
            class_ids=class_ids,
            class_names=class_names,
            modality_names=modality_names or [],
            graduation=graduation,
            available_actions=[
                AccountAction(
                    action=t.action,
                    label=t.label,
                    target_status=t.target.value,
                )
                for t in transitions
            ],
            dependents=dependents or [],
        )

    _APP_URL = "https://spartacus.app.br"

    _ACTION_EMAIL_MAP: dict[str, dict] = {
        "approve": {
            "title": "Cadastro aprovado!",
            "message": (
                "Parabéns! Seu cadastro no Projeto Spartacus foi aprovado. "
                "Agora você pode acessar o nosso aplicativo e a plataforma "
                "digital Spartacus. Estamos muito felizes em ter você com a gente!"
            ),
            "cta_text": "Acessar plataforma",
        },
        "reject": {
            "title": "Atualização sobre seu cadastro",
            "message": (
                "Gostaríamos de informar que, neste momento, não foi possível "
                "aprovar seu cadastro no Projeto Spartacus. Sabemos que isso "
                "pode ser frustrante, mas queremos que saiba que as portas do "
                "Spartacus continuam abertas. Caso tenha dúvidas ou queira "
                "tentar novamente, entre em contato conosco."
            ),
        },
        "approve_to_medical": {
            "title": "Próximo passo: anamnese",
            "message": (
                "Seu cadastro avançou para a próxima etapa! Para dar continuidade, "
                "acesse o aplicativo Spartacus e preencha o formulário de anamnese. "
                "Ele é rápido e importante para garantir a segurança nas atividades."
            ),
            "cta_text": "Preencher anamnese",
        },
        "approve_medical": {
            "title": "Anamnese aprovada!",
            "message": (
                "Sua anamnese foi analisada e aprovada pela equipe do Spartacus. "
                "Seu cadastro está completo e você já pode participar das atividades!"
            ),
            "cta_text": "Acessar plataforma",
        },
        "request_revision": {
            "title": "Revisão cadastral solicitada",
            "message": (
                "A equipe do Spartacus identificou que alguns dados do seu "
                "cadastro precisam ser revisados. Acesse o aplicativo para "
                "verificar e atualizar as informações solicitadas."
            ),
            "cta_text": "Revisar cadastro",
        },
        "submit_medical_history": {
            "title": "Anamnese enviada",
            "message": (
                "Sua ficha de anamnese foi recebida com sucesso! "
                "Ela será analisada pela equipe do Spartacus. "
                "Você receberá uma notificação quando a análise for concluída."
            ),
        },
    }

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

        config = self._ACTION_EMAIL_MAP.get(action)
        if not config:
            return None

        from app.events.models import AccountNotificationPayload

        return DomainEvent(
            id=f"account.{action}",
            payload=AccountNotificationPayload(
                to=email,
                name=user.get("name", ""),
                title=config["title"],
                message=config["message"],
                cta_text=config.get("cta_text", ""),
                cta_url=self._APP_URL if config.get("cta_text") else "",
            ),
        )
