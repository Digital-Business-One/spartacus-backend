#!/usr/bin/env python3
"""
Script utilitário para testar o pipeline de e-mail end-to-end.

Fluxo completo (RFC-10 + RFC-11):

    1. Script escreve doc em `events` collection
    2. Eventarc trigger → orchestrator Cloud Function
    3. Orchestrator lê EVENT_RULES → escreve doc em `notifications`
       (e potencialmente em timeline_entries / push_queue)
    4. Eventarc trigger → send_email Cloud Function
    5. send_email → SendGrid → caixa de entrada

A "collection adequada" agora é `events` (e NÃO `notifications`),
porque a partir da RFC-10 o orquestrador é quem decide o que fazer
com cada evento (template, canais, destinatários).

Uso:
    # Emulador local (padrão)
    uv run python scripts/test_email.py

    # Escolher evento
    uv run python scripts/test_email.py --event signup.email_confirmation
    uv run python scripts/test_email.py --event account.approve

    # Contra projeto real (cuidado: vai disparar email de verdade!)
    uv run python scripts/test_email.py --to seu@email.com --real

    # Listar eventos disponíveis
    uv run python scripts/test_email.py --list
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import firebase_admin
from firebase_admin import credentials, firestore

# ── Constants ────────────────────────────────────────────────────────────────

_APP_URL = "https://spartacus.app.br"
_COLLECTION = "events"
_DEFAULT_PROJECT_ID = "spartacus-artes-marciais"
_SOURCE = "test_email_script"


# ── Payloads por evento ──────────────────────────────────────────────────────
# Estes payloads são equivalentes ao que .personalization() produz
# nos dataclasses em app/events/models.py. O orquestrador (em
# functions/orchestrator/main.py) lê esses campos para decidir
# template, recipients, etc.

PAYLOADS: dict[str, dict] = {
    "signup.email_confirmation": {
        "uid": "test-uid-001",
        "entity_id": "test-uid-001",
        "source_entity_ref": "users/test-uid-001",
        "source_entity_type": "users",
        "target_uid": "test-uid-001",
        "target_name": "João Teste da Silva",
        "author_uid": "test-uid-001",
        "author_name": "João Teste da Silva",
        "title": "Nova conta criada",
        "description": "João Teste da Silva (Aluno, Responsável)",
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
        "uid": "test-uid-002",
        "entity_id": "test-uid-002",
        "source_entity_ref": "users/test-uid-002",
        "source_entity_type": "users",
        "target_uid": "test-uid-002",
        "target_name": "João Teste da Silva",
        "author_uid": "test-uid-002",
        "author_name": "João Teste da Silva",
        "title": "Nova conta criada",
        "description": "João Teste da Silva (Aluno)",
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
        "to": "joao.teste@example.com",
        "link": "https://example.com/verify?fake_token=xyz789",
        "show_link": True,
    },
    "account.approve": {
        "to": "joao.teste@example.com",
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
        "to": "joao.teste@example.com",
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
        "to": "joao.teste@example.com",
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
        "to": "joao.teste@example.com",
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
        "to": "joao.teste@example.com",
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
    "account.submit_medical_history": {
        "to": "joao.teste@example.com",
        "name": "João Teste da Silva",
        "title": "Anamnese enviada",
        "message": (
            "Sua anamnese foi recebida e está em análise pela equipe do "
            "Spartacus. Em breve você receberá uma confirmação."
        ),
        "cta_text": "",
        "cta_url": "",
    },
}


# ── Firebase init ────────────────────────────────────────────────────────────

def init_firebase(use_emulator: bool, project_id: str) -> None:
    if use_emulator:
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", "localhost:8080")
        print(f"  Emulador: {os.environ['FIRESTORE_EMULATOR_HOST']}")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)

    if not firebase_admin._apps:
        if use_emulator:
            firebase_admin.initialize_app(options={"projectId": project_id})
        else:
            firebase_admin.initialize_app(
                credentials.ApplicationDefault(),
                options={"projectId": project_id},
            )


# ── Write event ──────────────────────────────────────────────────────────────

def write_event(
    project_id: str,
    event_id: str,
    payload: dict,
    to_override: str | None,
) -> str:
    """Write a document to the `events` collection.

    The orchestrator will pick it up via Eventarc trigger and dispatch
    to all channels declared in EVENT_RULES (email, timeline, push, etc.).
    """
    # Override email recipient if --to was passed
    if to_override:
        if "email" in payload:
            payload["email"] = to_override
        if "to" in payload:
            payload["to"] = to_override
        # If neither field exists, set both
        if "email" not in payload and "to" not in payload:
            payload["email"] = to_override
            payload["to"] = to_override

    doc_data = {
        "eventId": event_id,
        "projectId": project_id,
        "source": _SOURCE,
        "payload": payload,
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "processedAt": None,
        "error": None,
    }

    db = firestore.client()
    _, doc_ref = db.collection(_COLLECTION).add(doc_data)
    return doc_ref.id


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Testa o pipeline de eventos: escreve em `events` "
            "→ orchestrator → notifications → send_email"
        ),
    )
    parser.add_argument(
        "--to",
        default=None,
        help="E-mail destinatário (sobrescreve o do payload)",
    )
    parser.add_argument(
        "--event",
        choices=list(PAYLOADS.keys()),
        default="signup.email_confirmation",
        help="Evento a testar",
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT", _DEFAULT_PROJECT_ID),
        help=f"Project ID (default: {_DEFAULT_PROJECT_ID})",
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help=(
            "Modo real: escreve no Firestore de produção. "
            "Eventarc → orchestrator → send_email → SendGrid."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Listar eventos disponíveis",
    )
    args = parser.parse_args()

    if args.list:
        print("\nEventos disponíveis:\n")
        for eid in PAYLOADS.keys():
            print(f"  • {eid}")
        print()
        return

    if args.event not in PAYLOADS:
        print(f"Evento '{args.event}' não encontrado.")
        sys.exit(1)

    use_emulator = not args.real
    payload = dict(PAYLOADS[args.event])  # copy

    print(f"\n{'=' * 60}")
    print(f"  Teste de e-mail — {args.event}")
    print(f"{'=' * 60}")
    recipient = (
        args.to
        or payload.get("email")
        or payload.get("to")
        or "(do payload)"
    )
    print(f"  Evento:       {args.event}")
    print(f"  Projeto:      {args.project_id}")
    print(f"  Destinatário: {recipient}")
    print(f"  Modo:         {'PRODUÇÃO' if args.real else 'Emulador'}")
    print(f"  Collection:   {_COLLECTION}")

    if args.real:
        confirm = input(
            "\n  ⚠  Modo REAL — escreve no Firestore de produção."
            "\n  Eventarc aciona orchestrator → send_email → SendGrid."
            "\n  Continuar? [y/N] "
        )
        if confirm.lower() != "y":
            print("  Cancelado.")
            sys.exit(0)

    init_firebase(use_emulator, args.project_id)

    print(f"\n  Criando documento na collection '{_COLLECTION}'...")
    doc_id = write_event(args.project_id, args.event, payload, args.to)
    print(f"  Doc criado: {_COLLECTION}/{doc_id}")

    if args.real:
        print(
            "\n  Eventarc deve acionar o orchestrator automaticamente."
            "\n  Acompanhe os logs:"
            "\n    gcloud functions logs read event-orchestrator"
            " --region us-east1 --gen2 --limit 20"
            "\n    gcloud functions logs read send-email"
            " --region us-east1 --gen2 --limit 20"
        )
    else:
        print(
            "\n  Verifique no Emulator UI:"
            f"\n    http://localhost:4000/firestore/data/{_COLLECTION}/{doc_id}"
        )

    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
