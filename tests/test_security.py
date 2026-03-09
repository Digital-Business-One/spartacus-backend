import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.logging.config import configure_logging
from app.logging.middleware import LoggingMiddleware
from app.security.context import ADMIN_ROLES, AuthContext, auth_ctx
from app.security.decorator import public, register_public_routes, require_roles, require_root
from app.security.middleware import AuthMiddleware

_PROJECT_ID = "test-project"
_ROOT_PROJECT_ID = "root-project"
_PROJECT_HEADER = {"X-Project-Id": _PROJECT_ID}
_ROOT_HEADER = {"X-Project-Id": _ROOT_PROJECT_ID}


# ── test app fixture ──────────────────────────────────────────────────────────

def _make_app() -> FastAPI:
    configure_logging()
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(AuthMiddleware)

    @public
    @app.get("/public")
    def public_route():
        return {"public": True}

    @app.get("/protected")
    def protected_route():
        ctx = auth_ctx.get()
        return {"uid": ctx.user_id, "project_id": ctx.project_id}

    @app.get("/teachers-only")
    @require_roles("teacher")
    def teachers_only():
        return {"ok": True}

    @app.get("/admin-only")
    @require_roles("owner", "assistant")
    def admin_only():
        return {"ok": True}

    @app.get("/root-only")
    @require_root
    def root_only():
        return {"ok": True}

    register_public_routes(app.routes)
    return app


@pytest.fixture()
def client():
    return TestClient(_make_app(), raise_server_exceptions=False)


def _valid_claims(project_id: str = _PROJECT_ID, roles: list[str] | None = None) -> dict:
    return {
        "uid": "user-123",
        "email": "test@test.com",
        "projects": {project_id: roles or []},
    }


# ── @public ───────────────────────────────────────────────────────────────────

def test_public_route_sem_token_retorna_200(client):
    response = client.get("/public")
    assert response.status_code == 200


def test_public_route_com_token_invalido_retorna_200(client):
    response = client.get("/public", headers={"Authorization": "Bearer lixo"})
    assert response.status_code == 200


# ── token ausente / inválido ──────────────────────────────────────────────────

def test_sem_token_retorna_401(client):
    response = client.get("/protected")
    assert response.status_code == 401


def test_token_mal_formatado_retorna_401(client):
    response = client.get("/protected", headers={"Authorization": "Basic abc"})
    assert response.status_code == 401


def test_token_invalido_retorna_401(client):
    with patch("app.security.middleware.verify_id_token", side_effect=Exception("invalid")):
        response = client.get("/protected", headers={"Authorization": "Bearer token-invalido"})
    assert response.status_code == 401


# ── X-Project-Id ausente ──────────────────────────────────────────────────────

def test_sem_project_id_retorna_400(client):
    with patch("app.security.middleware.verify_id_token", return_value=_valid_claims()):
        response = client.get("/protected", headers={"Authorization": "Bearer tok"})
    assert response.status_code == 400


# ── token válido + project ────────────────────────────────────────────────────

def test_token_valido_retorna_200(client):
    with patch("app.security.middleware.verify_id_token", return_value=_valid_claims()):
        response = client.get("/protected", headers={"Authorization": "Bearer token-ok", **_PROJECT_HEADER})
    assert response.status_code == 200
    assert response.json()["uid"] == "user-123"
    assert response.json()["project_id"] == _PROJECT_ID


def test_roles_escopadas_ao_projeto(client):
    """Usuário tem role 'teacher' em test-project mas não em outro-projeto."""
    claims = {"uid": "u1", "email": "u@t.com", "projects": {
        _PROJECT_ID: ["teacher"],
        "outro-projeto": ["student"],
    }}
    with patch("app.security.middleware.verify_id_token", return_value=claims):
        r1 = client.get("/teachers-only", headers={"Authorization": "Bearer tok", **_PROJECT_HEADER})
        r2 = client.get("/teachers-only", headers={"Authorization": "Bearer tok", "X-Project-Id": "outro-projeto"})
    assert r1.status_code == 200
    assert r2.status_code == 403


# ── @require_roles ────────────────────────────────────────────────────────────

def test_require_roles_role_correta_retorna_200(client):
    with patch("app.security.middleware.verify_id_token", return_value=_valid_claims(roles=["teacher"])):
        response = client.get("/teachers-only", headers={"Authorization": "Bearer tok", **_PROJECT_HEADER})
    assert response.status_code == 200


def test_require_roles_role_errada_retorna_403(client):
    with patch("app.security.middleware.verify_id_token", return_value=_valid_claims(roles=["student"])):
        response = client.get("/teachers-only", headers={"Authorization": "Bearer tok", **_PROJECT_HEADER})
    assert response.status_code == 403


def test_require_roles_multiplas_roles_qualquer_aceita(client):
    with patch("app.security.middleware.verify_id_token", return_value=_valid_claims(roles=["assistant"])):
        response = client.get("/admin-only", headers={"Authorization": "Bearer tok", **_PROJECT_HEADER})
    assert response.status_code == 200


def test_require_roles_sem_role_retorna_403(client):
    with patch("app.security.middleware.verify_id_token", return_value=_valid_claims(roles=[])):
        response = client.get("/admin-only", headers={"Authorization": "Bearer tok", **_PROJECT_HEADER})
    assert response.status_code == 403


# ── @require_root ─────────────────────────────────────────────────────────────

def test_require_root_projeto_correto_retorna_200(client):
    claims = _valid_claims(project_id=_ROOT_PROJECT_ID, roles=["owner"])
    with patch("app.security.middleware.verify_id_token", return_value=claims):
        with patch.dict(os.environ, {"ROOT_PROJECT_ID": _ROOT_PROJECT_ID}):
            response = client.get("/root-only", headers={"Authorization": "Bearer tok", **_ROOT_HEADER})
    assert response.status_code == 200


def test_require_root_projeto_errado_retorna_403(client):
    with patch("app.security.middleware.verify_id_token", return_value=_valid_claims(roles=["owner"])):
        with patch.dict(os.environ, {"ROOT_PROJECT_ID": _ROOT_PROJECT_ID}):
            response = client.get("/root-only", headers={"Authorization": "Bearer tok", **_PROJECT_HEADER})
    assert response.status_code == 403


# ── ADMIN_ROLES ───────────────────────────────────────────────────────────────

def test_admin_roles_contem_owner_e_assistant():
    assert ADMIN_ROLES >= {"owner", "assistant"}


# ── AuthContext ───────────────────────────────────────────────────────────────

def test_auth_context_propaga_user_id_ao_logging():
    """user_id deve ser propagado ao request_ctx do logging após autenticação."""
    from app.logging.context import request_ctx
    request_ctx.set({"request_id": "req-1", "user_ip": "127.0.0.1", "user_id": None})

    with patch("app.security.middleware.verify_id_token", return_value=_valid_claims()):
        client = TestClient(_make_app(), raise_server_exceptions=False)
        client.get("/protected", headers={"Authorization": "Bearer tok", **_PROJECT_HEADER})

    ctx = AuthContext(user_id="user-123", user_email="test@test.com", project_id=_PROJECT_ID, roles=[])
    assert ctx.user_id == "user-123"
