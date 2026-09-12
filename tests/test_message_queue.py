"""Cancellable FIFO messages must never interrupt or reconfigure the active turn."""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from server import app as adapter
from server.media import MediaStore
from test_media import png


def test_cancel_keeps_current_turn_and_dispatches_survivors_with_settings(relay, monkeypatch):
    row, calls = relay
    store = MediaStore(Path(row["cwd"]) / "media")
    monkeypatch.setattr(adapter, "media", store)
    image = store.save(png(), "reference.png", tid="t")
    document = store.save(b"reference", "notes.txt", tid="t")
    original = adapter.CodexRPC

    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()

        class RPC(original):
            async def call(self, method, params):
                result = await super().call(method, params)
                if method == "turn/start":
                    self.first = params["input"][0]["text"] == "first"
                    entered.set()
                return result

            async def wait_event(self, *args):
                if self.first:
                    await finish.wait()
                return await super().wait_event(*args)

        monkeypatch.setattr(adapter, "CodexRPC", RPC)
        jid = adapter.launch("first", row["cwd"], "t", "gpt-6-astra", "max")
        await asyncio.wait_for(entered.wait(), 3)
        dropped = await adapter.message("t", adapter.Message(text="mistake"))
        keep = await adapter.message(
            "t",
            adapter.Message(
                text="/plan review",
                attachments=[image["id"], document["id"]],
                model="gpt-5.6-sol",
                effort="high",
                permission="read-only",
            ),
        )
        goal = await adapter.message("t", adapter.Message(text="/goal accidental objective"))
        last = await adapter.message("t", adapter.Message(text="/tmp/文件", effort="low"))
        before = adapter.snapshot(row, True, history_limit=10)
        assert before["pendingCount"] == 4
        assert [q["text"] for q in before["pendingMessages"]] == [
            "mistake",
            "/plan review",
            "/goal accidental objective",
            "/tmp/文件",
        ]
        assert keep["pendingMessage"]["attachments"] == ["reference.png", "notes.txt"]
        await adapter.cancel_pending("t", dropped["queueId"])
        await adapter.cancel_pending("t", goal["queueId"])
        after = adapter.snapshot(row, True)
        assert [q["id"] for q in after["pendingMessages"]] == [keep["queueId"], last["queueId"]]
        assert after["status"] == "running" and after["managed"]
        assert not adapter.job_tasks[jid].done()
        assert (
            next(r for r in after["commandRecords"] if r["command"] == "goal")["status"]
            == "cancelled"
        )
        assert (
            next(n for n in after["notes"] if n["id"] == dropped["queueId"])["kind"] == "cancelled"
        )
        assert not any(m in ("turn/interrupt", "thread/goal/set") for m, _ in calls)
        finish.set()
        while adapter.tasks:
            await asyncio.gather(*list(adapter.tasks))
            await asyncio.sleep(0)
        assert adapter.snapshot(row, True)["pendingMessages"] == []

    asyncio.run(asyncio.wait_for(run(), 10))
    turns = [p for m, p in calls if m == "turn/start"]
    assert [p["input"][0]["text"] for p in turns] == ["first", "review", "/tmp/文件"]
    assert turns[1]["collaborationMode"]["mode"] == "plan"
    assert turns[1]["collaborationMode"]["settings"]["model"] == "gpt-5.6-sol"
    assert turns[1]["collaborationMode"]["settings"]["reasoning_effort"] == "high"
    assert turns[1]["input"][1:] == store.inputs([image["id"], document["id"]])
    assert [p for m, p in calls if m == "thread/resume"][1]["sandbox"] == "read-only"


@pytest.mark.parametrize("cancel_first", [True, False])
def test_cancellation_at_dispatch_boundary_is_truthful(relay, monkeypatch, cancel_first):
    row, calls = relay
    original = adapter.CodexRPC

    async def run():
        closing, finish_close = asyncio.Event(), asyncio.Event()

        class RPC(original):
            async def close(self):
                closing.set()
                await finish_close.wait()

        monkeypatch.setattr(adapter, "CodexRPC", RPC)
        adapter.launch("first", row["cwd"], "t")
        queued = await adapter.message("t", adapter.Message(text="second"))
        await asyncio.wait_for(closing.wait(), 3)
        if cancel_first:
            await adapter.cancel_pending("t", queued["queueId"])
        finish_close.set()
        while adapter.tasks:
            await asyncio.gather(*list(adapter.tasks))
            await asyncio.sleep(0)
        if not cancel_first:
            with pytest.raises(HTTPException) as error:
                await adapter.cancel_pending("t", queued["queueId"])
            assert error.value.status_code == 409
        assert len([m for m, _ in calls if m == "turn/start"]) == (1 if cancel_first else 2)

    asyncio.run(run())


