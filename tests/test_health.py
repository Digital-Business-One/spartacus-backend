from unittest.mock import patch

from fastapi.testclient import TestClient


# Inicialização do Firebase Admin SDK é mockada para não precisar de credenciais em teste
with patch("firebase_admin.initialize_app"):
    from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # Diagnostic field (BUG-02): parameterized public routes must be registered.
    assert "^/projects/[^/]+/classes$" in body["public_routes"]


def test_me_sem_token_retorna_401():
    response = client.get("/me")
    assert response.status_code == 401  # AuthMiddleware retorna 401 antes do FastAPI processar


def test_me_com_token_invalido_retorna_401():
    response = client.get("/me", headers={"Authorization": "Bearer token-invalido"})
    assert response.status_code == 401
