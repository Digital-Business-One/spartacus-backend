"""
Fixtures de integração — Firebase Emulators gerenciados pelo pytest.

Ordem de execução por sessão:
  emulators (session) → sobe docker compose, aguarda :4000, derruba ao fim
  app_client (session) → TestClient apontando para os emuladores
  seed_root  (session, autouse) → limpa Firestore e semeia o projeto ROOT

Ordem por teste:
  restore_firestore (function, autouse) → após cada teste, limpa e re-semeia ROOT
"""
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

# ── Constantes dos emuladores ─────────────────────────────────────────────────

_EMULATOR_PROJECT = "spartacus-artes-marciais"
_ROOT_PROJECT_ID = "spartacus-artes-marciais"
_FIRESTORE_HOST = "localhost:8080"
_AUTH_HOST = "localhost:9099"
_STORAGE_HOST = "localhost:9199"

_REPO_ROOT = Path(__file__).parents[2]  # tests/integration -> repo root
# The emulator docker-compose lives in the sibling `infra` repo (monorepo was
# split into independent repos); its volumes are relative to that directory.
_COMPOSE_FILE = str(_REPO_ROOT.parent / "infra" / "docker-compose.yml")

# ── Env vars — definidas antes de qualquer import do app ─────────────────────

os.environ.setdefault("FIRESTORE_EMULATOR_HOST", _FIRESTORE_HOST)
os.environ.setdefault("FIREBASE_AUTH_EMULATOR_HOST", _AUTH_HOST)
os.environ.setdefault("FIREBASE_STORAGE_EMULATOR_HOST", _STORAGE_HOST)
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", _EMULATOR_PROJECT)
os.environ.setdefault("ROOT_PROJECT_ID", _ROOT_PROJECT_ID)

# ── Credencial fake para os emuladores ────────────────────────────────────────
# O Firebase Admin SDK busca ADC ao criar clientes Firestore/Storage. Os
# emuladores não validam tokens, então uma credencial fake é suficiente.
# Inicializamos ANTES de importar app.main (que chama initialize_app()).
# app.main trata o ValueError "already initialized" com try/except.

import firebase_admin  # noqa: E402
import google.oauth2.credentials  # noqa: E402
from firebase_admin import credentials as fb_creds  # noqa: E402


class _EmulatorCredential(fb_creds.Base):
    """Credencial fake para Firebase Emulator — dispensa ADC real."""

    def get_credential(self) -> google.oauth2.credentials.Credentials:
        return google.oauth2.credentials.Credentials(token="fake-emulator-token")


try:
    firebase_admin.get_app()
except ValueError:
    firebase_admin.initialize_app(
        credential=_EmulatorCredential(),
        options={
            "projectId": _EMULATOR_PROJECT,
            "storageBucket": f"{_EMULATOR_PROJECT}.appspot.com",
        },
    )

# Import APÓS inicializar firebase_admin com credencial fake
from app.main import app  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


def _clear_firestore() -> None:
    """Apaga todos os documentos do Firestore emulado via REST API."""
    httpx.delete(
        f"http://{_FIRESTORE_HOST}/emulator/v1/projects/"
        f"{_EMULATOR_PROJECT}/databases/(default)/documents",
        timeout=10,
    )


def _seed_root_doc() -> None:
    """Insere o documento do projeto ROOT no Firestore emulado."""
    from firebase_admin import firestore

    db = firestore.client()
    now = datetime.now(timezone.utc).isoformat()
    db.collection("projects").document(_ROOT_PROJECT_ID).set(
        {
            "id": _ROOT_PROJECT_ID,
            "name": "Spartacus Artes Marciais",
            "razao_social": "Associacao Projeto Spartacus Artes Marciais",
            "cnpj": "59.933.142/0001-54",
            "address": "Rua Rotary Internacional, 270",
            "city": "Brasnorte",
            "state": "MT",
            "zip_code": "78350-000",
            "legal_nature": "Associação Privada (399-9)",
            "founded_at": "2025-03-06",
            "logo_url": None,
            "is_root": True,
            "created_at": now,
        }
    )


def _wait_for_emulators(timeout: float = 120.0, interval: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get("http://localhost:4000", timeout=2).is_success:
                return
        except Exception:
            pass
        time.sleep(interval)
    raise RuntimeError("Firebase emulators não ficaram prontos no tempo limite.")


# ── Fixtures de sessão ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def emulators():
    """Sobe os Firebase Emulators via docker compose e derruba ao fim da sessão."""
    subprocess.run(
        ["docker", "compose", "-f", _COMPOSE_FILE, "up", "-d", "emulators"],
        check=True,
    )
    _wait_for_emulators()
    yield
    subprocess.run(
        ["docker", "compose", "-f", _COMPOSE_FILE, "stop", "emulators"],
        check=True,
    )
    subprocess.run(
        ["docker", "compose", "-f", _COMPOSE_FILE, "rm", "-f", "emulators"],
        check=True,
    )


@pytest.fixture(scope="session")
def app_client(emulators):
    """TestClient apontado para os Firebase Emulators. Reutilizado na sessão."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="session", autouse=True)
def seed_root(app_client):
    """Limpa o Firestore e semeia o projeto ROOT uma vez por sessão."""
    _clear_firestore()
    _seed_root_doc()
    yield


# ── Fixture por teste ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def restore_firestore(seed_root):
    """Após cada teste, restaura o Firestore ao estado inicial (ROOT apenas)."""
    yield
    _clear_firestore()
    _seed_root_doc()
