"""Portable startup, first-use behavior, settings validation and migration regression."""

from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient
from server import app as adapter
from server.config import load_settings
from server.storage import access_password, connect, initialize


def test_paths_environment_precedence_and_client_config(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    config = tmp_path / "relay.toml"
    config.write_text("""[server]
port=8123
[paths]
workspace_root="work"
default_cwd="work"
data_dir="data/nested"
[codex]
binary="tools/codex"
[ui]
default_model="custom-model"
default_effort="high"
models=[{id="custom-model",name="Custom"}]
""")
    settings = load_settings({"RELAY_PORT": "8124", "RELAY_SECURE_COOKIE": "true"}, tmp_path)
    assert settings.port == 8124 and settings.secure_cookie
    assert settings.workspace_root == workspace
    assert settings.data_dir == tmp_path / "data/nested"
    assert settings.codex_bin == str(tmp_path / "tools/codex")
    assert settings.client_config()["defaultModel"] == "custom-model"
    assert settings.client_config()["defaultEffort"] == "high"
    assert "codex_home" not in settings.client_config()


@pytest.mark.parametrize(
    "content",
    [
        "[unknown]\nx=1",
        "[server]\nprot=8000",
        "[server]\nport=0",
        '[server]\nsecure_cookie="maybe"',
        "[ui]\nmodels=[]",
        '[ui]\ndefault_effort="ultra"',
        '[ui]\ndefault_model="missing"',
        '[ui]\nmodels=[{id="one",name="One"},{id="one",name="Duplicate"}]',
        '[paths]\nworkspace_root="missing"',
    ],
)
def test_invalid_configuration_fails_early(tmp_path, content):
    (tmp_path / "relay.toml").write_text(content)
    with pytest.raises(ValueError):
        load_settings({}, tmp_path)


def test_missing_explicit_config_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        load_settings({"RELAY_CONFIG_FILE": "absent.toml"}, tmp_path)


def test_default_paths_follow_user_home_and_existing_workspace(tmp_path):
    settings = load_settings({}, tmp_path)
    assert settings.codex_home == Path.home() / ".codex"
    assert settings.codex_bin == "codex"
    assert settings.host == "127.0.0.1"
    assert settings.default_cwd == settings.workspace_root


def test_clean_first_login_has_no_sessions_and_does_not_create_codex_db(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "CODEX_HOME", tmp_path / "empty-codex")
    client = TestClient(adapter.app)
    assert client.get("/api/threads").status_code == 401
    assert client.post("/api/login", json={"password": adapter.PASSWORD}).status_code == 200
    result = client.get("/api/threads")
    assert result.status_code == 200
    assert result.json()["threads"] == []
    assert result.json()["clientConfig"]["models"]
    assert not (tmp_path / "empty-codex").exists()
    assert client.get("/api/directories").status_code == 200


def test_existing_incompatible_database_remains_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "CODEX_HOME", tmp_path)
    with sqlite3.connect(tmp_path / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(wrong TEXT)")
    client = TestClient(adapter.app)
    client.cookies.set(adapter.SESSION_COOKIE, adapter.SESSION)
    assert client.get("/api/threads").status_code == 503


def test_password_persists_with_private_permissions_and_rejects_empty(tmp_path):
    first = access_password(tmp_path)
    assert access_password(tmp_path) == first
    path = tmp_path / "access.txt"
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("")
    with pytest.raises(ValueError):
        access_password(tmp_path)


def test_schema_upgrade_is_repeatable_and_keeps_existing_records(tmp_path):
    with connect(tmp_path) as db:
        db.execute(
            "CREATE TABLE jobs(id TEXT PRIMARY KEY, thread_id TEXT, title TEXT,cwd TEXT,status TEXT,pid INTEGER,created REAL,error TEXT)"
        )
        db.execute("INSERT INTO jobs(id,title) VALUES('existing','Keep me')")
    initialize(tmp_path)
    initialize(tmp_path)
    with connect(tmp_path) as db:
        row = db.execute("SELECT * FROM jobs WHERE id='existing'").fetchone()
        assert row["title"] == "Keep me" and row["options"] == "{}"
        assert {"model", "effort", "options"} <= set(row.keys())
