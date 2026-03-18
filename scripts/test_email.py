#!/usr/bin/env python3
"""
Script utilitário para testar envio de e-mail via extensão MailerSend do Firebase.

Cria um documento na collection `emails` do Firestore com a estrutura esperada
pela extensão. NÃO faz parte da suite de testes automatizados — executar manualmente.

Uso:
    # Com emulador local (padrão)
    uv run python scripts/test_email.py

    # Escolher template
    uv run python scripts/test_email.py --event signup.email_confirmation
    uv run python scripts/test_email.py --event signup.account_received
    uv run python scripts/test_email.py --event signup.resend_verification

    # Contra projeto real (cuidado: envia e-mail de verdade!)
    uv run python scripts/test_email.py --to seu@email.com --real

    # Customizar destinatário no emulador
    uv run python scripts/test_email.py --to outro@email.com
"""

import argparse
import os
import sys

import firebase_admin
from firebase_admin import credentials, firestore


# ── Dados dos templates ──────────────────────────────────────────────────────

TEMPLATES = {
    "signup.email_confirmation": {
        "template_id": "v69oxl59w12g785k",
        "subject": "Confirme seu e-mail — Spartacus",
        "personalization": {
            "name": "João Teste da Silva",
            "email": "joao.teste@example.com",
            "phone": "(65) 99999-0000",
            "roles_label": "Aluno, Responsável",
            "link": "https://example.com/verify?fake_token=abc123",
            "show_classes": True,
            "classes": [
                {"name": "Jiu Jitsu — Ter/Qui 18h"},
                {"name": "Capoeira — Seg/Qua 17h"},
            ],
            "show_dependents": True,
            "dependents": [
                {
                    "name": "Maria Teste",
                    "age": "8 anos",
                    "classes": "Capoeira — Seg/Qua 17h",
                },
            ],
        },
    },
    "signup.account_received": {
        "template_id": "z86org8zknegew13",
        "subject": "Cadastro recebido — Spartacus",
        "personalization": {
            "name": "João Teste da Silva",
        },
    },
    "signup.resend_verification": {
        "template_id": "pxkjn41w1j5lz781",
        "subject": "Novo link de verificação — Spartacus",
        "personalization": {
            "name": "João Teste da Silva",
            "link": "https://example.com/verify?fake_token=xyz789",
        },
    },
}

_FROM_EMAIL = "noreply@spartacus.app.br"
_FROM_NAME = "Spartacus Artes Marciais"
_COLLECTION = "emails"


def init_firebase(use_emulator: bool) -> None:
    if use_emulator:
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")
        print(
            f"  Emulador: {os.environ['FIRESTORE_EMULATOR_HOST']}"
        )

    if not firebase_admin._apps:
        firebase_admin.initialize_app(
            credentials.ApplicationDefault()
            if not use_emulator
            else None
        )


def create_email_doc(to: str, event_id: str) -> str:
    template = TEMPLATES[event_id]

    doc_data = {
        "to": [{"email": to}],
        "from": {"email": _FROM_EMAIL, "name": _FROM_NAME},
        "subject": template["subject"],
        "template_id": template["template_id"],
        "personalization": [
            {"email": to, "data": template["personalization"]}
        ],
        "tags": [event_id],
    }

    db = firestore.client()
    _, doc_ref = db.collection(_COLLECTION).add(doc_data)
    return doc_ref.id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Testa envio de e-mail criando documento na collection 'emails'."
    )
    parser.add_argument(
        "--to",
        default="teste@example.com",
        help="E-mail destinatário (default: teste@example.com)",
    )
    parser.add_argument(
        "--event",
        choices=list(TEMPLATES.keys()),
        default="signup.email_confirmation",
        help="Evento/template a testar (default: signup.email_confirmation)",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Usar projeto real (sem emulador). CUIDADO: envia e-mail de verdade!",
    )
    args = parser.parse_args()

    use_emulator = not args.real

    print(f"\n{'='*60}")
    print(f"  Teste de e-mail — {args.event}")
    print(f"{'='*60}")
    print(f"  Destinatário: {args.to}")
    print(f"  Template ID:  {TEMPLATES[args.event]['template_id']}")
    print(f"  Modo:         {'PRODUÇÃO' if args.real else 'Emulador'}")

    if args.real:
        confirm = input(
            "\n  ⚠  Modo REAL — vai criar doc no Firestore de produção."
            "\n  Continuar? [y/N] "
        )
        if confirm.lower() != "y":
            print("  Cancelado.")
            sys.exit(0)

    init_firebase(use_emulator)

    print(f"\n  Criando documento na collection '{_COLLECTION}'...")
    doc_id = create_email_doc(args.to, args.event)
    print(f"  Doc criado: {_COLLECTION}/{doc_id}")

    if use_emulator:
        print(
            f"\n  Verifique no Emulator UI: http://localhost:4000/firestore/data/{_COLLECTION}/{doc_id}"
        )
    else:
        print(
            "\n  A extensão MailerSend deve processar o documento automaticamente."
            "\n  Verifique o e-mail na caixa de entrada."
        )

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
