from datetime import datetime, timezone

from fastapi import HTTPException
from firebase_admin import firestore

from app.logging.decorator import log
from app.models.donation import (
    ITEM_LABELS,
    DonationConfig,
    DonationConfigItem,
    DonationConfigUpdate,
    DonationCreate,
    DonationOut,
)


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class DonationService:
    _DOACOES = "doacoes"
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
        month = _current_month()

        # Check duplicate
        existing = list(
            db.collection(self._DOACOES)
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

        _, ref = db.collection(self._DOACOES).add(doc_data)

        return DonationOut(
            id=ref.id,
            item=data.item,
            item_label=ITEM_LABELS.get(data.item, data.item),
            item_description=data.item_description,
            month=month,
            status="pledged",
            created_at=now,
        )

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
            db.collection(self._DOACOES)
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
