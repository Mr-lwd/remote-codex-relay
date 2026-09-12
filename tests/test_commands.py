"""Slash command records survive refresh and follow the actual native job lifecycle."""

import asyncio
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import app as adapter


async def drain():
    while adapter.tasks:
        await asyncio.gather(*list(adapter.tasks))


def test_local_commands_persist_without_model_turns(relay):
    row, calls = relay

    async def run():
        for command in ("/plan", "/default", "/model", "/permissions", "/status", "/help"):
            result = await adapter.message("t", adapter.Message(text=command))
            assert result["kind"] == "command"

    asyncio.run(run())
    records = adapter.snapshot(row, True)["commandRecords"]
    assert len(records) == 6 and all(
        r["execution_status"] == "completed" and r["message"] for r in records
    )
    assert not calls


def test_compact_and_plan_lifecycle(relay):
    row, calls = relay

    async def run():
        await adapter.message("t", adapter.Message(text="/compact"))
        active = adapter.snapshot(row, True)
        assert active["activeOperation"]["command"] == "compact"
        assert active["commandRecords"][-1]["execution_status"] == "starting"
        await drain()
        record = adapter.snapshot(row, True)["commandRecords"][-1]
        assert record["execution_status"] == "completed" and "已压缩" in record["message"]
        assert not any(method == "turn/start" for method, _ in calls)
        await adapter.message("t", adapter.Message(text="/plan 分析需求"))
        assert adapter.snapshot(row, True)["activeOperation"]["collaboration"] == "plan"
        await drain()

    asyncio.run(run())
    starts = [p for m, p in calls if m == "turn/start"]
    assert starts[0]["input"][0]["text"] == "分析需求"
    assert starts[0]["collaborationMode"]["mode"] == "plan"
    assert adapter.snapshot(row, True)["commandRecords"][-1]["input"] == "/plan 分析需求"


def test_queue_failure_and_live_goal_operation(relay, monkeypatch):
    row, calls = relay
    original_snapshot = adapter.snapshot
    monkeypatch.setattr(adapter, "snapshot", lambda *a: {"status": "running", "managed": True})

    async def run():
        result = await adapter.message("t", adapter.Message(text="/compact"))
        assert result["kind"] == "queued"
        with adapter.db() as c:
            pending = dict(c.execute("SELECT * FROM pending").fetchone())
        records = original_snapshot(row, True)["commandRecords"]
        assert records[-1]["execution_status"] == "queued"
        adapter.launch(pending["text"], row["cwd"], "t", options=json.loads(pending["options"]))
        with adapter.db() as c:
            c.execute("UPDATE pending SET status='submitted'")
        await drain()
        assert original_snapshot(row, True)["commandRecords"][-1]["execution_status"] == "completed"
        adapter.clients["t"] = adapter.CodexRPC()
        adapter.clients["t"].goal = {"status": "active", "objective": "测试目标"}
        result = await adapter.message("t", adapter.Message(text="/goal pause"))
        assert result["kind"] == "command" and "暂停" in result["message"]
        assert original_snapshot(row, True)["commandRecords"][-1]["execution_status"] == "completed"
        with pytest.raises(adapter.HTTPException):
            await adapter.message("t", adapter.Message(text="/compact invalid"))
        assert original_snapshot(row, True)["commandRecords"][-1]["execution_status"] == "failed"

    asyncio.run(run())
    assert any(m == "thread/goal/set" and p["status"] == "paused" for m, p in calls)


def test_native_failure_is_visible_in_command_history(relay, monkeypatch):
    row, _ = relay
    original = adapter.CodexRPC

    class FailingRPC(original):
        async def call(self, method, params):
            if method == "thread/compact/start":
                raise RuntimeError("压缩服务暂不可用")
            return await super().call(method, params)

    monkeypatch.setattr(adapter, "CodexRPC", FailingRPC)

    async def run():
        await adapter.message("t", adapter.Message(text="/compact"))
        await drain()

    asyncio.run(run())
    result = adapter.snapshot(row, True)
    assert result["commandRecords"][-1]["execution_status"] == "failed"
    assert result["commandRecords"][-1]["error"] == "压缩服务暂不可用"
    assert result["activeOperation"] is None


@pytest.mark.parametrize("command", ["/goal 新目标", "/goal resume"])
@pytest.mark.parametrize("model,effort", [("gpt-6-astra", "max"), ("gpt-5.6-sol", "low")])
def test_goal_queue_keeps_selected_settings_for_every_turn(
    relay, monkeypatch, command, model, effort
):
    row, calls = relay
    original = adapter.CodexRPC
    entered = asyncio.Event()
    release = asyncio.Event()

    class GoalRPC(original):
        async def call(self, method, params):
            if method == "thread/goal/get":
                calls.append((method, params))
                turns = sum(m == "turn/start" for m, _ in calls)
                return {"goal": {"status": "active" if turns == 2 else "completed"}}
            return await super().call(method, params)

        async def wait_event(self, events, *args):
            if sum(m == "turn/start" for m, _ in calls) == 1:
                entered.set()
                await release.wait()
            return await super().wait_event(events, *args)

    monkeypatch.setattr(adapter, "CodexRPC", GoalRPC)
    old_model = "gpt-5.6-sol" if model == "gpt-6-astra" else "gpt-6-astra"

    async def run():
        adapter.launch("第一轮", row["cwd"], "t", old_model, "medium")
        await entered.wait()
        result = await adapter.message(
            "t", adapter.Message(text=command, model=model, effort=effort, permission="read-only")
        )
        assert result["kind"] == "queued"
        assert not any(m == "thread/goal/set" for m, _ in calls), (
            "New goal must not mutate the old running job"
        )
        with adapter.db() as c:
            queued = dict(c.execute("SELECT * FROM pending").fetchone())
        assert (queued["model"], queued["effort"]) == (model, effort)
        release.set()
        await drain()

    asyncio.run(run())
    starts = [p for m, p in calls if m == "turn/start"]
    assert len(starts) == 3
    assert starts[0]["collaborationMode"]["settings"]["model"] == old_model
    for turn in starts[1:]:
        assert turn["collaborationMode"]["settings"]["model"] == model
        assert turn["collaborationMode"]["settings"]["reasoning_effort"] == effort
    resumes = [p for m, p in calls if m == "thread/resume"]
    assert resumes[-1]["model"] == model and resumes[-1]["sandbox"] == "read-only"
    assert resumes[-1]["config"]["model_reasoning_effort"] == effort
    with adapter.db() as c:
        assert tuple(
            c.execute("SELECT model,effort FROM jobs ORDER BY created DESC LIMIT 1").fetchone()
        ) == (model, effort)


@pytest.mark.parametrize(
    "command", ["/goal 新目标", "/plan 分析需求", "/default 执行需求", "/compact"]
)
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_native_commands_configure_thread_and_turn(relay, command, effort):
    row, calls = relay

    async def run():
        await adapter.message(
            "t", adapter.Message(text=command, model="gpt-6-astra", effort=effort)
        )
        current = adapter.snapshot({**row, "model": "gpt-5.6-sol"}, True)
        assert current["model"] == "gpt-6-astra"
        assert current["reasoningEffort"] == effort
        await drain()

    asyncio.run(run())
    resume = next(p for m, p in calls if m == "thread/resume")
    assert resume["model"] == "gpt-6-astra"
    assert resume["config"]["model_reasoning_effort"] == effort
    for method, params in calls:
        if method == "turn/start":
            assert params["collaborationMode"]["settings"]["model"] == "gpt-6-astra"
            assert params["collaborationMode"]["settings"]["reasoning_effort"] == effort
