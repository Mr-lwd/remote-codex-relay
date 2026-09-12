"""Authentication rejects invalid credentials and preserves cookie security attributes."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from server import app as adapter


@pytest.mark.parametrize("password", ["incorrect-password", "错误口令"])
def test_invalid_password_returns_401(password):
    with TestClient(adapter.app) as client:
        response = client.post("/api/login", json={"password": password})
        assert response.status_code == 401
        assert "set-cookie" not in response.headers


def test_unicode_password_can_log_in(monkeypatch):
    monkeypatch.setattr(adapter, "PASSWORD", "测试口令-123456")
    with TestClient(adapter.app) as client:
        response = client.post("/api/login", json={"password": adapter.PASSWORD})
        assert response.status_code == 200
        assert client.get("/api/threads").status_code == 200


def test_unicode_cookie_is_unauthorized():
    with TestClient(adapter.app) as client:
        response = client.get(
            "/api/threads", headers={"Cookie": f'{adapter.SESSION_COOKIE}="\\351"'}
        )
        assert response.status_code == 401


def test_legacy_cookie_does_not_authenticate():
    with TestClient(adapter.app) as client:
        client.cookies.set("relay_session", adapter.SESSION)
        assert client.get("/api/threads").status_code == 401


@pytest.mark.parametrize(
    ("scheme", "configured_secure", "expected_secure"),
    [("http", False, False), ("https", False, True), ("https", True, True)],
)
def test_cookie_security_and_logout(monkeypatch, scheme, configured_secure, expected_secure):
    monkeypatch.setattr(
        adapter, "settings", replace(adapter.settings, secure_cookie=configured_secure)
    )
    with TestClient(adapter.app, base_url=f"{scheme}://relay.example.com") as client:
        response = client.post("/api/login", json={"password": adapter.PASSWORD})
        assert response.status_code == 200
        cookie = next(iter(client.cookies.jar))
        assert cookie.secure == expected_secure
        assert cookie.path == "/"
        assert not cookie.domain_specified
        assert cookie.has_nonstandard_attr("HttpOnly")
        assert cookie.get_nonstandard_attr("SameSite") == "strict"
        assert client.get("/api/threads").status_code == 200
        assert client.post("/api/logout", headers={"X-Relay-Request": "1"}).status_code == 200
        assert client.get("/api/threads").status_code == 401
