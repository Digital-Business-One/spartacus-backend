from datetime import datetime, timezone

from fastapi import HTTPException
from firebase_admin import auth, firestore

from app.logging.decorator import log
from app.models.auth import SignupRequest, SignupResponse


class AuthService:
    _USERS = "users"
    _MEMBERSHIPS = "memberships"

    @log
    def signup(
        self,
        project_id: str,
        data: SignupRequest,
        google_uid: str | None = None,
    ) -> SignupResponse:
        db = firestore.client()
        self._assert_no_duplicate(db, data.email, data.tax_id)

        if data.auth_method == "email":
            try:
                record = auth.create_user(
                    email=data.email,
                    password=data.password,
                    display_name=data.name,
                )
                uid = record.uid
            except auth.EmailAlreadyExistsError:
                raise HTTPException(status_code=409, detail="Email já cadastrado")
        else:
            uid = google_uid

        now = datetime.now(timezone.utc).isoformat()

        db.collection(self._USERS).document(uid).set(
            {
                "name": data.name,
                "email": data.email,
                "birthDate": data.birth_date,
                "taxId": data.tax_id,
                "phone": data.phone,
                "whatsapp": data.whatsapp,
                "address": {
                    "postalCode": data.postal_code,
                    "street": data.street,
                    "number": data.number,
                    "complement": data.complement,
                    "neighborhood": data.neighborhood,
                    "city": data.city,
                    "state": data.state,
                },
                "approvalStatus": "pending_approval",
                "isDependent": False,
                "classIds": data.class_ids,
                "createdAt": now,
            },
            merge=True,
        )
        self._create_membership(db, project_id, uid, data.roles, now)

        for dep in data.dependents:
            dep_uid = f"{uid}_dep_{dep.id}"
            db.collection(self._USERS).document(dep_uid).set(
                {
                    "name": dep.name,
                    "birthDate": dep.birth_date,
                    "taxId": dep.tax_id,
                    "guardianUid": uid,
                    "approvalStatus": "pending_approval",
                    "isDependent": True,
                    "classIds": dep.class_ids,
                    "createdAt": now,
                },
                merge=True,
            )
            self._create_membership(db, project_id, dep_uid, ["student"], now)

        return SignupResponse(uid=uid, status="pending_approval")

    def _assert_no_duplicate(self, db, email: str, tax_id: str) -> None:
        users = db.collection(self._USERS)
        if list(users.where("email", "==", email).limit(1).stream()):
            raise HTTPException(status_code=409, detail="Email já cadastrado")
        if list(users.where("taxId", "==", tax_id).limit(1).stream()):
            raise HTTPException(status_code=409, detail="CPF já cadastrado")

    def _create_membership(
        self, db, project_id: str, user_id: str, roles: list[str], now: str
    ) -> None:
        db.collection(self._MEMBERSHIPS).document(f"{project_id}_{user_id}").set(
            {
                "projectId": project_id,
                "userId": user_id,
                "roles": roles,
                "status": "pending_approval",
                "joined_at": now,
            },
            merge=True,
        )
