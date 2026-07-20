"""GraduationService — belt/degree progression driven by a rules matrix.

Reads the `graduation_systems` matrix (per modality + age band) and the
students' inline graduation on the user doc. Powers the staff graduations
dashboard: approve a student's pending (app-inserted) graduation, add a
degree, or promote to the next belt — each validated against the matrix and
the student's current age.
"""

from datetime import datetime, timezone

from firebase_admin import firestore

from app.events.models import DomainEvent, GraduationEventPayload
from app.logging.decorator import log
from app.models.graduation_system import (
    AgeBand,
    Belt,
    GraduationDashboardOut,
    GraduationStudentCard,
    GraduationSystem,
    NextBelt,
    RosterFamily,
    RosterOut,
    RosterPerson,
    RosterTurma,
)
from app.services.account_history_service import AccountHistoryService
from app.services.attendance_service import _calc_age, _initials


def _slug(key: str) -> str:
    return (key or "").strip().lower().replace(" ", "-")


class GraduationService:
    _USERS = "users"
    _MEMBERSHIPS = "memberships"
    _CLASSES = "classes"
    _MODALITIES = "modalities"
    _SYSTEMS = "graduation_systems"

    # ── Matrix ───────────────────────────────────────────────────────────

    @log
    def get_systems(self, project_id: str) -> list[GraduationSystem]:
        db = firestore.client()
        docs = (
            db.collection(self._SYSTEMS)
            .where("projectId", "==", project_id)
            .stream()
        )
        return [self._doc_to_system(d.to_dict()) for d in docs]

    def _load_system(
        self, db, project_id: str, slug: str,
    ) -> GraduationSystem | None:
        doc = (
            db.collection(self._SYSTEMS).document(f"{project_id}_{slug}").get()
        )
        if not doc.exists:
            return None
        return self._doc_to_system(doc.to_dict())

    @staticmethod
    def _doc_to_system(data: dict) -> GraduationSystem:
        return GraduationSystem(
            modality_slug=data.get("modalitySlug", ""),
            modality_name=data.get("modalityName", ""),
            type=data.get("type", "linear"),
            age_bands=[
                AgeBand(
                    min_age=b.get("minAge", 0),
                    max_age=b.get("maxAge", 200),
                    belts=[
                        Belt(
                            order=x.get("order", i),
                            slug=x.get("slug", ""),
                            name=x.get("name", ""),
                            color=x.get("color", ""),
                            max_degree=x.get("maxDegree", 0),
                        )
                        for i, x in enumerate(b.get("belts", []))
                    ],
                )
                for b in data.get("ageBands", [])
            ],
        )

    @staticmethod
    def _band_for_age(
        system: GraduationSystem, age: int | None,
    ) -> AgeBand | None:
        if not system.age_bands:
            return None
        if system.type == "linear" or age is None:
            return system.age_bands[0]
        for band in system.age_bands:
            if band.min_age <= age <= band.max_age:
                return band
        return None

    # ── Progression resolution ───────────────────────────────────────────

    def resolve_progression(
        self,
        system: GraduationSystem,
        age: int | None,
        belt_slug: str | None,
        degree: int,
    ) -> dict:
        """Resolve current position + next steps from the matrix.

        Returns belt_name/color, max_degree, can_add_degree, next_belt,
        out_of_band. Tolerates a current belt that is not in the resolved
        age band (out_of_band → no automatic actions).
        """
        band = self._band_for_age(system, age)
        if band is None:
            return {
                "belt_name": None, "color": None, "max_degree": 0,
                "can_add_degree": False, "next_belt": None,
                "out_of_band": True,
            }
        belts = band.belts
        idx = -1
        if belt_slug:
            target = _slug(belt_slug)
            idx = next(
                (i for i, b in enumerate(belts)
                 if b.slug == target or _slug(b.name) == target),
                -1,
            )
        # No current belt → next is the first belt of the band
        if not belt_slug:
            nb = belts[0] if belts else None
            return {
                "belt_name": None, "color": None, "max_degree": 0,
                "can_add_degree": False,
                "next_belt": self._to_next(nb),
                "out_of_band": False,
            }
        if idx == -1:
            # belt exists but not in this band (e.g. aged into a new band)
            return {
                "belt_name": belt_slug, "color": None, "max_degree": 0,
                "can_add_degree": False, "next_belt": None,
                "out_of_band": True,
            }
        belt = belts[idx]
        nb = belts[idx + 1] if idx + 1 < len(belts) else None
        return {
            "belt_name": belt.name,
            "color": belt.color,
            "max_degree": belt.max_degree,
            "can_add_degree": degree < belt.max_degree,
            "next_belt": self._to_next(nb),
            "out_of_band": False,
        }

    @staticmethod
    def _to_next(belt: Belt | None) -> NextBelt | None:
        if belt is None:
            return None
        return NextBelt(
            slug=belt.slug, name=belt.name,
            color=belt.color, max_degree=belt.max_degree,
        )

    # ── Roster (grouped by guardian) ─────────────────────────────────────

    def _to_roster_person(
        self, p: dict, systems: dict[str, GraduationSystem],
    ) -> RosterPerson:
        """Build a RosterPerson from a plain user dict. Pure (no I/O).

        `p` keys: uid, name, nickname, birthDate, photoUrl, guardianUid,
        graduation (dict), enrolledSlugs (set), turmas (list[RosterTurma]).
        One card per modality that has an entry OR an enrollment WITH a
        system. Modalities with neither a system nor an entry are skipped
        (e.g. MMA → shown only as a turma).
        """
        grad_raw = {
            _slug(k): v for k, v in (p.get("graduation") or {}).items()
        }
        enrolled = set(p.get("enrolledSlugs") or [])
        age = _calc_age(p.get("birthDate"))
        cards: list[GraduationStudentCard] = []
        for slug in sorted(set(grad_raw) | enrolled):
            system = systems.get(slug)
            entry = grad_raw.get(slug)
            if system is None and not entry:
                continue  # no rules, no data → no graduation block
            belt = entry.get("belt") if entry else None
            degree = entry.get("degree", 0) if entry else 0
            status = entry.get("status", "approved") if entry else "none"
            if system is not None:
                prog = self.resolve_progression(system, age, belt, degree)
                modality_name = system.modality_name
            else:
                prog = {
                    "belt_name": belt, "color": None, "max_degree": 0,
                    "can_add_degree": False, "next_belt": None,
                    "out_of_band": False,
                }
                modality_name = slug
            cards.append(GraduationStudentCard(
                user_id=p["uid"],
                name=p.get("name", ""),
                nickname=p.get("nickname"),
                initials=_initials(p.get("name", "")),
                age=age,
                photo_url=p.get("photoUrl"),
                is_dependent=bool(p.get("guardianUid")),
                guardian_uid=p.get("guardianUid"),
                modality_slug=slug,
                modality_name=modality_name,
                belt=belt,
                belt_name=prog["belt_name"],
                color=prog["color"],
                degree=degree,
                status=status,
                max_degree=prog["max_degree"],
                can_add_degree=prog["can_add_degree"],
                next_belt=prog["next_belt"],
                out_of_band=prog["out_of_band"],
                can_undo=bool(entry and entry.get("prev")),
            ))
        return RosterPerson(
            user_id=p["uid"],
            display_name=p.get("nickname") or p.get("name", ""),
            name=p.get("name", ""),
            initials=_initials(p.get("name", "")),
            photo_url=p.get("photoUrl"),
            age=age,
            is_dependent=bool(p.get("guardianUid")),
            guardian_uid=p.get("guardianUid"),
            turmas=p.get("turmas") or [],
            graduations=cards,
        )

    def assemble_roster(
        self, people: list[dict], systems: dict[str, GraduationSystem],
    ) -> RosterOut:
        """Group people into families (guardian → dependents). Pure (no I/O).

        Each person dict is as for `_to_roster_person` plus `roles: set[str]`.
        - A person with dependents is a guardian card; if not a student its
          graduations are cleared (container only, item 7).
        - A dependent is nested under its guardian (item 3/4).
        - A standalone student is its own family.
        - Anyone who is neither a student nor a guardian is skipped.
        """
        persons = {p["uid"]: p for p in people}
        rendered = {uid: self._to_roster_person(p, systems)
                    for uid, p in persons.items()}

        deps_by_guardian: dict[str, list[str]] = {}
        for uid, p in persons.items():
            g = p.get("guardianUid")
            if g:
                deps_by_guardian.setdefault(g, []).append(uid)

        families: list[RosterFamily] = []
        for uid, p in persons.items():
            if p.get("guardianUid"):
                continue  # dependents are attached to their guardian below
            dep_uids = deps_by_guardian.get(uid, [])
            roles = p.get("roles") or set()
            is_guardian = bool(dep_uids) or "guardian" in roles
            is_student = "student" in roles
            if not is_guardian and not is_student:
                continue  # e.g. supporter with no student role, no deps
            guardian_person = rendered[uid]
            guardian_is_student = is_student
            if not guardian_is_student:
                guardian_person = guardian_person.model_copy(
                    update={"graduations": []},
                )
            families.append(RosterFamily(
                guardian=guardian_person,
                guardian_is_student=guardian_is_student,
                dependents=[rendered[d] for d in dep_uids],
            ))

        families.sort(key=lambda f: f.guardian.display_name.lower())

        pending = 0
        for fam in families:
            for person in [fam.guardian, *fam.dependents]:
                pending += sum(
                    1 for c in person.graduations if c.status == "pending"
                )
        return RosterOut(families=families, pending_count=pending)

    @log
    def build_roster(self, project_id: str) -> RosterOut:
        """Load active members + guardians and assemble the grouped roster."""
        db = firestore.client()
        systems = {s.modality_slug: s for s in self.get_systems(project_id)}

        # modality doc id ("{project}_{slug}") → {name, slug}
        modalities: dict[str, dict] = {}
        for m in (
            db.collection(self._MODALITIES)
            .where("projectId", "==", project_id).stream()
        ):
            md = m.to_dict()
            modalities[m.id] = {
                "name": md.get("name", ""),
                "slug": _slug(md.get("name", "")),
            }

        # class id → {modality_slug, modality_name, class_name}
        class_info: dict[str, dict] = {}
        for c in (
            db.collection(self._CLASSES)
            .where("projectId", "==", project_id).stream()
        ):
            cd = c.to_dict()
            mod = modalities.get(cd.get("modalityId", ""), {})
            class_info[c.id] = {
                "modality_slug": mod.get("slug", ""),
                "modality_name": mod.get("name", ""),
                "class_name": cd.get("name", ""),
            }

        # active memberships → roles per uid
        roles_by_uid: dict[str, set[str]] = {}
        for m in (
            db.collection(self._MEMBERSHIPS)
            .where("projectId", "==", project_id)
            .where("status", "==", "active").stream()
        ):
            md = m.to_dict()
            uid = md.get("userId", "")
            if uid:
                roles_by_uid.setdefault(uid, set()).update(md.get("roles") or [])

        # user docs for members
        user_docs: dict[str, dict] = {}
        if roles_by_uid:
            refs = [db.collection(self._USERS).document(u) for u in roles_by_uid]
            for doc in db.get_all(refs):
                if doc.exists:
                    user_docs[doc.id] = doc.to_dict()

        # ensure guardians of members are present even if not active members
        missing_guardians = {
            ud.get("guardianUid") for ud in user_docs.values()
            if ud.get("guardianUid") and ud.get("guardianUid") not in user_docs
        }
        if missing_guardians:
            grefs = [
                db.collection(self._USERS).document(g)
                for g in missing_guardians if g
            ]
            for doc in db.get_all(grefs):
                if doc.exists:
                    user_docs[doc.id] = doc.to_dict()
                    roles_by_uid.setdefault(doc.id, set()).add("guardian")

        people: list[dict] = []
        for uid, ud in user_docs.items():
            class_ids = ud.get("classIds", []) or []
            turmas = [
                RosterTurma(
                    modality_name=class_info[cid]["modality_name"],
                    class_name=class_info[cid]["class_name"],
                )
                for cid in class_ids
                if cid in class_info and class_info[cid]["modality_name"]
            ]
            enrolled_slugs = {
                class_info[cid]["modality_slug"]
                for cid in class_ids
                if cid in class_info and class_info[cid]["modality_slug"]
            }
            people.append({
                "uid": uid,
                "name": ud.get("name", ""),
                "nickname": ud.get("nickname"),
                "birthDate": ud.get("birthDate"),
                "photoUrl": ud.get("photoUrl"),
                "guardianUid": ud.get("guardianUid"),
                "roles": roles_by_uid.get(uid, set()),
                "graduation": ud.get("graduation") or {},
                "enrolledSlugs": enrolled_slugs,
                "turmas": turmas,
            })

        return self.assemble_roster(people, systems)

    # ── Dashboard ────────────────────────────────────────────────────────

    @log
    def dashboard(
        self, project_id: str, modality_slug: str,
    ) -> GraduationDashboardOut:
        db = firestore.client()
        slug = _slug(modality_slug)
        system = self._load_system(db, project_id, slug)
        modality_doc_id = f"{project_id}_{slug}"

        modality_name = system.modality_name if system else slug
        if not system:
            mod_doc = (
                db.collection(self._MODALITIES).document(modality_doc_id).get()
            )
            if mod_doc.exists:
                modality_name = mod_doc.to_dict().get("name", slug)

        # Classes of this modality → enrolled classIds
        class_ids = {
            c.id for c in db.collection(self._CLASSES)
            .where("projectId", "==", project_id)
            .where("modalityId", "==", modality_doc_id)
            .stream()
        }

        # Active students
        student_uids: list[str] = []
        seen: set[str] = set()
        for m in (
            db.collection(self._MEMBERSHIPS)
            .where("projectId", "==", project_id)
            .where("status", "==", "active")
            .stream()
        ):
            md = m.to_dict()
            uid = md.get("userId", "")
            if uid and uid not in seen and "student" in (md.get("roles") or []):
                seen.add(uid)
                student_uids.append(uid)

        cards: list[GraduationStudentCard] = []
        if student_uids:
            refs = [db.collection(self._USERS).document(u) for u in student_uids]
            for doc in db.get_all(refs):
                if not doc.exists:
                    continue
                ud = doc.to_dict()
                grad_raw = {
                    _slug(k): v for k, v in (ud.get("graduation") or {}).items()
                }
                entry = grad_raw.get(slug)
                enrolled = bool(set(ud.get("classIds", []) or []) & class_ids)
                if not enrolled and not entry:
                    continue

                age = _calc_age(ud.get("birthDate"))
                belt = entry.get("belt") if entry else None
                degree = entry.get("degree", 0) if entry else 0
                status = entry.get("status", "approved") if entry else "none"

                prog = (
                    self.resolve_progression(system, age, belt, degree)
                    if system
                    else {
                        "belt_name": belt, "color": None, "max_degree": 0,
                        "can_add_degree": False, "next_belt": None,
                        "out_of_band": False,
                    }
                )

                cards.append(GraduationStudentCard(
                    user_id=doc.id,
                    name=ud.get("name", ""),
                    nickname=ud.get("nickname"),
                    initials=_initials(ud.get("name", "")),
                    age=age,
                    photo_url=ud.get("photoUrl"),
                    is_dependent=bool(ud.get("guardianUid")),
                    guardian_uid=ud.get("guardianUid"),
                    guardian_name=None,
                    belt=belt,
                    belt_name=prog["belt_name"],
                    color=prog["color"],
                    degree=degree,
                    status=status,
                    max_degree=prog["max_degree"],
                    can_add_degree=prog["can_add_degree"],
                    next_belt=prog["next_belt"],
                    out_of_band=prog["out_of_band"],
                    can_undo=bool(entry and entry.get("prev")),
                ))

        # Guardian names for dependents
        guardian_uids = {c.guardian_uid for c in cards if c.guardian_uid}
        if guardian_uids:
            grefs = [
                db.collection(self._USERS).document(g) for g in guardian_uids
            ]
            names = {
                d.id: d.to_dict().get("name")
                for d in db.get_all(grefs) if d.exists
            }
            for c in cards:
                if c.guardian_uid:
                    c.guardian_name = names.get(c.guardian_uid)

        cards.sort(key=lambda c: c.name.lower())
        return GraduationDashboardOut(
            modality_slug=slug,
            modality_name=modality_name,
            has_system=system is not None,
            students=cards,
        )

    # ── Actions ──────────────────────────────────────────────────────────

    def _load_user_grad(self, db, uid: str):
        ref = db.collection(self._USERS).document(uid)
        snap = ref.get()
        if not snap.exists:
            raise LookupError("Aluno não encontrado")
        ud = snap.to_dict()
        grad = {_slug(k): v for k, v in (ud.get("graduation") or {}).items()}
        return ref, ud, grad

    @staticmethod
    def _actor_name(db, actor_uid: str) -> str:
        doc = db.collection("users").document(actor_uid).get()
        return doc.to_dict().get("name", "") if doc.exists else ""

    @log
    def approve(
        self, project_id: str, uid: str, modality: str, actor_uid: str,
    ) -> DomainEvent:
        db = firestore.client()
        slug = _slug(modality)
        ref, ud, grad = self._load_user_grad(db, uid)
        entry = grad.get(slug)
        if not entry:
            raise ValueError("Aluno não tem graduação nesta modalidade")

        now = datetime.now(timezone.utc).isoformat()
        actor_name = self._actor_name(db, actor_uid)
        entry.update({
            "status": "approved",
            "gradedBy": actor_uid,
            "gradedByName": actor_name,
            "gradedAt": now,
            "lockedByStudent": True,
            # snapshot so the action can be undone if it was a mistake
            "prev": {
                "belt": entry.get("belt"),
                "degree": entry.get("degree", 0),
                "status": "pending",
            },
        })
        grad[slug] = entry
        ref.update({"graduation": grad})

        system = self._load_system(db, project_id, slug)
        modality_name = system.modality_name if system else slug
        belt_label = self._belt_label(system, entry)

        AccountHistoryService().record(
            uid=uid, project_id=project_id, event_type="graduation",
            event_subtype="approved", actor_uid=actor_uid,
            actor_name=actor_name, actor_roles=[],
            description=f"Graduação aprovada em {modality_name}: {belt_label}",
        )
        first = (ud.get("name", "").split() or [""])[0]
        return DomainEvent(
            id="graduation.approved",
            payload=GraduationEventPayload(
                entity_id=uid, target_uid=uid,
                target_name=ud.get("name", ""),
                author_uid=actor_uid, author_name=actor_name,
                title="Parabéns pela graduação! 🥋",
                body=(
                    f"{first}, sua graduação em {modality_name} foi confirmada: "
                    f"{belt_label}. Continue treinando firme — Oss!"
                ),
            ),
        )

    @log
    def reject(
        self, project_id: str, uid: str, modality: str, actor_uid: str,
    ) -> DomainEvent:
        """Reject the student's first-insert graduation (correction needed).

        Unlocks the modality so the student can edit and resubmit it from the
        app. Only a pending graduation can be rejected.
        """
        db = firestore.client()
        slug = _slug(modality)
        ref, ud, grad = self._load_user_grad(db, uid)
        entry = grad.get(slug)
        if not entry:
            raise ValueError("Aluno não tem graduação nesta modalidade")
        if entry.get("status") != "pending":
            raise ValueError("Só é possível reprovar uma graduação pendente")

        now = datetime.now(timezone.utc).isoformat()
        actor_name = self._actor_name(db, actor_uid)
        entry.update({
            "status": "rejected",
            "lockedByStudent": False,  # student can edit & resubmit
            "gradedBy": actor_uid,
            "gradedByName": actor_name,
            "gradedAt": now,
        })
        entry.pop("prev", None)
        grad[slug] = entry
        ref.update({"graduation": grad})

        system = self._load_system(db, project_id, slug)
        modality_name = system.modality_name if system else slug
        AccountHistoryService().record(
            uid=uid, project_id=project_id, event_type="graduation",
            event_subtype="rejected", actor_uid=actor_uid,
            actor_name=actor_name, actor_roles=[],
            description=(
                f"Graduação reprovada em {modality_name} — "
                "aluno pode corrigir e reenviar"
            ),
        )
        first = (ud.get("name", "").split() or [""])[0]
        return DomainEvent(
            id="graduation.rejected",
            payload=GraduationEventPayload(
                entity_id=uid, target_uid=uid,
                target_name=ud.get("name", ""),
                author_uid=actor_uid, author_name=actor_name,
                title="Vamos ajustar sua graduação 🥋",
                body=(
                    f"{first}, precisamos corrigir sua graduação em "
                    f"{modality_name}. É rapidinho: abra o app, ajuste a faixa "
                    "e reenvie. Estamos aqui pra ajudar!"
                ),
            ),
        )

    @log
    def promote(
        self, project_id: str, uid: str, modality: str,
        actor_uid: str, kind: str,
    ) -> DomainEvent:
        db = firestore.client()
        slug = _slug(modality)
        ref, ud, grad = self._load_user_grad(db, uid)
        system = self._load_system(db, project_id, slug)
        if not system:
            raise ValueError("Modalidade sem sistema de graduação")

        age = _calc_age(ud.get("birthDate"))
        existed = slug in grad
        entry = grad.get(slug) or {"belt": None, "degree": 0, "prajied": None}
        # snapshot prior state so the promotion can be undone
        prev = {
            "belt": entry.get("belt") if existed else None,
            "degree": entry.get("degree", 0) if existed else 0,
            "status": entry.get("status") if existed else None,
        }
        prog = self.resolve_progression(
            system, age, entry.get("belt"), entry.get("degree", 0),
        )

        if kind == "degree":
            if prog["out_of_band"] or not prog["can_add_degree"]:
                raise ValueError("Não é possível adicionar grau a esta faixa")
            entry["degree"] = entry.get("degree", 0) + 1
            subtype = "promoted_degree"
            body = (
                f"Você recebeu o {entry['degree']}º grau na faixa "
                f"{prog['belt_name']} ({system.modality_name})!"
            )
            belt_label = self._belt_label(system, entry)
        elif kind == "belt":
            nxt = prog["next_belt"]
            if nxt is None:
                raise ValueError("Não há próxima faixa para promover")
            entry["belt"] = nxt.slug
            entry["degree"] = 0
            subtype = "promoted_belt"
            body = (
                f"Parabéns! Você foi promovido para a faixa {nxt.name} "
                f"em {system.modality_name}!"
            )
            belt_label = nxt.name
        else:
            raise ValueError("Tipo de promoção inválido")

        now = datetime.now(timezone.utc).isoformat()
        actor_name = self._actor_name(db, actor_uid)
        entry.update({
            "status": "approved",
            "gradedBy": actor_uid,
            "gradedByName": actor_name,
            "gradedAt": now,
            "lockedByStudent": True,
            "prev": prev,
        })
        grad[slug] = entry
        ref.update({"graduation": grad})

        AccountHistoryService().record(
            uid=uid, project_id=project_id, event_type="graduation",
            event_subtype=subtype, actor_uid=actor_uid,
            actor_name=actor_name, actor_roles=[],
            description=(
                f"Promoção em {system.modality_name}: {belt_label}"
            ),
        )
        return DomainEvent(
            id="graduation.promoted",
            payload=GraduationEventPayload(
                entity_id=uid, target_uid=uid,
                target_name=ud.get("name", ""),
                author_uid=actor_uid, author_name=actor_name,
                title="Você foi graduado! 🥋", body=body,
            ),
        )

    @log
    def undo(
        self, project_id: str, uid: str, modality: str, actor_uid: str,
    ) -> None:
        """Revert the last graduation change (approve/promote) — corrections.

        Restores the snapshot taken at the time of the action. If the prior
        state had no belt (the student was graded from nothing), the entry is
        removed entirely. History only; no push.
        """
        db = firestore.client()
        slug = _slug(modality)
        ref, ud, grad = self._load_user_grad(db, uid)
        entry = grad.get(slug)
        if not entry or not entry.get("prev"):
            raise ValueError("Nada para desfazer nesta modalidade")

        prev = entry["prev"]
        now = datetime.now(timezone.utc).isoformat()
        actor_name = self._actor_name(db, actor_uid)

        if not prev.get("belt"):
            # was graded from nothing → back to "sem graduação"
            grad.pop(slug, None)
        else:
            entry["belt"] = prev.get("belt")
            entry["degree"] = prev.get("degree", 0)
            entry["status"] = prev.get("status") or "approved"
            entry["gradedBy"] = actor_uid
            entry["gradedByName"] = actor_name
            entry["gradedAt"] = now
            entry.pop("prev", None)
            grad[slug] = entry
        ref.update({"graduation": grad})

        system = self._load_system(db, project_id, slug)
        modality_name = system.modality_name if system else slug
        AccountHistoryService().record(
            uid=uid, project_id=project_id, event_type="graduation",
            event_subtype="undone", actor_uid=actor_uid,
            actor_name=actor_name, actor_roles=[],
            description=f"Graduação desfeita em {modality_name}",
        )

    @staticmethod
    def _belt_label(system: GraduationSystem | None, entry: dict) -> str:
        belt = entry.get("belt") or ""
        degree = entry.get("degree", 0)
        name = belt
        if system:
            for band in system.age_bands:
                for b in band.belts:
                    if b.slug == _slug(belt):
                        name = b.name
                        break
        return f"{name} · {degree}º grau" if degree else name
