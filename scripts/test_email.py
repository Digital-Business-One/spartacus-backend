#!/usr/bin/env python3
"""
Script utilitário para testar envio de e-mail via pipeline SendGrid.

Cria um documento na collection `notifications` do Firestore com a estrutura
esperada pelo NotificationAdapter. Em modo --real, também publica no PubSub
para acionar a Cloud Function que envia via SendGrid.

Uso:
    # Com emulador local (padrão — só escreve no Firestore)
    uv run python scripts/test_email.py

    # Escolher evento
    uv run python scripts/test_email.py --event signup.email_confirmation
    uv run python scripts/test_email.py --event account.approve
    uv run python scripts/test_email.py --event account.reject
    uv run python scripts/test_email.py --event account.approve_to_medical
    uv run python scripts/test_email.py --event account.request_revision

    # Contra projeto real (cuidado: publica no PubSub → envia e-mail!)
    uv run python scripts/test_email.py --to seu@email.com --real

    # Listar eventos disponíveis
    uv run python scripts/test_email.py --list
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import firebase_admin
from firebase_admin import credentials, firestore

from app.notifications.rules import RULES

# ── Dados de teste por evento ────────────────────────────────────────────────

_APP_URL = "https://spartacus.app.br"

PAYLOADS: dict[str, dict] = {
    "signup.email_confirmation": {
        "name": "João Teste da Silva",
        "email": "joao.teste@example.com",
        "phone": "(65) 99999-0000",
        "roles_label": "Aluno, Responsável",
        "link": "https://example.com/verify?fake_token=abc123",
        "show_link": True,
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
    "signup.account_created": {
        "name": "João Teste da Silva",
        "email": "joao.teste@example.com",
        "phone": "(65) 99999-0000",
        "roles_label": "Aluno",
        "link": "",
        "show_link": False,
        "show_classes": True,
        "classes": [{"name": "Muay Thai Kids — Seg/Qua 16h"}],
        "show_dependents": False,
        "dependents": [],
    },
    "signup.resend_verification": {
        "name": "João Teste da Silva",
        "link": "https://example.com/verify?fake_token=xyz789",
        "show_link": True,
    },
    "account.approve": {
        "name": "João Teste da Silva",
        "title": "Cadastro aprovado!",
        "message": (
            "Parabéns! Seu cadastro no Projeto Spartacus foi aprovado. "
            "Agora você pode acessar o nosso aplicativo e a plataforma "
            "digital Spartacus. Estamos muito felizes em ter você com a gente!"
        ),
        "cta_text": "Acessar plataforma",
        "cta_url": _APP_URL,
    },
    "account.reject": {
        "name": "João Teste da Silva",
        "title": "Atualização sobre seu cadastro",
        "message": (
            "Gostaríamos de informar que, neste momento, não foi possível "
            "aprovar seu cadastro no Projeto Spartacus. Sabemos que isso "
            "pode ser frustrante, mas queremos que saiba que as portas do "
            "Spartacus continuam abertas."
        ),
        "cta_text": "",
        "cta_url": "",
    },
    "account.approve_to_medical": {
        "name": "João Teste da Silva",
        "title": "Próximo passo: anamnese",
        "message": (
            "Seu cadastro avançou para a próxima etapa! Acesse o aplicativo "
            "Spartacus e preencha o formulário de anamnese."
        ),
        "cta_text": "Preencher anamnese",
        "cta_url": _APP_URL,
    },
    "account.approve_medical": {
        "name": "João Teste da Silva",
        "title": "Anamnese aprovada!",
        "message": (
            "Sua anamnese foi analisada e aprovada pela equipe do Spartacus. "
            "Seu cadastro está completo!"
        ),
        "cta_text": "Acessar plataforma",
        "cta_url": _APP_URL,
    },
    "account.request_revision": {
        "name": "João Teste da Silva",
        "title": "Revisão cadastral solicitada",
        "message": (
            "A equipe do Spartacus identificou que alguns dados do seu "
            "cadastro precisam ser revisados. Acesse o aplicativo para "
            "verificar e atualizar as informações solicitadas."
        ),
        "cta_text": "Revisar cadastro",
        "cta_url": _APP_URL,
    },
}

_FROM_EMAIL = "noreply@spartacus.app.br"
_FROM_NAME = "Spartacus Artes Marciais"
_COLLECTION = "notifications"


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


def write_notification(to: str, event_id: str) -> str:
    rule = RULES.get(event_id)
    if not rule:
        print(f"  ERRO: evento '{event_id}' não encontrado em rules.py")
        sys.exit(1)

    data = PAYLOADS[event_id]

    doc_data = {
        "event_id": event_id,
        "template_id": rule["template_id"],
        "subject": rule["subject"],
        "to": to,
        "from_email": _FROM_EMAIL,
        "from_name": _FROM_NAME,
        "data": data,
    }

    db = firestore.client()
    _, doc_ref = db.collection(_COLLECTION).add(doc_data)
    return doc_ref.id


def publish_pubsub(to: str, event_id: str) -> None:
    from google.cloud import pubsub_v1

    rule = RULES[event_id]
    data = PAYLOADS[event_id]

    payload = {
        "event_id": event_id,
        "template_id": rule["template_id"],
        "subject": rule["subject"],
        "to": to,
        "from_email": _FROM_EMAIL,
        "from_name": _FROM_NAME,
        "data": data,
    }

    project = os.environ.get(
        "GOOGLE_CLOUD_PROJECT", "spartacus-artes-marciais"
    )
    topic = f"projects/{project}/topics/email-notifications"

    publisher = pubsub_v1.PublisherClient()
    future = publisher.publish(topic, json.dumps(payload).encode("utf-8"))
    print(f"  PubSub message ID: {future.result()}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Testa pipeline de e-mail (Firestore + PubSub → SendGrid)."
    )
    parser.add_argument(
        "--to",
        default="teste@example.com",
        help="E-mail destinatário (default: teste@example.com)",
    )
    parser.add_argument(
        "--event",
        choices=list(PAYLOADS.keys()),
        default="signup.email_confirmation",
        help="Evento a testar",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="Modo real: escreve no Firestore + publica no PubSub. ENVIA E-MAIL!",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Listar eventos disponíveis",
    )
    args = parser.parse_args()

    if args.list:
        print("\nEventos disponíveis:\n")
        for eid, rule in RULES.items():
            marker = "✓" if eid in PAYLOADS else "✗"
            print(f"  {marker} {eid:40s} {rule['subject']}")
        print()
        return

    rule = RULES.get(args.event)
    if not rule:
        print(f"Evento '{args.event}' não encontrado.")
        sys.exit(1)

    use_emulator = not args.real

    print(f"\n{'='*60}")
    print(f"  Teste de e-mail — {args.event}")
    print(f"{'='*60}")
    print(f"  Destinatário: {args.to}")
    print(f"  Template ID:  {rule['template_id']}")
    print(f"  Subject:      {rule['subject']}")
    print(f"  Modo:         {'PRODUÇÃO' if args.real else 'Emulador'}")

    if args.real:
        confirm = input(
            "\n  ⚠  Modo REAL — vai publicar no PubSub e ENVIAR e-mail."
            "\n  Continuar? [y/N] "
        )
        if confirm.lower() != "y":
            print("  Cancelado.")
            sys.exit(0)

    init_firebase(use_emulator)

    print(f"\n  Criando documento na collection '{_COLLECTION}'...")
    doc_id = write_notification(args.to, args.event)
    print(f"  Doc criado: {_COLLECTION}/{doc_id}")

    if args.real:
        print("\n  Publicando no PubSub...")
        publish_pubsub(args.to, args.event)
        print("  Cloud Function deve processar e enviar o e-mail.")
    else:
        print(
            f"\n  Verifique no Emulator UI:"
            f"\n  http://localhost:4000/firestore/data/{_COLLECTION}/{doc_id}"
        )

    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
