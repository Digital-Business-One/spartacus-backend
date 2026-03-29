"""Notification rules: event_id → (template, subject).

Template IDs reference SendGrid Dynamic Templates.
Run scripts/setup_sendgrid_templates.py to create them and update IDs here.
"""

# SendGrid Dynamic Template IDs
# Replace with actual IDs after running setup_sendgrid_templates.py
_TPL_SIGNUP = "TBD_SIGNUP_WELCOME"
_TPL_NOTIFICATION = "TBD_ACCOUNT_NOTIFICATION"

RULES: dict[str, dict] = {
    # ── Signup ────────────────────────────────────────────────────────────────
    "signup.email_confirmation": {
        "template_id": _TPL_SIGNUP,
        "subject": "Confirme seu e-mail — Spartacus",
        "active": True,
    },
    "signup.account_created": {
        "template_id": _TPL_SIGNUP,
        "subject": "Bem-vindo ao Spartacus!",
        "active": True,
    },
    "signup.resend_verification": {
        "template_id": _TPL_SIGNUP,
        "subject": "Novo link de verificação — Spartacus",
        "active": True,
    },
    # ── Account lifecycle ─────────────────────────────────────────────────────
    "account.approve": {
        "template_id": _TPL_NOTIFICATION,
        "subject": "Cadastro aprovado — Spartacus",
        "active": True,
    },
    "account.reject": {
        "template_id": _TPL_NOTIFICATION,
        "subject": "Atualização sobre seu cadastro — Spartacus",
        "active": True,
    },
    "account.approve_to_medical": {
        "template_id": _TPL_NOTIFICATION,
        "subject": "Próximo passo: anamnese — Spartacus",
        "active": True,
    },
    "account.approve_medical": {
        "template_id": _TPL_NOTIFICATION,
        "subject": "Anamnese aprovada — Spartacus",
        "active": True,
    },
    "account.request_revision": {
        "template_id": _TPL_NOTIFICATION,
        "subject": "Revisão cadastral solicitada — Spartacus",
        "active": True,
    },
}
