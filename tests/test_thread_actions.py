import asyncio
import sqlite3
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from server import app as adapter


@pytest.fixture
def setup(tmp_path, monkeypatch):
    def db():
        c = sqlite3.connect(tmp_path / "relay.sqlite")
        c.row_factory = sqlite3.Row
        return c

    with db() as c:
        c.executescript(
            "CREATE TABLE jobs(thread_id TEXT,status TEXT);CREATE TABLE pending(thread_id TEXT,status TEXT);"
        )
    monkeypatch.setattr(adapter, "db", db)
    monkeypatch.setattr(adapter, "find_thread", lambda tid: {"id": tid})
    monkeypatch.setattr(adapter, "writer_alive", lambda tid: False)
    monkeypatch.setattr(adapter, "clients", {})
    calls = []

    async def call(tid, method, params):
        calls.append((tid, method, params))
        return {}

    monkeypatch.setattr(adapter, "thread_metadata_call", call)
    client = TestClient(adapter.app)
    client.cookies.set("relay_session", adapter.SESSION)
    client.headers["X-Relay-Request"] = "1"
    return client, db, calls


def test_rename_trim_validation_and_auth(setup):
    client, db, calls = setup
    assert (
        client.post("/api/threads/t/rename", json={"name": "  新名称 中文  "}).json()["title"]
        == "新名称 中文"
    )
    assert calls == [("t", "thread/name/set", {"name": "新名称 中文"})]
    for name in ["   ", "x" * 121, "bad\nname", "bad\x00name"]:
        assert client.post("/api/threads/t/rename", json={"name": name}).status_code in (400, 422)
    assert len(calls) == 1
    assert (
        TestClient(adapter.app).post("/api/threads/t/rename", json={"name": "x"}).status_code == 401
    )
    assert TestClient(adapter.app).post("/api/threads/t/archive").status_code == 401
    client.headers.pop("X-Relay-Request")
    assert client.post("/api/threads/t/archive").status_code == 403


@pytest.mark.parametrize("reason", ["job", "queued", "writer", "client"])
def test_archive_rejects_busy_thread_without_native_call(setup, monkeypatch, reason):
    client, db, calls = setup
    if reason == "job":
        with db() as c:
            c.execute("INSERT INTO jobs VALUES('t','running')")
    elif reason == "queued":
        with db() as c:
            c.execute("INSERT INTO pending VALUES('t','queued')")
    elif reason == "writer":
        monkeypatch.setattr(adapter, "writer_alive", lambda tid: True)
    else:
        monkeypatch.setattr(adapter, "clients", {"t": object()})
    assert client.post("/api/threads/t/archive").status_code == 409
    assert not calls


def test_archive_success_and_error_keep_state(setup, monkeypatch):
    client, db, calls = setup
    adapter.cache["t"] = {"existing": True}

    async def fail(*a):
        raise HTTPException(409, "Native archive failed")

    monkeypatch.setattr(adapter, "thread_metadata_call", fail)
    assert client.post("/api/threads/t/archive").status_code == 409
    assert adapter.cache["t"] == {"existing": True}

    async def success(*a):
        calls.append(a)

    monkeypatch.setattr(adapter, "thread_metadata_call", success)
    assert client.post("/api/threads/t/archive").json()["archived"]
    assert calls == [("t", "thread/archive", {})] and "t" not in adapter.cache


def test_archived_selected_thread_emits_removal_instead_of_error(setup, monkeypatch):
    client, db, calls = setup

    # Only one SSE iteration, no real sleeping needed.
    class Request:
        n = 0

        async def is_disconnected(self):
            self.n += 1
            return self.n > 1

    with db() as c:
        c.execute("DROP TABLE jobs")
        c.execute(
            "CREATE TABLE jobs(id TEXT,thread_id TEXT,title TEXT,status TEXT,error TEXT,created REAL)"
        )
    monkeypatch.setattr(adapter, "rows", lambda: [])

    def missing(tid):
        raise HTTPException(404, "archived")

    monkeypatch.setattr(adapter, "find_thread", missing)

    async def no_sleep(*a):
        pass

    monkeypatch.setattr(adapter.asyncio, "sleep", no_sleep)

    async def run():
        response = await adapter.events(Request(), "archived")
        return [chunk async for chunk in response.body_iterator]

    chunks = asyncio.run(run())
    assert len(chunks) == 1
    assert "removedThread" in chunks[0] and "backend-error" not in chunks[0]


def test_names_and_archive_filter_come_from_native_database(tmp_path, monkeypatch):
    path = tmp_path / "state_5.sqlite"
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE threads(id TEXT,title TEXT,name TEXT,cwd TEXT,model TEXT,source TEXT,updated_at REAL,rollout_path TEXT,tokens_used INTEGER,archived INTEGER)"
        )
        c.executemany(
            "INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                ("renamed", "old", "新名称", "/tmp", "model", "test", 3, "", 0, 0),
                ("default", "original", None, "/tmp", "model", "test", 2, "", 0, 0),
                ("archived", "archived", "hidden", "/tmp", "model", "test", 1, "", 0, 1),
            ],
        )
    monkeypatch.setattr(adapter, "CODEX_HOME", tmp_path)
    assert [(r["id"], r["title"]) for r in adapter.rows()] == [
        ("renamed", "新名称"),
        ("default", "original"),
    ]
