import os
from datetime import date, datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException
from firebase_admin import auth, firestore

from app.events.models import (
    AccountNotificationPayload,
    DomainEvent,
    ResendVerificationPayload,
    SignupEmailPayload,
)
from app.logging.decorator import log
from app.models.auth import ResendVerificationRequest, SignupRequest

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
            email_verified = False
        else:
            uid = google_uid
            email_verified = True  # Google always verifies email

        now = datetime.now(timezone.utc).isoformat()
        auth_provider = (
            "google.com" if data.auth_method == "google" else "password"
        )

        db.collection(self._USERS).document(uid).set(
            {
                "name": data.name,
                "email": data.email,
                "birthDate": data.birth_date,
                "gender": data.gender,
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
                "emailVerified": email_verified,
                "isDependent": False,
                "classIds": data.class_ids,
                "authProvider": auth_provider,
                "createdAt": now,
                "updatedAt": now,
                "lastUpdatedBy": uid,
            },
            merge=True,
        )
        self._create_membership(db, project_id, uid, data.roles, now)

        # Record creation in history sub-collection
        from app.services.account_history_service import AccountHistoryService
        history_service = AccountHistoryService()
        history_service.record(
            uid=uid,
            project_id=project_id,
            event_type="creation",
            event_subtype=auth_provider,
            actor_uid=uid,
            actor_name=data.name,
            actor_roles=data.roles,
            description=(
                "Conta criada via Google"
                if auth_provider == "google.com"
                else "Conta criada via e-mail e senha"
            ),
        )

        for dep in data.dependents:
            dep_uid = f"{uid}_dep_{dep.id}"
            db.collection(self._USERS).document(dep_uid).set(
                {
                    "name": dep.name,
                    "birthDate": dep.birth_date,
                    "gender": dep.gender,
                    "taxId": dep.tax_id,
                    "guardianUid": uid,
                    "guardianRelationship": dep.guardian_relationship,
                    "approvalStatus": "pending_approval",
                    "isDependent": True,
                    "classIds": dep.class_ids,
                    "authProvider": "guardian_created",
                    "createdAt": now,
                    "updatedAt": now,
                    "lastUpdatedBy": uid,
                },
                merge=True,
            )
            self._create_membership(db, project_id, dep_uid, ["student"], now)
            history_service.record(
                uid=dep_uid,
                project_id=project_id,
                event_type="creation",
                event_subtype="guardian_created",
                actor_uid=uid,
                actor_name=data.name,
                actor_roles=data.roles,
                description=(
                    f"Dependente criado por {data.name} (responsável)"
                ),
            )

        # Build signup details (shared by email and Google paths)
        details = self._build_signup_details(db, data)

        link = ""
        event_id = "signup.account_created"
        if data.auth_method == "email":
            link = auth.generate_email_verification_link(data.email)
            event_id = "signup.email_confirmation"

        return DomainEvent(
            id=event_id,
            payload=SignupEmailPayload(
                uid=uid,
                status="pending_approval",
                to=data.email,
                name=data.name,
                email=data.email,
                phone=data.phone,
                roles_label=_roles_label(data.roles),
                link=link,
                **details,
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
        # Only update the emailVerified field — status is NOT affected
        if user_data.get("emailVerified") is True:
            return None  # Already marked
        user_ref.update({"emailVerified": True})
        return DomainEvent(
            id="signup.email_verified",
            payload=AccountNotificationPayload(
                to=email,
                name=user_data.get("name", ""),
                title="E-mail confirmado",
                message=(
                    "Seu e-mail foi verificado com sucesso. "
                    "Seu cadastro está em análise pela equipe."
                ),
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
        if user_data.get("emailVerified") is True:
            return None  # Already verified
        link = auth.generate_email_verification_link(data.email)
        return DomainEvent(
            id="signup.resend_verification",
            payload=ResendVerificationPayload(
                to=data.email,
                name=user_data.get("name", ""),
                link=link,
            ),
        )

    @log
    def request_password_reset(self, email: str) -> DomainEvent | None:
        """Generate a password reset link and emit an event.

        Returns None if the email is not registered — we intentionally do
        not reveal account existence, so the endpoint responds 200 either
        way and no notification is sent.
        """
        try:
            record = auth.get_user_by_email(email)
        except auth.UserNotFoundError:
            return None

        firebase_link = auth.generate_password_reset_link(email)
        # Firebase always generates a link pointing to its own action URL
        # (emulator or prod). Rewrite it to point to our own branded page so
        # the user lands on a Spartacus-styled reset screen. The oobCode is
        # the only thing our page needs — it calls confirmPasswordReset on
        # the Firebase SDK, which works against both emulator and prod.
        oob_code = parse_qs(urlparse(firebase_link).query).get("oobCode", [""])[0]
        web_base = os.getenv("APP_WEB_URL", "").rstrip("/")
        link = (
            f"{web_base}/reset-password?oobCode={oob_code}"
            if web_base and oob_code
            else firebase_link
        )

        name = record.display_name or email.split("@")[0]
        return DomainEvent(
            id="account.password_reset",
            payload=AccountNotificationPayload(
                to=email,
                name=name,
                title="Redefinir sua senha",
                message=(
                    "Recebemos uma solicitação para redefinir a senha da sua "
                    "conta Spartacus. Clique no botão abaixo para criar uma "
                    "nova senha. Se você não solicitou, pode ignorar este "
                    "e-mail — sua senha atual continuará funcionando."
                ),
                cta_text="Redefinir senha",
                cta_url=link,
            ),
        )

    @log
    def get_user_status(self, uid: str) -> dict:
        db = firestore.client()
        doc = db.collection(self._USERS).document(uid).get()
        if not doc.exists:
            return {"approvalStatus": "not_found"}
        data = doc.to_dict()
        return {
            "approvalStatus": data.get("approvalStatus", "unknown"),
            "birthDate": data.get("birthDate"),
        }

    def _build_signup_details(
        self, db, data: SignupRequest
    ) -> dict:
        """Build shared signup details for email templates."""
        class_map = self._fetch_class_names(db, data)
        show_classes = bool(data.class_ids) and any(
            r in data.roles
            for r in ("student", "teacher", "instructor")
        )
        show_dependents = (
            "guardian" in data.roles and bool(data.dependents)
        )
        classes = (
            [{"name": class_map.get(cid, cid)} for cid in data.class_ids]
            if show_classes
            else []
        )
        dependents_data: list[dict] = []
        if show_dependents:
            for dep in data.dependents:
                dep_class_names = [
                    class_map.get(cid, cid) for cid in dep.class_ids
                ]
                dependents_data.append(
                    {
                        "id": dep.id,
                        "name": dep.name,
                        "age": _calculate_age(dep.birth_date),
                        "classes": ", ".join(dep_class_names),
                        "class_names": dep_class_names,
                    }
                )
        return {
            "show_classes": show_classes,
            "classes": classes,
            "show_dependents": show_dependents,
            "dependents": dependents_data,
        }

    def _fetch_class_names(
        self, db, data: SignupRequest
    ) -> dict[str, str]:
        """Resolve class IDs to names for email templates.

        Class IDs are composite: ``{projectId}_{slug}`` (e.g.
        ``spartacus-artes-marciais_muay-thai-kids``).  The frontend
        receives these from ``GET /projects/{id}/classes`` and sends
        them back as-is during signup.
        """
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
