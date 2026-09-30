"""Client de test qui se comporte comme le frontend de Kilo : cookies + en-tête CSRF.

Plusieurs ApiClient peuvent partager le même TestClient (une seule boucle d'événements,
un seul cycle de vie de l'app) tout en simulant des navigateurs distincts : chacun garde
son propre jeu de cookies.
"""

import uuid
from typing import Any

import httpx2 as httpx
from fastapi.testclient import TestClient

from digital360.core.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME

PASSWORD = "Maquis-du-Plateau-2026"


class ApiClient:
    def __init__(self, client: TestClient) -> None:
        self.http = client
        self.jar = httpx.Cookies()

    @property
    def cookies(self) -> dict[str, str]:
        return dict(self.jar.items())

    def set_cookie(self, name: str, value: str) -> None:
        for cookie in list(self.jar.jar):
            if cookie.name == name:
                self.jar.jar.clear(cookie.domain, cookie.path, cookie.name)
        self.jar.set(name, value, domain="testserver.local")

    def _send(self, method: str, path: str, json: Any = None, *, csrf: bool) -> httpx.Response:
        # Le TestClient est partagé : on lui confie le jar de CE navigateur le temps de l'appel
        self.http.cookies = self.jar
        headers = {}
        if csrf:
            token = self.http.cookies.get(CSRF_COOKIE_NAME)
            if token is None:
                token = self.http.get("/api/v1/auth/csrf").json()["csrf_token"]
            headers[CSRF_HEADER_NAME] = token
        response = self.http.request(method, f"/api/v1{path}", json=json, headers=headers)
        self.jar = httpx.Cookies(self.http.cookies)
        return response

    def get(self, path: str) -> httpx.Response:
        return self._send("GET", path, csrf=False)

    def post(self, path: str, json: Any = None) -> httpx.Response:
        return self._send("POST", path, json, csrf=True)

    def patch(self, path: str, json: Any = None) -> httpx.Response:
        return self._send("PATCH", path, json, csrf=True)

    def request(self, method: str, path: str, json: Any = None) -> httpx.Response:
        return self._send(method, path, json, csrf=method not in {"GET", "HEAD"})

    def post_without_csrf(self, path: str, json: Any = None) -> httpx.Response:
        return self._send("POST", path, json, csrf=False)

    def register(self, email: str | None = None, **extra: Any) -> dict[str, Any]:
        email = email or f"user-{uuid.uuid4().hex[:10]}@exemple.ci"
        response = self.post(
            "/auth/register",
            {"email": email, "password": PASSWORD, "full_name": "Awa Koné", **extra},
        )
        assert response.status_code == 201, response.json()
        body: dict[str, Any] = response.json()
        return body

    def create_organization(self, name: str = "Maquis Le Délice") -> dict[str, Any]:
        response = self.post("/orgs", {"commercial_name": name, "country": "CI"})
        assert response.status_code == 201, response.json()
        body: dict[str, Any] = response.json()
        return body
