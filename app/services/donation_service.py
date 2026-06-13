from datetime import datetime, timezone

from fastapi import HTTPException
from firebase_admin import firestore

from app.events.models import (
    DomainEvent,
    DonationRegisteredPayload,
    ValidationPayload,
)
from app.logging.decorator import log
from app.models.account import GraduationEntry
from app.models.donation import (
    ITEM_LABELS,
    DonationConfig,
    DonationConfigItem,
    DonationConfigUpdate,
    DonationCreate,
    DonationDashboardOut,
    DonationHistoryItem,
    DonationHistoryOut,
    DonationOut,
    DonationRegisterReceived,
    DonationStudentCard,
)
from app.services.account_history_service import AccountHistoryService
from app.services.attendance_service import _calc_age, _initials


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class DonationService:
    _DONATIONS = "donations"
    _PROJECTS = "projects"

    @log
    def create(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None,
        data: DonationCreate,
    ) -> DonationOut:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian(uid, target_uid)

        db = firestore.client()
        month = data.month or _current_month()

        # Check duplicate
        existing = list(
            db.collection(self._DONATIONS)
            .where("projectId", "==", project_id)
            .where("userId", "==", target_uid)
            .where("month", "==", month)
            .limit(1)
            .stream()
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail="Doação já registrada para este mês",
            )

        now = datetime.now(timezone.utc).isoformat()
        doc_data = {
            "projectId": project_id,
            "userId": target_uid,
            "actingAs": acting_as,
            "item": data.item,
            "itemDescription": data.item_description,
            "month": month,
            "status": "pledged",
            "createdAt": now,
            "receivedAt": None,
            "receivedBy": None,
        }

        _, ref = db.collection(self._DONATIONS).add(doc_data)

        # Resolve user name for event payload
        user_doc = db.collection("users").document(target_uid).get()
        user_name = ""
        if user_doc.exists:
            user_name = user_doc.to_dict().get("name", "")

        item_label = ITEM_LABELS.get(data.item, data.item)
        desc = data.item_description or ""
        amount = f"{item_label}: {desc}" if desc else item_label

        # Record in history
        AccountHistoryService().record(
            uid=target_uid,
            project_id=project_id,
            event_type="donation",
            event_subtype="pledged",
            actor_uid=uid,
            actor_name=user_name,
            actor_roles=[],
            description=f"Doação registrada: {amount}",
        )

        event = DomainEvent(
            id="donation.registered",
            payload=DonationRegisteredPayload(
                entity_id=ref.id,
                source_entity_ref=f"donations/{ref.id}",
                source_entity_type="donations",
                target_uid=target_uid,
                target_name=user_name,
                author_uid=uid,
                author_name=user_name,
                donation_amount=amount,
                donation_date=month,
            ),
        )

        return DonationOut(
            id=ref.id,
            item=data.item,
            item_label=item_label,
            item_description=data.item_description,
            month=month,
            status="pledged",
            created_at=now,
        ), event

    @log
    def get_current(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None = None,
    ) -> DonationOut | None:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian(uid, target_uid)

        db = firestore.client()
        month = _current_month()

        results = list(
            db.collection(self._DONATIONS)
            .where("projectId", "==", project_id)
            .where("userId", "==", target_uid)
            .where("month", "==", month)
            .limit(1)
            .stream()
        )
        if not results:
            return None

        doc = results[0]
        data = doc.to_dict()
        return DonationOut(
            id=doc.id,
            item=data.get("item", ""),
            item_label=ITEM_LABELS.get(
                data.get("item", ""), data.get("item", ""),
            ),
            item_description=data.get("itemDescription"),
            month=data.get("month", month),
            status=data.get("status", "pledged"),
            created_at=data.get("createdAt", ""),
        )

    # ── History ──────────────────────────────────────────────────────

    @log
    def get_history(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None = None,
        year: int | None = None,
    ) -> DonationHistoryOut:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian(uid, target_uid)

        return self._get_history_for(project_id, target_uid, year)

    @log
    def get_history_admin(
        self,
        project_id: str,
        target_uid: str,
        year: int | None = None,
    ) -> DonationHistoryOut:
        """Staff version of get_history (no guardian check). RFC-12.

        Authorization is enforced upstream (owner/assistant only).
        """
        return self._get_history_for(project_id, target_uid, year)

    def _get_history_for(
        self,
        project_id: str,
        target_uid: str,
        year: int | None = None,
    ) -> DonationHistoryOut:
        db = firestore.client()

        results = list(
            db.collection(self._DONATIONS)
            .where("projectId", "==", project_id)
            .where("userId", "==", target_uid)
            .stream()
        )

        # Index donations by month key
        by_month: dict[str, tuple] = {}
        for doc in results:
            data = doc.to_dict()
            month_str = data.get("month", "")
            by_month[month_str] = (doc.id, data)

        # Resolve receivedBy uids → names in batch
        receiver_uids = {
            d.get("receivedBy")
            for _, d in by_month.values()
            if d.get("receivedBy")
        }
        names: dict[str, str] = {}
        if receiver_uids:
            refs = [
                db.collection("users").document(u)
                for u in receiver_uids
            ]
            for udoc in db.get_all(refs):
                if udoc.exists:
                    names[udoc.id] = udoc.to_dict().get("name", "")

        # Build list for the year (or last 6 months when no year filter)
        items: list[DonationHistoryItem] = []
        if year is not None:
            month_keys = [
                (year, m) for m in range(12, 0, -1)
            ]
        else:
            now = datetime.now(timezone.utc)
            y, m = now.year, now.month
            month_keys = []
            for _ in range(6):
                month_keys.append((y, m))
                m -= 1
                if m == 0:
                    m = 12
                    y -= 1

        for y, m in month_keys:
            key = f"{y}-{m:02d}"
            month_label = self._format_month_label(key)

            if key in by_month:
                doc_id, data = by_month[key]
                item_code = data.get("item", "")
                status = data.get("status", "pledged")
                created_at = data.get("createdAt", "")
                created_label = self._format_created_date(created_at)
                status_label = (
                    "Validado" if status == "received" else "Aguardando"
                )
                received_by = data.get("receivedBy")
                items.append(DonationHistoryItem(
                    id=doc_id,
                    month=key,
                    month_label=month_label,
                    item=item_code,
                    item_label=ITEM_LABELS.get(item_code, item_code),
                    item_description=data.get("itemDescription"),
                    status=status,
                    status_label=status_label,
                    created_at=created_label,
                    received_by=received_by,
                    received_by_name=names.get(received_by) if received_by else None,
                    received_at=data.get("receivedAt"),
                ))
            else:
                items.append(DonationHistoryItem(
                    id=f"pending_{key}",
                    month=key,
                    month_label=month_label,
                    item=None,
                    item_label="",
                    item_description=None,
                    status="pending",
                    status_label="Pendente",
                    created_at="Ainda não registrado",
                ))

        return DonationHistoryOut(donations=items)

    @staticmethod
    def _format_month_label(month_str: str) -> str:
        """Convert '2026-03' to 'MARÇO / 2026'."""
        month_names = {
            1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO", 4: "ABRIL",
            5: "MAIO", 6: "JUNHO", 7: "JULHO", 8: "AGOSTO",
            9: "SETEMBRO", 10: "OUTUBRO", 11: "NOVEMBRO",
            12: "DEZEMBRO",
        }
        try:
            parts = month_str.split("-")
            y, m = int(parts[0]), int(parts[1])
            return f"{month_names.get(m, '')} / {y}"
        except (ValueError, IndexError):
            return month_str

    @staticmethod
    def _format_created_date(iso_str: str) -> str:
        """Convert ISO date to '15 de Março, 2026'."""
        month_names = {
            1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
            5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
            9: "Setembro", 10: "Outubro", 11: "Novembro",
            12: "Dezembro",
        }
        try:
            dt = datetime.fromisoformat(iso_str)
            return f"{dt.day} de {month_names.get(dt.month, '')}, {dt.year}"
        except (ValueError, TypeError):
            return iso_str

    # ── Donation config ──────────────────────────────────────────────

    @log
    def get_config(self, project_id: str) -> DonationConfig:
        db = firestore.client()
        doc = db.collection(self._PROJECTS).document(project_id).get()
        if not doc.exists:
            raise HTTPException(
                status_code=404, detail="Projeto não encontrado",
            )
        raw = doc.to_dict().get("donationConfig")
        if not raw:
            # Return default config
            return DonationConfig(
                items=[
                    DonationConfigItem(code=k, label=v)
                    for k, v in ITEM_LABELS.items()
                ],
                thank_you_message=(
                    "Muito obrigado pelo seu apoio! "
                    "Lembre-se de levar a sua doação "
                    "no próximo treino. Oss!"
                ),
            )
        return DonationConfig(
            items=[
                DonationConfigItem(**i)
                for i in raw.get("items", [])
            ],
            thank_you_message=raw.get("thankYouMessage", ""),
        )

    @log
    def update_config(
        self,
        project_id: str,
        data: DonationConfigUpdate,
    ) -> DonationConfig:
        db = firestore.client()
        ref = db.collection(self._PROJECTS).document(project_id)
        if not ref.get().exists:
            raise HTTPException(
                status_code=404, detail="Projeto não encontrado",
            )

        updates: dict = {}
        if data.items is not None:
            updates["donationConfig.items"] = [
                i.model_dump() for i in data.items
            ]
        if data.thank_you_message is not None:
            updates["donationConfig.thankYouMessage"] = (
                data.thank_you_message
            )

        if updates:
            ref.update(updates)

        return self.get_config(project_id)

    # ── Dashboard de doações (kanban) ────────────────────────────────

    @log
    def dashboard(self, project_id: str) -> DonationDashboardOut:
        """All students of the project + donation status for the month.

        Status per student: "none" (no donation, or rejected), "pledged"
        (registered, waiting approval) or "received" (approved).
        """
        db = firestore.client()
        month = _current_month()

        memberships = (
            db.collection("memberships")
            .where("projectId", "==", project_id)
            .where("status", "==", "active")
            .stream()
        )
        student_uids: list[str] = []
        seen: set[str] = set()
        for m in memberships:
            md = m.to_dict()
            uid = md.get("userId", "")
            if not uid or uid in seen:
                continue
            if "student" not in (md.get("roles") or []):
                continue
            seen.add(uid)
            student_uids.append(uid)

        don_by_user: dict[str, dict] = {}
        donations = (
            db.collection(self._DONATIONS)
            .where("projectId", "==", project_id)
            .where("month", "==", month)
            .stream()
        )
        for d in donations:
            dd = d.to_dict()
            don_by_user[dd.get("userId", "")] = {"id": d.id, **dd}

        cards: list[DonationStudentCard] = []
        if student_uids:
            refs = [
                db.collection("users").document(u) for u in student_uids
            ]
            for doc in db.get_all(refs):
                if not doc.exists:
                    continue
                ud = doc.to_dict()
                don = don_by_user.get(doc.id)
                status = "none"
                if don and don.get("status") in ("pledged", "received"):
                    status = don["status"]
                item = don.get("item") if don else None

                graduation_raw = ud.get("graduation") or {}
                graduation = {
                    k: GraduationEntry(
                        belt=v.get("belt", ""),
                        degree=v.get("degree", 0),
                        prajied=v.get("prajied"),
                    )
                    for k, v in graduation_raw.items()
                    if isinstance(v, dict)
                } or None

                cards.append(DonationStudentCard(
                    user_id=doc.id,
                    name=ud.get("name", ""),
                    nickname=ud.get("nickname"),
                    initials=_initials(ud.get("name", "")),
                    age=_calc_age(ud.get("birthDate")),
                    photo_url=ud.get("photoUrl"),
                    is_dependent=bool(ud.get("guardianUid")),
                    guardian_uid=ud.get("guardianUid"),
                    guardian_name=None,
                    graduation=graduation,
                    status=status,
                    donation_id=don.get("id") if don else None,
                    item=item,
                    item_label=(
                        ITEM_LABELS.get(item, item) if item else None
                    ),
                    item_description=(
                        don.get("itemDescription") if don else None
                    ),
                    registered_at=don.get("createdAt") if don else None,
                    validated_at=don.get("validatedAt") if don else None,
                ))

        # Resolve guardian names for dependents (batch fetch)
        guardian_uids = {c.guardian_uid for c in cards if c.guardian_uid}
        if guardian_uids:
            refs = [
                db.collection("users").document(g) for g in guardian_uids
            ]
            names = {
                d.id: d.to_dict().get("name")
                for d in db.get_all(refs)
                if d.exists
            }
            for c in cards:
                if c.guardian_uid:
                    c.guardian_name = names.get(c.guardian_uid)

        cards.sort(key=lambda c: c.name.lower())
        return DonationDashboardOut(
            month=month,
            month_label=self._format_month_label(month),
            students=cards,
        )

    @log
    def register_received(
        self,
        project_id: str,
        actor_uid: str,
        data: DonationRegisterReceived,
    ) -> tuple[DonationOut, DomainEvent]:
        """Staff registers a donation on behalf of a student, already
        approved (dashboard column 1 → 3, like attendance force-confirm).

        Updates the month's existing donation doc if there is one
        (e.g., a rejected donation being re-registered), otherwise
        creates it directly as received.
        """
        db = firestore.client()
        month = _current_month()
        now = datetime.now(timezone.utc).isoformat()
        target_uid = data.user_id

        user_doc = db.collection("users").document(target_uid).get()
        if not user_doc.exists:
            raise HTTPException(
                status_code=404, detail="Aluno não encontrado",
            )
        user_name = user_doc.to_dict().get("name", "")
        actor_doc = db.collection("users").document(actor_uid).get()
        actor_name = (
            actor_doc.to_dict().get("name", "") if actor_doc.exists else ""
        )

        item_label = ITEM_LABELS.get(data.item, data.item)
        desc = data.item_description or ""
        label = f"{item_label}: {desc}" if desc else item_label

        update_fields = {
            "item": data.item,
            "itemDescription": data.item_description,
            "status": "received",
            "receivedAt": now,
            "receivedBy": actor_uid,
            "validatedBy": actor_uid,
            "validatedAt": now,
        }
        existing = list(
            db.collection(self._DONATIONS)
            .where("projectId", "==", project_id)
            .where("userId", "==", target_uid)
            .where("month", "==", month)
            .limit(1)
            .stream()
        )
        if existing:
            ref = existing[0].reference
            cur = existing[0].to_dict()
            created_at = cur.get("createdAt", now)
            # Came from column 1 ("Sem doação") — a card here has no doc or a
            # rejected one; either way undo should return it to column 1.
            ref.update({**update_fields, "previousStatus": "absent"})
        else:
            created_at = now
            _, ref = db.collection(self._DONATIONS).add({
                "projectId": project_id,
                "userId": target_uid,
                "actingAs": None,
                "month": month,
                "createdAt": now,
                "previousStatus": "absent",
                **update_fields,
            })

        AccountHistoryService().record(
            uid=target_uid,
            project_id=project_id,
            event_type="donation",
            event_subtype="received",
            actor_uid=actor_uid,
            actor_name=actor_name,
            actor_roles=[],
            description=f"Doação registrada e validada: {label}",
        )

        event = DomainEvent(
            id="donation.received",
            payload=ValidationPayload(
                entity_id=ref.id,
                target_uid=target_uid,
                target_name=user_name,
                validated_by=actor_uid,
                validated_at=now,
                donation_amount=label,
            ),
        )

        return DonationOut(
            id=ref.id,
            item=data.item,
            item_label=item_label,
            item_description=data.item_description,
            month=month,
            status="received",
            created_at=created_at,
        ), event

    # ── Helpers ──────────────────────────────────────────────────────

    def _assert_guardian(self, guardian_uid, target_uid):
        db = firestore.client()
        doc = db.collection("users").document(target_uid).get()
        if not doc.exists:
            raise HTTPException(
                status_code=404,
                detail="Dependente não encontrado",
            )
        if doc.to_dict().get("guardianUid") != guardian_uid:
            raise HTTPException(
                status_code=403,
                detail="Você não é responsável deste dependente",
            )
