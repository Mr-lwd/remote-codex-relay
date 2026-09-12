import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import app as adapter


def test_both_codex_history_formats_and_partial_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "CODEX_HOME", tmp_path)
    adapter.cache.clear()
    folder = tmp_path / "sessions"
    folder.mkdir()
    path = folder / "history.jsonl"
    events = [
        {"type": "event_msg", "payload": {"type": "user_message", "message": "old user"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "old answer"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "UserMessage",
                    "id": "u",
                    "content": [{"type": "text", "text": "new user"}],
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "AgentMessage",
                    "id": "a",
                    "content": [{"type": "Text", "text": "new answer"}],
                    "phase": "final_answer",
                },
            },
        },
        {"type": "response_item", "payload": {"type": "reasoning", "summary": "private reasoning"}},
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"text": "private system instructions"}],
            },
        },
    ]
    path.write_text("".join(json.dumps(e) + "\n" for e in events) + '{"type":')
    row = {"id": "test-history", "rollout_path": str(path)}
    result = adapter.read_rollout(row)
    assert [m["text"] for m in result["messages"]] == [
        "old user",
        "old answer",
        "new user",
        "new answer",
    ]
    assert len(adapter.read_rollout(row)["messages"]) == 4
    with path.open("a") as f:
        f.write('"event_msg","payload":{"type":"agent_message","message":"next answer"}}\n')
    assert adapter.read_rollout(row)["messages"][-1]["text"] == "next answer"


def test_secrets_redacted_without_hiding_regular_text():
    assert adapter.redact("key sk-example_test_secret_12345678") == "key [密钥已隐藏]"
    assert adapter.redact("Authorization: Bearer abc.xyz.123") == "Authorization: Bearer [已隐藏]"
    assert adapter.redact("任务已完成") == "任务已完成"


def test_status_questions_do_not_consume_execution_requests():
    assert adapter.STATUS_RE.fullmatch("当前任务进度如何？")
    assert adapter.STATUS_RE.fullmatch("现在在做什么")
    assert not adapter.STATUS_RE.fullmatch("创建一个任务进度页面")
    assert not adapter.STATUS_RE.fullmatch("请修改当前任务的代码")


def test_effort_validation_and_queued_execution(tmp_path, monkeypatch):
    import asyncio
    import sqlite3
    import pytest
    from pydantic import ValidationError

    for effort in ("low", "medium", "high", "xhigh", "max"):
        assert adapter.NewTask(text="test", effort=effort).effort == effort
    with pytest.raises(ValidationError):
        adapter.Message(text="test", effort="ultra")

    def test_db():
        c = sqlite3.connect(tmp_path / "test.sqlite")
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(adapter, "db", test_db)
    monkeypatch.setattr(adapter, "DATA", tmp_path)
    with test_db() as c:
        c.executescript("""CREATE TABLE jobs(id TEXT,thread_id TEXT,title TEXT,cwd TEXT,status TEXT,pid INTEGER,created REAL,error TEXT,model TEXT,effort TEXT,options TEXT);
        CREATE TABLE pending(id TEXT,thread_id TEXT,text TEXT,created REAL,status TEXT,model TEXT,effort TEXT,options TEXT);""")
        c.execute(
            "INSERT INTO pending VALUES(?,?,?,?,?,?,?,?)",
            (
                "p",
                "t",
                "follow up",
                1,
                "queued",
                "gpt-5.6-sol",
                "low",
                json.dumps({"permission": "read-only", "collaboration": "plan"}),
            ),
        )
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
            if method == "thread/resume":
                return {"thread": {"id": "t"}, "model": params["model"]}
            if method == "turn/start":
                return {"turn": {"id": "turn"}}
            if method == "thread/goal/get":
                return {"goal": None}

        async def wait_event(self, *args):
            return {"params": {"turn": {"status": "completed"}}}

    monkeypatch.setattr(adapter, "CodexRPC", RPC)

    async def run():
        adapter.launch("first", str(tmp_path), "t", "gpt-6-astra", "max")
        while adapter.tasks:
            await asyncio.gather(*list(adapter.tasks))

    asyncio.run(run())
    starts = [params for method, params in calls if method == "turn/start"]
    assert len(starts) == 2
    assert starts[0]["collaborationMode"]["settings"]["model"] == "gpt-6-astra"
    assert starts[0]["collaborationMode"]["settings"]["reasoning_effort"] == "max"
    assert starts[1]["collaborationMode"]["settings"]["model"] == "gpt-5.6-sol"
    assert starts[1]["collaborationMode"]["settings"]["reasoning_effort"] == "low"
    assert starts[1]["collaborationMode"]["mode"] == "plan"
    resumes = [params for method, params in calls if method == "thread/resume"]
    assert resumes[1]["sandbox"] == "read-only" and resumes[1]["approvalPolicy"] == "never"
    with test_db() as c:
        assert [r["effort"] for r in c.execute("SELECT effort FROM jobs ORDER BY created")] == [
            "max",
            "low",
        ]
        assert c.execute("SELECT status FROM pending").fetchone()[0] == "submitted"


