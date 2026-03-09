"""
Testes de integração — Entidade Projeto.

Diferença dos testes unitários:
  - Firestore real (emulador): dados são gravados e lidos de verdade.
  - verify_id_token ainda mockado (auth coberta pelos unit tests).
  - Valida round-trip completo: POST /projects → Firestore → GET /projects/{id}.
"""
import os
from unittest.mock import patch

_ROOT_ID = "demo-spartacus"
_NEW_PROJECT_ID = "integration-test-project"
_AUTH = "Bearer tok"


def _claims(project_id: str, roles: list[str]) -> dict:
    return {
        "uid": "user-integration",
        "email": "integration@test.com",
        "projects": {project_id: roles},
    }


def _headers(project_id: str) -> dict:
    return {"Authorization": _AUTH, "X-Project-Id": project_id}


# ── POST /projects ─────────────────────────────────────────────────────────────


class TestCreateProjectIntegration:
    _PAYLOAD = {
        "id": _NEW_PROJECT_ID,
        "name": "Integration Test Project",
        "cnpj": "00.000.000/0001-99",
        "razao_social": "Projeto Integração Ltda",
        "city": "Cuiabá",
        "state": "MT",
    }

    def test_cria_projeto_e_persiste_no_firestore(self, app_client):
        """POST cria o documento no Firestore; GET subsequente retorna os dados."""
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_ROOT_ID, ["owner"]),
        ):
            with patch.dict(os.environ, {"ROOT_PROJECT_ID": _ROOT_ID}):
                post = app_client.post(
                    "/projects",
                    json=self._PAYLOAD,
                    headers=_headers(_ROOT_ID),
                )

        assert post.status_code == 201
        body = post.json()
        assert body["id"] == _NEW_PROJECT_ID
        assert body["cnpj"] == "00.000.000/0001-99"
        assert body["is_root"] is False
        assert "created_at" in body

        # Confirma persistência lendo do Firestore emulado
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_NEW_PROJECT_ID, ["owner"]),
        ):
            get = app_client.get(
                f"/projects/{_NEW_PROJECT_ID}",
                headers=_headers(_NEW_PROJECT_ID),
            )

        assert get.status_code == 200
        stored = get.json()
        assert stored["id"] == _NEW_PROJECT_ID
        assert stored["razao_social"] == "Projeto Integração Ltda"
        assert stored["city"] == "Cuiabá"

    def test_campos_opcionais_nulos_preservados(self, app_client):
        """Campos opcionais não enviados devem ser None no retorno."""
        payload = {"id": _NEW_PROJECT_ID, "name": "Minimal Project"}

        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_ROOT_ID, ["owner"]),
        ):
            with patch.dict(os.environ, {"ROOT_PROJECT_ID": _ROOT_ID}):
                post = app_client.post(
                    "/projects",
                    json=payload,
                    headers=_headers(_ROOT_ID),
                )

        assert post.status_code == 201
        body = post.json()
        assert body["cnpj"] is None
        assert body["logo_url"] is None
        assert body["address"] is None


# ── GET /projects/{project_id} ─────────────────────────────────────────────────


class TestGetProjectIntegration:
    def test_retorna_projeto_root_semeado(self, app_client):
        """Projeto ROOT semeado pela fixture de sessão deve ser retornável."""
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_ROOT_ID, ["owner"]),
        ):
            response = app_client.get(
                f"/projects/{_ROOT_ID}",
                headers=_headers(_ROOT_ID),
            )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == _ROOT_ID
        assert body["cnpj"] == "59.933.142/0001-54"
        assert body["is_root"] is True
        assert body["city"] == "Brasnorte"

    def test_projeto_inexistente_retorna_404(self, app_client):
        """GET em projeto que não existe deve retornar 404."""
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims("ghost-project", ["owner"]),
        ):
            response = app_client.get(
                "/projects/ghost-project",
                headers=_headers("ghost-project"),
            )

        assert response.status_code == 404

    def test_isolamento_entre_testes(self, app_client):
        """Dados criados em testes anteriores não devem vazar (restore_firestore)."""
        with patch(
            "app.security.middleware.verify_id_token",
            return_value=_claims(_NEW_PROJECT_ID, ["owner"]),
        ):
            response = app_client.get(
                f"/projects/{_NEW_PROJECT_ID}",
                headers=_headers(_NEW_PROJECT_ID),
            )

        # Projeto criado em testes anteriores não deve existir (limpeza automática)
        assert response.status_code == 404
