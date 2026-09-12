from fastapi.testclient import TestClient

from server import app as adapter
from server.maintenance import maintenance_lock


def test_maintenance_rejects_new_writes_but_serves_health():
    with TestClient(adapter.app) as client:
        with maintenance_lock(adapter.DATA, exclusive=True):
            assert client.get("/api/health").status_code == 200
            response = client.post("/api/login", json={"password": adapter.PASSWORD})
            assert response.status_code == 503
            assert "维护" in response.text
        assert client.post("/api/login", json={"password": adapter.PASSWORD}).status_code == 200


def test_frontend_missing_returns_actionable_html_without_local_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "DIST", tmp_path / "absent-dist")
    with TestClient(adapter.app) as client:
        response = client.get("/")
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("text/html")
        assert "bash scripts/relay.sh repair" in response.text
        assert str(tmp_path) not in response.text
        assert response.headers["cache-control"] == "no-store"