def test_commands_are_parsed_without_sending_slash_text():
    assert adapter.parse_command("/compact") == ("compact", "")
    assert adapter.parse_command("/goal 完成测试") == ("goal", "完成测试")
    assert adapter.parse_command("/plan 分析需求") == ("plan", "分析需求")
    assert adapter.parse_command("/plan\n分析需求") == ("plan", "分析需求")
    assert adapter.parse_command("ordinary input") == (None, "ordinary input")
    assert adapter.parse_command("/unsupported") == (None, "/unsupported")


def test_approval_requires_explicit_reply_and_rejects_duplicates(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    import pytest
    from server.codex_rpc import receive_request

    class Client:
        def __init__(self):
            self.inbox = {}
            self.items = {}
            self.sent = []

        async def write(self, value):
            self.sent.append(value)

    client = Client()
    monkeypatch.setattr(adapter, "find_thread", lambda tid: {"id": tid})
    monkeypatch.setattr(adapter, "note", lambda *args: None)
    monkeypatch.setattr(adapter, "clients", {"t": client})

    async def run():
        await receive_request(
            client,
            {
                "id": 12,
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "t",
                    "turnId": "turn",
                    "command": "echo harmless",
                    "itemId": "i",
                },
            },
        )
        assert not client.sent  # Receiving a request never grants approval.
        key = next(iter(client.inbox))
        with pytest.raises(HTTPException):
            await adapter.answer_request("other", key, adapter.InteractionReply(decision="accept"))
        await adapter.answer_request("t", key, adapter.InteractionReply(decision="decline"))
        assert client.sent == [{"id": 12, "result": {"decision": "decline"}}]
        with pytest.raises(HTTPException):
            await adapter.answer_request("t", key, adapter.InteractionReply(decision="accept"))
        await receive_request(
            client,
            {
                "id": "p",
                "method": "item/permissions/requestApproval",
                "params": {"threadId": "t", "permissions": {"network": {"enabled": True}}},
            },
        )
        key = next(iter(client.inbox))
        await adapter.answer_request("t", key, adapter.InteractionReply(decision="decline"))
        assert client.sent[-1] == {"id": "p", "result": {"permissions": {}, "scope": "turn"}}
        await receive_request(
            client,
            {
                "id": 13,
                "method": "item/tool/requestUserInput",
                "params": {
                    "threadId": "t",
                    "questions": [{"id": "q", "question": "Choose", "header": "Choice"}],
                },
            },
        )
        key = next(iter(client.inbox))
        with pytest.raises(HTTPException):
            await adapter.answer_request("t", key, adapter.InteractionReply(answers={}))
        await adapter.answer_request("t", key, adapter.InteractionReply(answers={"q": ["A"]}))
        assert client.sent[-1] == {"id": 13, "result": {"answers": {"q": {"answers": ["A"]}}}}

    asyncio.run(run())
