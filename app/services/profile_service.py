from datetime import date, datetime, timezone

from fastapi import HTTPException
from firebase_admin import firestore

from app.logging.decorator import log
from app.models.account import AddressOut
from app.models.profile import (
    AddressUpdate,
    ClassesUpdate,
    CompetitionOut,
    CompetitionUpdate,
    DependentCreate,
    DependentOut,
    GraduationEntry,
    GraduationUpdate,
    ProfileOut,
    ProfileUpdate,
)


def _calculate_age(birth_date_str: str) -> int | None:
    try:
        birth = datetime.strptime(birth_date_str, "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return None
    today = date.today()
    return (
        today.year
        - birth.year
        - ((today.month, today.day) < (birth.month, birth.day))
    )


class ProfileService:
    _USERS = "users"
    _MEMBERSHIPS = "memberships"
    _CLASSES = "classes"
    _PROJECTS = "projects"

    # ── Read ─────────────────────────────────────────────────────────────

    @log
    def get_profile(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None = None,
    ) -> ProfileOut:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        db = firestore.client()
        user_doc = db.collection(self._USERS).document(target_uid).get()
        if not user_doc.exists:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        user = user_doc.to_dict()
        roles = self._get_roles(db, project_id, target_uid)
        class_ids = user.get("classIds", [])
        class_names_map = self._fetch_class_names(db, class_ids)

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

        graduation_raw = user.get("graduation")
        graduation = None
        if graduation_raw and isinstance(graduation_raw, dict):
            graduation = {
                k: GraduationEntry(**v)
                for k, v in graduation_raw.items()
                if isinstance(v, dict)
            }

        competition_raw = user.get("competition")
        competition = None
        if competition_raw and isinstance(competition_raw, dict):
            competition = self._build_competition_out(
                db, project_id, competition_raw, user.get("birthDate")
            )

        completion = self._calculate_completion(user, roles)

        return ProfileOut(
            uid=target_uid,
            name=user.get("name", ""),
            email=user.get("email"),
            birth_date=user.get("birthDate"),
            gender=user.get("gender"),
            phone=user.get("phone"),
            whatsapp=user.get("whatsapp"),
            tax_id=user.get("taxId"),
            photo_url=user.get("photoUrl"),
            roles=roles,
            approval_status=user.get("approvalStatus", ""),
            address=address,
            class_ids=class_ids,
            class_names=[class_names_map.get(cid, cid) for cid in class_ids],
            graduation=graduation,
            competition=competition,
            completion_percent=completion,
            is_dependent=user.get("isDependent", False),
            guardian_uid=user.get("guardianUid"),
        )

    # ── Update personal data ─────────────────────────────────────────────

    @log
    def update_profile(
        self,
        uid: str,
        acting_as: str | None,
        data: ProfileUpdate,
    ) -> None:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        updates = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.birth_date is not None:
            updates["birthDate"] = data.birth_date
        if data.gender is not None:
            updates["gender"] = data.gender
        if data.phone is not None:
            updates["phone"] = data.phone
        if data.whatsapp is not None:
            updates["whatsapp"] = data.whatsapp
        if data.tax_id is not None:
            updates["taxId"] = data.tax_id

        if not updates:
            return

        db = firestore.client()
        ref = db.collection(self._USERS).document(target_uid)
        if not ref.get().exists:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        ref.update(updates)

    # ── Update address ───────────────────────────────────────────────────

    @log
    def update_address(
        self,
        uid: str,
        acting_as: str | None,
        data: AddressUpdate,
    ) -> None:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        db = firestore.client()
        ref = db.collection(self._USERS).document(target_uid)
        if not ref.get().exists:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")
        ref.update(
            {
                "address": {
                    "postalCode": data.postal_code,
                    "street": data.street,
                    "number": data.number,
                    "complement": data.complement,
                    "neighborhood": data.neighborhood,
                    "city": data.city,
                    "state": data.state,
                }
            }
        )

    # ── Dependents ───────────────────────────────────────────────────────

    @log
    def list_dependents(
        self,
        project_id: str,
        uid: str,
    ) -> list[DependentOut]:
        db = firestore.client()
        deps = (
            db.collection(self._USERS)
            .where("guardianUid", "==", uid)
            .where("isDependent", "==", True)
            .stream()
        )

        all_class_ids: set[str] = set()
        dep_list: list[tuple[str, dict]] = []
        for doc in deps:
            data = doc.to_dict()
            all_class_ids.update(data.get("classIds", []))
            dep_list.append((doc.id, data))

        class_names_map = self._fetch_class_names(db, list(all_class_ids))

        results: list[DependentOut] = []
        for dep_uid, data in dep_list:
            roles = self._get_roles(db, project_id, dep_uid)
            class_ids = data.get("classIds", [])
            status = data.get("approvalStatus", "")
            reg_complete = status != "incomplete"

            results.append(
                DependentOut(
                    uid=dep_uid,
                    name=data.get("name", ""),
                    birth_date=data.get("birthDate"),
                    gender=data.get("gender"),
                    photo_url=data.get("photoUrl"),
                    roles=roles,
                    approval_status=status,
                    class_ids=class_ids,
                    class_names=[
                        class_names_map.get(cid, cid) for cid in class_ids
                    ],
                    registration_complete=reg_complete,
                )
            )
        return results

    @log
    def create_dependent(
        self,
        project_id: str,
        uid: str,
        data: DependentCreate,
    ) -> DependentOut:
        db = firestore.client()

        # Generate dependent UID
        timestamp = int(datetime.now(timezone.utc).timestamp() * 1000)
        dep_uid = f"{uid}_dep_{timestamp}"
        now = datetime.now(timezone.utc).isoformat()

        db.collection(self._USERS).document(dep_uid).set(
            {
                "name": data.name,
                "birthDate": data.birth_date,
                "gender": data.gender,
                "guardianUid": uid,
                "approvalStatus": "incomplete",
                "isDependent": True,
                "classIds": [],
                "createdAt": now,
            }
        )

        # Create membership with student role
        db.collection(self._MEMBERSHIPS).document(
            f"{project_id}_{dep_uid}"
        ).set(
            {
                "projectId": project_id,
                "userId": dep_uid,
                "roles": ["student"],
                "status": "pending_approval",
                "joined_at": now,
            }
        )

        return DependentOut(
            uid=dep_uid,
            name=data.name,
            birth_date=data.birth_date,
            gender=data.gender,
            roles=["student"],
            approval_status="incomplete",
            registration_complete=False,
        )

    # ── Classes ──────────────────────────────────────────────────────────

    @log
    def update_classes(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None,
        data: ClassesUpdate,
    ) -> None:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        db = firestore.client()
        user_ref = db.collection(self._USERS).document(target_uid)
        user_doc = user_ref.get()
        if not user_doc.exists:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        # Validate all class IDs exist and are active
        if data.class_ids:
            refs = [
                db.collection(self._CLASSES).document(cid)
                for cid in data.class_ids
            ]
            docs = db.get_all(refs)
            for doc in docs:
                if not doc.exists:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Turma não encontrada: {doc.id}",
                    )
                if not doc.to_dict().get("active", True):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Turma inativa: {doc.id}",
                    )

        user_data = user_doc.to_dict()
        roles = self._get_roles(db, project_id, target_uid)

        # If user is not a student and is adding classes, add student role
        needs_student_role = (
            data.class_ids
            and "student" not in roles
        )

        user_ref.update({"classIds": data.class_ids})

        if needs_student_role:
            mem_ref = db.collection(self._MEMBERSHIPS).document(
                f"{project_id}_{target_uid}"
            )
            mem_doc = mem_ref.get()
            if mem_doc.exists:
                current_roles = mem_doc.to_dict().get("roles", [])
                mem_ref.update({"roles": current_roles + ["student"]})

        # Auto-transition: incomplete → pending_approval when dependent has classes
        status = user_data.get("approvalStatus", "")
        if status == "incomplete" and data.class_ids:
            user_ref.update({"approvalStatus": "pending_approval"})

    # ── Graduation ───────────────────────────────────────────────────────

    @log
    def update_graduation(
        self,
        uid: str,
        acting_as: str | None,
        data: GraduationUpdate,
    ) -> None:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        db = firestore.client()
        ref = db.collection(self._USERS).document(target_uid)
        if not ref.get().exists:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        graduation_dict = {
            k: v.model_dump() for k, v in data.graduation.items()
        }
        ref.update({"graduation": graduation_dict})

    # ── Competition ──────────────────────────────────────────────────────

    @log
    def update_competition(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None,
        data: CompetitionUpdate,
    ) -> None:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian_of(uid, target_uid)

        db = firestore.client()
        ref = db.collection(self._USERS).document(target_uid)
        if not ref.get().exists:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        updates: dict = {}
        if data.weight_kg is not None:
            updates["competition.weightKg"] = data.weight_kg
        if data.target_categories is not None:
            updates["competition.targetCategories"] = data.target_categories

        if updates:
            ref.update(updates)

    # ── Private helpers ──────────────────────────────────────────────────

    def _assert_guardian_of(self, guardian_uid: str, target_uid: str) -> None:
        db = firestore.client()
        target_doc = db.collection(self._USERS).document(target_uid).get()
        if not target_doc.exists:
            raise HTTPException(status_code=404, detail="Dependente não encontrado")
        data = target_doc.to_dict()
        if data.get("guardianUid") != guardian_uid:
            raise HTTPException(
                status_code=403,
                detail="Acesso negado: você não é responsável deste dependente",
            )

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

    def _fetch_class_names(
        self, db, class_ids: list[str]
    ) -> dict[str, str]:
        if not class_ids:
            return {}
        refs = [
            db.collection(self._CLASSES).document(cid)
            for cid in class_ids
        ]
        docs = db.get_all(refs)
        return {
            doc.id: doc.to_dict().get("name", doc.id)
            for doc in docs
            if doc.exists
        }

    def _calculate_completion(self, user: dict, roles: list[str]) -> int:
        total = 0
        filled = 0

        # Personal data (required for all)
        total += 1
        if user.get("name") and user.get("birthDate") and user.get("gender"):
            filled += 1

        # Address (required for non-dependent users)
        if not user.get("isDependent"):
            total += 1
            addr = user.get("address")
            if addr and isinstance(addr, dict) and addr.get("postalCode"):
                filled += 1

        # Classes (required for students)
        student_roles = {"student", "instructor", "teacher"}
        if any(r in student_roles for r in roles):
            total += 1
            if user.get("classIds"):
                filled += 1

            # Graduation (required for students/instructors/teachers)
            total += 1
            if user.get("graduation"):
                filled += 1

        if total == 0:
            return 100
        return round(filled / total * 100)

    def _build_competition_out(
        self,
        db,
        project_id: str,
        competition_raw: dict,
        birth_date: str | None,
    ) -> CompetitionOut:
        weight_kg = competition_raw.get("weightKg")
        target_categories = competition_raw.get("targetCategories", [])

        age_category = None
        weight_category = None

        project_doc = db.collection(self._PROJECTS).document(project_id).get()
        if project_doc.exists:
            config = project_doc.to_dict().get("categoryConfig")
            if config:
                # Calculate age category
                if birth_date:
                    age = _calculate_age(birth_date)
                    if age is not None:
                        for cat in config.get("ageCategories", []):
                            min_age = cat.get("minAge", 0)
                            max_age = cat.get("maxAge")
                            if age >= min_age and (
                                max_age is None or age <= max_age
                            ):
                                age_category = cat.get("name")
                                break

                # Calculate weight category
                if weight_kg is not None:
                    for cat in config.get("weightCategories", []):
                        min_w = cat.get("minWeight", 0)
                        max_w = cat.get("maxWeight")
                        name = cat.get("name", "")
                        if name.lower() == "absoluto":
                            continue
                        if weight_kg >= min_w and (
                            max_w is None or weight_kg < max_w
                        ):
                            weight_category = name
                            break

        return CompetitionOut(
            weight_kg=weight_kg,
            target_categories=target_categories,
            calculated_age_category=age_category,
            calculated_weight_category=weight_category,
        )