def test_cancel_api_auth_scope_idempotency_and_persistence(relay):
    row, _ = relay
    queued = adapter.enqueue("t", "persisted", adapter.Message(text="persisted"), {})
    ident = queued["queueId"]
    path = f"/api/threads/t/queue/{ident}/cancel"
    client = TestClient(adapter.app)
    assert client.post(path, json={}).status_code == 401
    client.cookies.set("relay_session", adapter.SESSION)
    assert client.post(path, json={}).status_code == 403
    client.headers["X-Relay-Request"] = "1"
    assert client.get("/api/threads/t").json()["pendingMessages"][0]["id"] == ident
    assert client.post(f"/api/threads/other/queue/{ident}/cancel", json={}).status_code == 404
    for _ in range(2):
        assert client.post(path, json={}).status_code == 200
    assert client.get("/api/threads/t").json()["pendingCount"] == 0
    assert client.post("/api/threads/t/queue/missing/cancel", json={}).status_code == 404
    with adapter.db() as c:
        assert (
            c.execute("SELECT status FROM pending WHERE id=?", (ident,)).fetchone()[0]
            == "cancelled"
        )


def test_public_queue_redacts_secrets_and_does_not_expose_options(relay):
    row, _ = relay
    # Synthetic redaction fixture, not a credential.
    secret = "sk-" + "a" * 36
    queued = adapter.enqueue("t", secret, adapter.Message(text=secret), {"internal": "private"})
    detail = adapter.snapshot(row, True)
    assert secret not in json.dumps(queued)
    assert secret not in json.dumps(detail["pendingMessages"])
    assert "options" not in detail["pendingMessages"][0]


@pytest.mark.parametrize("action", ["cancel", "stop", "pause", "clear"])
def test_queue_and_goal_actions_are_isolated_between_existing_threads(relay, monkeypatch, action):
    row, _ = relay
    rows = {tid: {**row, "id": tid} for tid in ("a", "b")}
    monkeypatch.setattr(adapter, "find_thread", lambda tid: rows[tid])
    goals = {tid: {"objective": f"Goal {tid}", "status": "active"} for tid in rows}

    async def metadata(tid, method, params):
        if method == "thread/goal/clear":
            goals[tid] = None
        elif method == "thread/goal/set":
            goals[tid].update(params)
        return {"goal": goals[tid]}

    monkeypatch.setattr(adapter, "thread_metadata_call", metadata)
    items = {
        tid: adapter.enqueue(tid, f"Message {tid}", adapter.Message(text=f"Message {tid}"), {})
        for tid in rows
    }
    for tid in rows:
        assert [q["id"] for q in adapter.snapshot(rows[tid], True)["pendingMessages"]] == [
            items[tid]["queueId"]
        ]

    async def run():
        # Both sessions exist: an ID from B cannot be cancelled through A's endpoint.
        with pytest.raises(HTTPException) as error:
            await adapter.cancel_pending("a", items["b"]["queueId"])
        assert error.value.status_code == 404
        if action == "cancel":
            await adapter.cancel_pending("a", items["a"]["queueId"])
        elif action == "stop":
            await adapter.stop("a")
        else:
            await adapter.control_goal("a", adapter.GoalControl(action=action))

    asyncio.run(run())
    assert adapter.snapshot(rows["a"], True)["pendingMessages"] == []
    assert [q["id"] for q in adapter.snapshot(rows["b"], True)["pendingMessages"]] == [
        items["b"]["queueId"]
    ]
    assert goals["b"] == {"objective": "Goal b", "status": "active"}
    if action == "clear":
        assert goals["a"] is None
    elif action in ("pause", "stop"):
        assert goals["a"]["status"] == "paused"
