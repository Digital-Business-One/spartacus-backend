"""
Fixtures de integração para test_collapse_approval.

Usa os Firebase Emulators já em execução (Firestore :8181, Auth :9099).
Caso os emuladores não estejam rodando, os testes são marcados como SKIP.
"""
import os

import httpx
import pytest

# ── Portas alternativas (8080 ocupado por outro serviço no ambiente) ──────────
_EMULATOR_PROJECT = "spartacus-artes-marciais"
_FIRESTORE_HOST = "localhost:8181"
_AUTH_HOST = "localhost:9099"
_STORAGE_HOST = "localhost:9199"

# Definir vars ANTES de qualquer import do firebase_admin / app
os.environ["FIRESTORE_EMULATOR_HOST"] = _FIRESTORE_HOST
os.environ["FIREBASE_AUTH_EMULATOR_HOST"] = _AUTH_HOST
os.environ["FIREBASE_STORAGE_EMULATOR_HOST"] = _STORAGE_HOST
os.environ["GOOGLE_CLOUD_PROJECT"] = _EMULATOR_PROJECT
os.environ["ROOT_PROJECT_ID"] = _EMULATOR_PROJECT

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

from app.main import app  # noqa: E402


def _emulators_running() -> bool:
    """Retorna True se Firestore e Auth emuladores estão acessíveis."""
    try:
        r1 = httpx.get(f"http://{_FIRESTORE_HOST}", timeout=2)
        r2 = httpx.get(f"http://{_AUTH_HOST}", timeout=2)
        return r1.is_success and r2.is_success
    except Exception:
        return False


def _clear_firestore() -> None:
    httpx.delete(
        f"http://{_FIRESTORE_HOST}/emulator/v1/projects/"
        f"{_EMULATOR_PROJECT}/databases/(default)/documents",
        timeout=10,
    )


def _seed_root_doc() -> None:
    from datetime import datetime, timezone

    from firebase_admin import firestore

    db = firestore.client()
    now = datetime.now(timezone.utc).isoformat()
    db.collection("projects").document(_EMULATOR_PROJECT).set(
        {
            "id": _EMULATOR_PROJECT,
            "name": "Spartacus Artes Marciais",
            "is_root": True,
            "created_at": now,
        }
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def emulators():
    """Valida que os emuladores estão em execução; pula a sessão se não estiverem."""
    if not _emulators_running():
        pytest.skip(
            "Firebase emulators não acessíveis em "
            f"Firestore:{_FIRESTORE_HOST} / Auth:{_AUTH_HOST}. "
            "Inicie com: firebase emulators:start --only firestore,auth"
        )
    yield


@pytest.fixture(scope="session")
def app_client(emulators):
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="session", autouse=True)
def seed_root(app_client):
    _clear_firestore()
    _seed_root_doc()
    yield


@pytest.fixture(autouse=True)
def restore_firestore(seed_root):
    yield
    _clear_firestore()
    _seed_root_doc()
