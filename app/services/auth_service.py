from datetime import date, datetime, timezone

from fastapi import HTTPException
from firebase_admin import auth, firestore

from app.logging.decorator import log
from app.models.auth import ResendVerificationRequest, SignupRequest
from app.notifications.models import (
    AccountReceivedPayload,
    DomainEvent,
    ResendVerificationPayload,
    SignupEmailPayload,
    SignupGooglePayload,
)

_ROLES_PT = {
    "student": "Aluno",
    "teacher": "Professor",
    "instructor": "Instrutor",
    "guardian": "Responsável",
    "supporter": "Apoiador",
    "sponsor": "Patrocinador",
}


def _roles_label(roles: list[str]) -> str:
    return ", ".join(_ROLES_PT.get(r, r) for r in roles)


def _calculate_age(birth_date_str: str) -> str:
    birth = datetime.strptime(birth_date_str, "%d/%m/%Y").date()
    today = date.today()
    age = (
        today.year
        - birth.year
        - ((today.month, today.day) < (birth.month, birth.day))
    )
    return f"{age} ano{'s' if age != 1 else ''}"


class AuthService:
    _USERS = "users"
    _MEMBERSHIPS = "memberships"
    _CLASSES = "classes"

    @log
    def check_email(self, email: str) -> bool:
        """Return True if email is available (not in Firestore)."""
        db = firestore.client()
        results = list(
            db.collection(self._USERS)
            .where("email", "==", email.lower())
            .limit(1)
            .stream()
        )
        return len(results) == 0

    @log
    def signup(
        self,
        project_id: str,
        data: SignupRequest,
        google_uid: str | None = None,
    ) -> DomainEvent:
        db = firestore.client()
        self._assert_no_duplicate(db, data)

        if data.auth_method == "email":
            try:
                record = auth.create_user(
                    email=data.email,
                    password=data.password,
                    display_name=data.name,
                )
                uid = record.uid
            except auth.EmailAlreadyExistsError:
                existing = auth.get_user_by_email(data.email)
                user_doc = db.collection(self._USERS).document(existing.uid).get()
                if user_doc.exists:
                    raise HTTPException(
                        status_code=409, detail="Email já cadastrado"
                    )
                auth.update_user(
                    existing.uid,
                    password=data.password,
                    display_name=data.name,
                )
                uid = existing.uid
            approval_status = "pending_email"
        else:
            uid = google_uid
            approval_status = "pending_approval"

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
                "approvalStatus": approval_status,
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

        if data.auth_method == "email":
            link = auth.generate_email_verification_link(data.email)
            class_map = self._fetch_class_names(db, data)

            show_classes = bool(data.class_ids) and any(
                r in data.roles for r in ("student", "teacher", "instructor")
            )
            show_dependents = "guardian" in data.roles and bool(data.dependents)

            classes = (
                [{"name": class_map.get(cid, cid)} for cid in data.class_ids]
                if show_classes
                else []
            )

            dependents_data = []
            if show_dependents:
                for dep in data.dependents:
                    dep_classes = ", ".join(
                        class_map.get(cid, cid) for cid in dep.class_ids
                    )
                    dependents_data.append(
                        {
                            "name": dep.name,
                            "age": _calculate_age(dep.birth_date),
                            "classes": dep_classes,
                        }
                    )

            return DomainEvent(
                id="signup.email_confirmation",
                payload=SignupEmailPayload(
                    uid=uid,
                    status=approval_status,
                    to=data.email,
                    name=data.name,
                    email=data.email,
                    phone=data.phone,
                    roles_label=_roles_label(data.roles),
                    link=link,
                    show_classes=show_classes,
                    classes=classes,
                    show_dependents=show_dependents,
                    dependents=dependents_data,
                ),
            )
        else:
            return DomainEvent(
                id="signup.google_completed",
                payload=SignupGooglePayload(
                    uid=uid, status=approval_status
                ),
            )

    @log
    def confirm_email_verified(
        self, uid: str, email: str
    ) -> DomainEvent | None:
        record = auth.get_user(uid)
        if not record.email_verified:
            raise HTTPException(
                status_code=400,
                detail="Email ainda não verificado no Firebase Auth",
            )
        db = firestore.client()
        user_ref = db.collection(self._USERS).document(uid)
        user_doc = user_ref.get()
        if not user_doc.exists:
            raise HTTPException(
                status_code=404, detail="Usuário não encontrado"
            )
        user_data = user_doc.to_dict()
        current_status = user_data.get("approvalStatus")
        if current_status != "pending_email":
            return None
        user_ref.update({"approvalStatus": "pending_approval"})
        return DomainEvent(
            id="signup.account_received",
            payload=AccountReceivedPayload(
                to=email,
                name=user_data.get("name", ""),
            ),
        )

    @log
    def resend_verification(
        self, data: ResendVerificationRequest
    ) -> DomainEvent | None:
        db = firestore.client()
        users = db.collection(self._USERS)
        results = list(
            users.where("email", "==", data.email).limit(1).stream()
        )
        if not results:
            return None
        user_data = results[0].to_dict()
        if user_data.get("approvalStatus") != "pending_email":
            return None
        link = auth.generate_email_verification_link(data.email)
        return DomainEvent(
            id="signup.resend_verification",
            payload=ResendVerificationPayload(
                to=data.email,
                name=user_data.get("name", ""),
                link=link,
            ),
        )

    def _fetch_class_names(
        self, db, data: SignupRequest
    ) -> dict[str, str]:
        all_ids = set(data.class_ids)
        for dep in data.dependents:
            all_ids.update(dep.class_ids)
        if not all_ids:
            return {}
        refs = [
            db.collection(self._CLASSES).document(cid) for cid in all_ids
        ]
        docs = db.get_all(refs)
        return {
            doc.id: doc.to_dict().get("name", doc.id)
            for doc in docs
            if doc.exists
        }

    def _assert_no_duplicate(
        self, db, data: SignupRequest
    ) -> None:
        users = db.collection(self._USERS)
        if list(
            users.where("email", "==", data.email)
            .limit(1)
            .stream()
        ):
            raise HTTPException(
                status_code=409, detail="Email já cadastrado"
            )
        if data.tax_id and list(
            users.where("taxId", "==", data.tax_id)
            .limit(1)
            .stream()
        ):
            raise HTTPException(
                status_code=409, detail="CPF já cadastrado"
            )
        matches = (
            users.where("name", "==", data.name)
            .where("birthDate", "==", data.birth_date)
            .where("phone", "==", data.phone)
            .limit(1)
            .stream()
        )
        if list(matches):
            raise HTTPException(
                status_code=409,
                detail="Já existe uma conta cadastrada para este usuário",
            )

    def _create_membership(
        self, db, project_id: str, user_id: str, roles: list[str], now: str
    ) -> None:
        db.collection(self._MEMBERSHIPS).document(
            f"{project_id}_{user_id}"
        ).set(
            {
                "projectId": project_id,
                "userId": user_id,
                "roles": roles,
                "status": "pending_approval",
                "joined_at": now,
            },
            merge=True,
        )
