from unittest.mock import patch

from fastapi.testclient import TestClient


# Inicialização do Firebase Admin SDK é mockada para não precisar de credenciais em teste
with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_router_routes_are_registered():
    """Regression guard (BUG-02): include_router() routes must land in the app.

    A newer Starlette (shipped to prod because the Docker image didn't pin
    uv.lock) silently dropped every include_router route, so AuthMiddleware
    returned 401 for all of them. Keep an explicit check that public router
    routes exist and are reachable without a token.
    """
    from app.security.decorator import _PUBLIC_PATTERNS

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/projects/{project_id}/classes" in paths
    assert "/projects" in paths
    # The classes listing must be registered as public (AuthMiddleware lets it
    # through without a token).
    assert any(
        method == "GET" and pattern.match("/projects/spartacus/classes")
        for method, pattern in _PUBLIC_PATTERNS
    )


def test_me_sem_token_retorna_401():
    response = client.get("/me")
    assert response.status_code == 401  # AuthMiddleware retorna 401 antes do FastAPI processar


def test_me_com_token_invalido_retorna_401():
    response = client.get("/me", headers={"Authorization": "Bearer token-invalido"})
    assert response.status_code == 401
