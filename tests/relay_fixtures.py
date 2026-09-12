from fastapi.testclient import TestClient
from server.media import MediaStore
import asyncio
import sqlite3
import pytest
from server import app as adapter


@pytest.fixture
def relay(tmp_path, monkeypatch):
    with adapter.db() as c:
        schemas = [
            r[0]
            for r in c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name IN ('jobs','pending','notes','command_records')"
            )
        ]

    def db():
        c = sqlite3.connect(tmp_path / "relay.sqlite")
        c.row_factory = sqlite3.Row
        return c

    with db() as c:
        for schema in schemas:
            c.execute(schema)
    monkeypatch.setattr(adapter, "db", db)
    row = {
        "id": "t",
        "cwd": str(tmp_path),
        "rollout_path": str(tmp_path / "missing"),
        "title": "Test",
    }
    monkeypatch.setattr(adapter, "find_thread", lambda _: row)
    monkeypatch.setattr(
        adapter,
        "read_rollout",
        lambda _: {
            "status": "idle",
            "tokens": 0,
            "started": None,
            "messages": [],
            "activities": [],
        },
    )
    monkeypatch.setattr(adapter, "writer_alive", lambda _: False)
    monkeypatch.setattr(adapter, "clients", {})
    monkeypatch.setattr(adapter, "processes", {})
    monkeypatch.setattr(adapter, "tasks", set())
    monkeypatch.setattr(adapter, "job_tasks", {})
    monkeypatch.setattr(adapter, "send_locks", __import__("collections").defaultdict(asyncio.Lock))
    calls = []

    class RPC:
        def __init__(self, *args):
            self.proc = type("Process", (), {"pid": 123})()
            self.inbox = {}

        async def start(self):
            return self

        async def close(self):
            pass

        async def call(self, method, params):
            calls.append((method, params))
            if method in ("thread/start", "thread/resume"):
                return {"thread": {"id": "t"}, "model": "gpt-5.6-sol"}
            if method == "turn/start":
                return {"turn": {"id": "turn"}}
            if method == "thread/goal/get":
                return {"goal": getattr(self, "goal", None)}

        async def wait_event(self, events, *args):
            return {
                "method": "thread/compacted" if "thread/compacted" in events else "turn/completed",
                "params": {"turn": {"status": "completed"}},
            }

    monkeypatch.setattr(adapter, "CodexRPC", RPC)
    return row, calls


@pytest.fixture
def store(tmp_path, monkeypatch):
    store = MediaStore(tmp_path / "data")
    monkeypatch.setattr(adapter, "media", store)
    monkeypatch.setattr(adapter, "find_thread", lambda tid: {"id": tid, "cwd": str(tmp_path)})
    return store


@pytest.fixture
def client(store):
    client = TestClient(adapter.app)
    client.cookies.set("relay_session", adapter.SESSION)
    client.headers["X-Relay-Request"] = "1"
    return client
