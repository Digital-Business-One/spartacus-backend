"""SupportService — donations and services (Apoio).

Generalizes the former DonationService. No monthly lock: members register as
many supports as they want; staff approve/reject each. Items come from the
per-project config (`supportConfig.donations` / `.services`).
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from firebase_admin import firestore

from app.events.models import (
    DomainEvent,
    SupportRegisteredPayload,
    ValidationPayload,
)
from app.logging.decorator import log
from app.models.support import (
    DEFAULT_DONATION_ITEMS,
    DEFAULT_SERVICE_ITEMS,
    DEFAULT_THANK_YOU,
    SupportConfig,
    SupportConfigItem,
    SupportConfigUpdate,
    SupportCreate,
    SupportDashboardOut,
    SupportHistoryItem,
    SupportHistoryOut,
    SupportOut,
    SupportRecordCard,
    SupportRegisterReceived,
)
from app.services.account_history_service import AccountHistoryService
from app.services.attendance_service import _calc_age, _initials


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


_TYPE_LABEL = {"donation": "Doação", "service": "Serviço"}


class SupportService:
    _SUPPORT = "support"
    _PROJECTS = "projects"

    # ── Config ───────────────────────────────────────────────────────────

    @log
    def get_config(self, project_id: str) -> SupportConfig:
        db = firestore.client()
        doc = db.collection(self._PROJECTS).document(project_id).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Projeto não encontrado")
        data = doc.to_dict()
        raw = data.get("supportConfig")
        if raw:
            return SupportConfig(
                donations=[SupportConfigItem(**i) for i in raw.get("donations", [])],
                services=[SupportConfigItem(**i) for i in raw.get("services", [])],
                thank_you_message=raw.get("thankYouMessage", DEFAULT_THANK_YOU),
            )
        # Migrate from legacy donationConfig (donations only) or defaults.
        legacy = data.get("donationConfig") or {}
        donations = legacy.get("items")
        return SupportConfig(
            donations=[
                SupportConfigItem(**i) for i in donations
            ] if donations else [
                SupportConfigItem(code=k, label=v)
                for k, v in DEFAULT_DONATION_ITEMS.items()
            ],
            services=[
                SupportConfigItem(code=k, label=v)
                for k, v in DEFAULT_SERVICE_ITEMS.items()
            ],
            thank_you_message=legacy.get("thankYouMessage", DEFAULT_THANK_YOU),
        )

    @log
    def update_config(
        self, project_id: str, data: SupportConfigUpdate,
    ) -> SupportConfig:
        db = firestore.client()
        ref = db.collection(self._PROJECTS).document(project_id)
        if not ref.get().exists:
            raise HTTPException(status_code=404, detail="Projeto não encontrado")
        updates: dict = {}
        if data.donations is not None:
            updates["supportConfig.donations"] = [
                i.model_dump() for i in data.donations
            ]
        if data.services is not None:
            updates["supportConfig.services"] = [
                i.model_dump() for i in data.services
            ]
        if data.thank_you_message is not None:
            updates["supportConfig.thankYouMessage"] = data.thank_you_message
        if updates:
            ref.update(updates)
        return self.get_config(project_id)

    def _label_for(
        self, project_id: str, support_type: str, item: str,
    ) -> str:
        cfg = self.get_config(project_id)
        items = cfg.donations if support_type == "donation" else cfg.services
        return next((i.label for i in items if i.code == item), item)

    @staticmethod
    def _amount(item_label: str, description: str | None) -> str:
        desc = (description or "").strip()
        return f"{item_label}: {desc}" if desc else item_label

    # ── Create (member self-register) ────────────────────────────────────

    @log
    def create(
        self,
        project_id: str,
        uid: str,
        acting_as: str | None,
        data: SupportCreate,
    ) -> tuple[SupportOut, DomainEvent]:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian(uid, target_uid)

        db = firestore.client()
        now = datetime.now(timezone.utc).isoformat()
        item_label = self._label_for(project_id, data.support_type, data.item)
        amount = self._amount(item_label, data.item_description)

        doc_data = {
            "projectId": project_id,
            "userId": target_uid,
            "actingAs": acting_as,
            "supportType": data.support_type,
            "item": data.item,
            "itemLabel": item_label,
            "itemDescription": data.item_description,
            "month": data.month or _current_month(),
            "status": "pledged",
            "createdAt": now,
            "receivedAt": None,
            "receivedBy": None,
        }
        _, ref = db.collection(self._SUPPORT).add(doc_data)

        user_doc = db.collection("users").document(target_uid).get()
        user_name = user_doc.to_dict().get("name", "") if user_doc.exists else ""

        AccountHistoryService().record(
            uid=target_uid, project_id=project_id, event_type="support",
            event_subtype=f"{data.support_type}_pledged", actor_uid=uid,
            actor_name=user_name, actor_roles=[],
            description=f"{_TYPE_LABEL[data.support_type]} registrada: {amount}",
        )

        event = DomainEvent(
            id="support.registered",
            payload=SupportRegisteredPayload(
                entity_id=ref.id,
                source_entity_ref=f"support/{ref.id}",
                source_entity_type="support",
                target_uid=target_uid,
                target_name=user_name,
                author_uid=uid,
                author_name=user_name,
                support_type=data.support_type,
                support_label=amount,
                support_date=doc_data["month"],
            ),
        )
        return SupportOut(
            id=ref.id,
            support_type=data.support_type,
            item=data.item,
            item_label=item_label,
            item_description=data.item_description,
            month=doc_data["month"],
            status="pledged",
            created_at=now,
        ), event

    # ── History ──────────────────────────────────────────────────────────

    @log
    def get_history(
        self, project_id: str, uid: str, acting_as: str | None = None,
        year: int | None = None,
    ) -> SupportHistoryOut:
        target_uid = acting_as or uid
        if acting_as:
            self._assert_guardian(uid, target_uid)
        return self._history_for(project_id, target_uid, year)

    @log
    def get_history_admin(
        self, project_id: str, target_uid: str, year: int | None = None,
    ) -> SupportHistoryOut:
        return self._history_for(project_id, target_uid, year)

    def _history_for(
        self, project_id: str, target_uid: str, year: int | None,
    ) -> SupportHistoryOut:
        db = firestore.client()
        docs = list(
            db.collection(self._SUPPORT)
            .where("projectId", "==", project_id)
            .where("userId", "==", target_uid)
            .stream()
        )
        records = [(d.id, d.to_dict()) for d in docs]
        if year is not None:
            records = [
                (i, x) for (i, x) in records
                if str(x.get("month", "")).startswith(f"{year}-")
                or str(x.get("createdAt", "")).startswith(f"{year}-")
            ]

        # Resolve validator names
        receiver_uids = {x.get("receivedBy") for _, x in records if x.get("receivedBy")}
        names: dict[str, str] = {}
        if receiver_uids:
            refs = [db.collection("users").document(u) for u in receiver_uids]
            for udoc in db.get_all(refs):
                if udoc.exists:
                    names[udoc.id] = udoc.to_dict().get("name", "")

        records.sort(key=lambda r: r[1].get("createdAt", ""), reverse=True)
        items: list[SupportHistoryItem] = []
        for doc_id, x in records:
            status = x.get("status", "pledged")
            stype = x.get("supportType", "donation")
            rb = x.get("receivedBy")
            month = x.get("month")
            items.append(SupportHistoryItem(
                id=doc_id,
                support_type=stype,
                month=month,
                month_label=self._format_month_label(month) if month else None,
                item=x.get("item"),
                item_label=x.get("itemLabel") or x.get("item", ""),
                item_description=x.get("itemDescription"),
                status=status,
                status_label=(
                    "Validado" if status == "received"
                    else "Recusado" if status == "absent"
                    else "Aguardando"
                ),
                created_at=self._format_created_date(x.get("createdAt", "")),
                received_by=rb,
                received_by_name=names.get(rb) if rb else None,
                received_at=x.get("receivedAt"),
            ))
        return SupportHistoryOut(items=items)

    @staticmethod
    def _format_month_label(month_str: str) -> str:
        names = {
            1: "JANEIRO", 2: "FEVEREIRO", 3: "MARÇO", 4: "ABRIL", 5: "MAIO",
            6: "JUNHO", 7: "JULHO", 8: "AGOSTO", 9: "SETEMBRO", 10: "OUTUBRO",
            11: "NOVEMBRO", 12: "DEZEMBRO",
        }
        try:
            y, m = month_str.split("-")[:2]
            return f"{names.get(int(m), '')} / {y}"
        except (ValueError, IndexError):
            return month_str

    @staticmethod
    def _format_created_date(iso_str: str) -> str:
        names = {
            1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio",
            6: "Junho", 7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro",
            11: "Novembro", 12: "Dezembro",
        }
        try:
            dt = datetime.fromisoformat(iso_str)
            return f"{dt.day} de {names.get(dt.month, '')}, {dt.year}"
        except (ValueError, TypeError):
            return iso_str

    # ── Dashboard (per-record kanban) ────────────────────────────────────

    @log
    def dashboard(
        self, project_id: str, month: str | None = None,
        support_type: str | None = None,
    ) -> SupportDashboardOut:
        db = firestore.client()
        month = month or _current_month()

        query = (
            db.collection(self._SUPPORT)
            .where("projectId", "==", project_id)
            .where("month", "==", month)
        )
        recs = [(d.id, d.to_dict()) for d in query.stream()]
        if support_type:
            recs = [
                (i, x) for (i, x) in recs
                if x.get("supportType", "donation") == support_type
            ]

        user_ids = {x.get("userId", "") for _, x in recs if x.get("userId")}
        users: dict[str, dict] = {}
        if user_ids:
            refs = [db.collection("users").document(u) for u in user_ids]
            for udoc in db.get_all(refs):
                if udoc.exists:
                    users[udoc.id] = udoc.to_dict()

        cards: list[SupportRecordCard] = []
        for doc_id, x in recs:
            ud = users.get(x.get("userId", ""), {})
            cards.append(SupportRecordCard(
                id=doc_id,
                user_id=x.get("userId", ""),
                name=ud.get("name", ""),
                nickname=ud.get("nickname"),
                initials=_initials(ud.get("name", "")),
                age=_calc_age(ud.get("birthDate")),
                photo_url=ud.get("photoUrl"),
                is_dependent=bool(ud.get("guardianUid")),
                guardian_uid=ud.get("guardianUid"),
                guardian_name=None,
                support_type=x.get("supportType", "donation"),
                item=x.get("item"),
                item_label=x.get("itemLabel") or x.get("item"),
                item_description=x.get("itemDescription"),
                status=x.get("status", "pledged"),
                created_at=x.get("createdAt"),
                validated_at=x.get("validatedAt"),
            ))

        guardian_uids = {c.guardian_uid for c in cards if c.guardian_uid}
        if guardian_uids:
            refs = [db.collection("users").document(g) for g in guardian_uids]
            gnames = {
                d.id: d.to_dict().get("name")
                for d in db.get_all(refs) if d.exists
            }
            for c in cards:
                if c.guardian_uid:
                    c.guardian_name = gnames.get(c.guardian_uid)

        cards.sort(key=lambda c: (c.name.lower(), c.created_at or ""))
        return SupportDashboardOut(
            month=month,
            month_label=self._format_month_label(month),
            items=cards,
        )

    @log
    def register_received(
        self, project_id: str, actor_uid: str, data: SupportRegisterReceived,
    ) -> tuple[SupportOut, DomainEvent]:
        """Staff registers a support already received (status=received)."""
        db = firestore.client()
        month = _current_month()
        now = datetime.now(timezone.utc).isoformat()
        target_uid = data.user_id

        user_doc = db.collection("users").document(target_uid).get()
        if not user_doc.exists:
            raise HTTPException(status_code=404, detail="Aluno não encontrado")
        user_name = user_doc.to_dict().get("name", "")
        actor_doc = db.collection("users").document(actor_uid).get()
        actor_name = actor_doc.to_dict().get("name", "") if actor_doc.exists else ""

        item_label = self._label_for(project_id, data.support_type, data.item)
        label = self._amount(item_label, data.item_description)

        _, ref = db.collection(self._SUPPORT).add({
            "projectId": project_id,
            "userId": target_uid,
            "actingAs": None,
            "supportType": data.support_type,
            "item": data.item,
            "itemLabel": item_label,
            "itemDescription": data.item_description,
            "month": month,
            "status": "received",
            "createdAt": now,
            "receivedAt": now,
            "receivedBy": actor_uid,
            "validatedBy": actor_uid,
            "validatedAt": now,
            "previousStatus": "absent",
        })

        AccountHistoryService().record(
            uid=target_uid, project_id=project_id, event_type="support",
            event_subtype=f"{data.support_type}_received", actor_uid=actor_uid,
            actor_name=actor_name, actor_roles=[],
            description=(
                f"{_TYPE_LABEL[data.support_type]} registrada e validada: {label}"
            ),
        )

        event = DomainEvent(
            id="support.received",
            payload=ValidationPayload(
                entity_id=ref.id,
                target_uid=target_uid,
                target_name=user_name,
                validated_by=actor_uid,
                validated_at=now,
                donation_amount=label,
            ),
        )
        return SupportOut(
            id=ref.id,
            support_type=data.support_type,
            item=data.item,
            item_label=item_label,
            item_description=data.item_description,
            month=month,
            status="received",
            created_at=now,
        ), event

    # ── Helpers ──────────────────────────────────────────────────────────

    def _assert_guardian(self, guardian_uid, target_uid):
        db = firestore.client()
        doc = db.collection("users").document(target_uid).get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail="Dependente não encontrado")
        if doc.to_dict().get("guardianUid") != guardian_uid:
            raise HTTPException(
                status_code=403, detail="Você não é responsável deste dependente",
            )
