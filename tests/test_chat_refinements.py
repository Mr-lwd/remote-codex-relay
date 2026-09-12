import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from server import app as adapter


@pytest.mark.parametrize("action", ["stop", "pause", "clear", "slash-pause", "slash-clear"])
def test_every_stop_control_interrupts_now_and_cancels_queue(relay, monkeypatch, action):
    row, calls = relay
    entered = asyncio.Event()
    interrupted = asyncio.Event()
    goal = {"objective": "保留原始目标", "status": "active"}
    original = adapter.CodexRPC

    class RPC(original):
        async def call(self, method, params):
            if method == "thread/goal/get":
                return {"goal": dict(goal) if goal else None}
            if method == "thread/goal/set":
                goal.update(params)
                return {"goal": dict(goal)}
            if method == "thread/goal/clear":
                goal.clear()
                return {}
            if method == "turn/interrupt":
                interrupted.set()
                return {}
            result = await super().call(method, params)
            if method == "turn/start":
                entered.set()
            return result

        async def wait_event(self, *args):
            await interrupted.wait()
            return {"params": {"turn": {"status": "interrupted"}}}

        async def close(self):
            # A late native goal update must not overwrite the user's pause.
            if getattr(self, "thread_id", None):
                goal["status"] = "complete"

    monkeypatch.setattr(adapter, "CodexRPC", RPC)

    async def run():
        jid = adapter.launch("long reply", row["cwd"], "t", "gpt-6-astra", "high")
        await entered.wait()
        await adapter.message("t", adapter.Message(text="/goal resume"))
        if action == "stop":
            await adapter.stop("t")
        elif action.startswith("slash-"):
            await adapter.message("t", adapter.Message(text="/goal " + action[6:]))
        else:
            await adapter.control_goal("t", adapter.GoalControl(action=action))
        assert interrupted.is_set()
        assert not adapter.tasks and not adapter.processes and "t" not in adapter.clients
        with adapter.db() as c:
            assert (
                c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0]
                == "interrupted"
            )
            assert c.execute("SELECT status FROM pending").fetchone()[0] == "cancelled"
        if action.endswith("clear"):
            assert not goal
        else:
            assert goal["objective"] == "保留原始目标" and goal["status"] == "paused"

    asyncio.run(run())


def test_stop_during_startup_prevents_any_turn(relay):
    row, calls = relay

    async def run():
        jid = adapter.launch("not started", row["cwd"], "t")
        await adapter.stop("t")
        await asyncio.sleep(0)
        assert not adapter.tasks and not adapter.processes
        with adapter.db() as c:
            assert (
                c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()[0]
                == "interrupted"
            )

    asyncio.run(run())
    assert not any(method == "turn/start" for method, _ in calls)


def test_recent_history_is_ten_combined_records_and_all_has_no_old_caps(relay, monkeypatch):
    row, _ = relay
    raw = [
        {
            "id": str(i),
            "role": "assistant",
            "text": f"message {i}",
            "at": datetime.fromtimestamp(i + 1, timezone.utc).isoformat(),
        }
        for i in range(350)
    ]
    monkeypatch.setattr(
        adapter,
        "read_rollout",
        lambda _: {
            "messages": raw,
            "status": "idle",
            "tokens": 0,
            "started": None,
            "activities": [],
        },
    )
    decorated = []

    def decorate(item, *a):
        decorated.append(item["id"])
        return dict(item)

    monkeypatch.setattr(adapter.media, "decorate", decorate)
    with adapter.db() as c:
        for i in range(205):
            c.execute(
                "INSERT INTO notes VALUES(?,?,?,?,?,?)",
                (f"n{i}", "t", "system", f"note {i}", 400 + i, "system"),
            )
        for i in range(105):
            c.execute(
                "INSERT INTO command_records VALUES(?,?,?,?,?,?,?,?)",
                (f"c{i}", "t", "help", "/help", "completed", "help", None, 700 + i),
            )
    client = TestClient(adapter.app)
    client.cookies.set("relay_session", adapter.SESSION)
    recent = client.get("/api/threads/t").json()
    assert recent["history"] == {"mode": "recent", "total": 660, "hasMore": True}
    assert len(recent["messages"]) + len(recent["notes"]) + len(recent["commandRecords"]) == 10
    assert recent["commandRecords"][0]["id"] == "c95"
    assert not decorated  # Older attachments are not imported for a recent-only response.
    all_history = client.get("/api/threads/t?history=all").json()
    assert (
        len(all_history["messages"]) == 350
        and len(all_history["notes"]) == 205
        and len(all_history["commandRecords"]) == 105
    )
    assert all_history["history"] == {"mode": "all", "total": 660, "hasMore": False}
    raw.append(
        {
            "id": "new",
            "role": "assistant",
            "text": "latest",
            "at": datetime.fromtimestamp(1000, timezone.utc).isoformat(),
        }
    )
    recent = client.get("/api/threads/t").json()
    assert recent["messages"][0]["id"] == "new" and len(recent["commandRecords"]) == 9
    assert TestClient(adapter.app).get("/api/threads/t?history=all").status_code == 401


def test_rollout_retains_messages_older_than_300(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "CODEX_HOME", tmp_path)
    adapter.cache.clear()
    path = tmp_path / "sessions" / "long.jsonl"
    path.parent.mkdir()
    path.write_text(
        "".join(
            json.dumps(
                {
                    "type": "event_msg",
                    "payload": {"type": "agent_message", "message": f"answer {i}"},
                }
            )
            + "\n"
            for i in range(350)
        )
    )
    history = adapter.read_rollout({"id": "long", "rollout_path": str(path)})
    assert len(history["messages"]) == 350 and history["messages"][0]["text"] == "answer 0"
