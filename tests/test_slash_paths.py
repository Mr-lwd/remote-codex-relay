"""Only recognized commands get command semantics; slash paths remain literal messages."""

import asyncio
import json

import pytest
from server import app as adapter
from test_commands import drain

PATH_TEXTS = [
    "/tmp/project/report.png",
    "/tmp/报告 文件.png 请查看这张图",
    "/goal/report.md",
    "/plan/project",
    "/status.json",
    "/src",
    "/",
    "//server/share/document.pdf",
    "/unknown 请处理",
    "/go",
    "/constructor",
]


@pytest.mark.parametrize("text", PATH_TEXTS)
def test_only_known_whole_command_names_are_parsed(text):
    assert adapter.parse_command(text) == (None, text)


@pytest.mark.parametrize("text", PATH_TEXTS)
def test_path_message_reaches_native_input_without_command_record(relay, text):
    row, calls = relay

    async def run():
        result = await adapter.message(
            "t", adapter.Message(text=text, model="gpt-6-astra", effort="high")
        )
        assert result["kind"] == "started"
        await drain()

    asyncio.run(run())
    turn = next(params for method, params in calls if method == "turn/start")
    assert turn["input"][0]["text"] == text
    assert turn["collaborationMode"]["mode"] == "default"
    with adapter.db() as db:
        assert db.execute("SELECT count(*) FROM command_records").fetchone()[0] == 0


def test_path_with_attachment_and_queue_keeps_literal_text(relay, store, monkeypatch):
    _, calls = relay
    monkeypatch.setattr(adapter, "snapshot", lambda *args: {"status": "running", "managed": True})
    asset = store.save(b"document", "reference.txt", tid="t")
    text = "/src 请参考附件"

    async def run():
        result = await adapter.message("t", adapter.Message(text=text, attachments=[asset["id"]]))
        assert result["kind"] == "queued"

    asyncio.run(run())
    with adapter.db() as db:
        pending = db.execute("SELECT text,options FROM pending").fetchone()
        options = json.loads(pending["options"])
        assert pending["text"] == text and options["attachments"] == [asset["id"]]
        assert "command" not in options and "commandRecord" not in options
    assert not calls


def test_new_task_accepts_path_with_attachment(relay, store, tmp_path, monkeypatch):
    from server import directories

    monkeypatch.setattr(directories, "WORKSPACE_ROOT", tmp_path)
    _, calls = relay
    asset = store.save(b"reference", "reference.txt")

    async def run():
        result = await adapter.new_task(
            adapter.NewTask(
                text="/plan/project 请查看附件", cwd=str(tmp_path), attachments=[asset["id"]]
            )
        )
        assert result["jobId"]
        await drain()

    asyncio.run(run())
    turn = next(params for method, params in calls if method == "turn/start")
    assert turn["input"][0]["text"] == "/plan/project 请查看附件"
    assert turn["collaborationMode"]["mode"] == "default"
    assert len(turn["input"]) == 2
