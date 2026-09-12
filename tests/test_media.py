import asyncio
import base64
import io
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from server import app as adapter
from server.media import MediaStore, MAX_BYTES


def png(color="red"):
    data = io.BytesIO()
    Image.new("RGB", (72, 48), color).save(data, "PNG")
    return data.getvalue()


def test_upload_requires_login_and_origin_and_download_preserves_original(client, store):
    raw = png()
    unauth = TestClient(adapter.app)
    assert unauth.post("/api/media", content=raw).status_code == 401
    no_origin = TestClient(adapter.app)
    no_origin.cookies.set(adapter.SESSION_COOKIE, adapter.SESSION)
    assert no_origin.post("/api/media", content=raw).status_code == 403
    response = client.post("/api/media?name=photo.png&thread=t", content=raw)
    assert response.status_code == 200, response.text
    asset = response.json()
    assert (asset["width"], asset["height"]) == (72, 48)
    assert unauth.get(asset["url"]).status_code == 401
    assert client.get(asset["url"]).content == raw
    thumb = client.get(asset["thumbnailUrl"])
    assert thumb.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(thumb.content)).size == (72, 48)
    download = client.get(asset["downloadUrl"])
    assert download.content == raw and "attachment;" in download.headers["content-disposition"]
    assert "nosniff" == download.headers["x-content-type-options"]
    assert client.get("/api/media/not-an-id").status_code == 404
    assert store.path(store.get(asset["id"])).stat().st_mode & 0o777 == 0o600


def test_invalid_oversized_and_unsupported_uploads(client, store):
    assert (
        client.post("/api/media?name=fake.png", content=b"<script>alert(1)</script>").status_code
        == 400
    )
    assert client.post("/api/media?name=x.svg", content=b"<svg></svg>").status_code == 400
    assert client.post("/api/media", content=b"x" * (MAX_BYTES + 1)).status_code == 413
    assert client.post("/api/media", content=png()[:30]).status_code == 400
    # Header-only oversized dimensions must be rejected before decoding pixel data.
    original = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = 100
        with pytest.raises(HTTPException):
            store.save(png())
    finally:
        Image.MAX_IMAGE_PIXELS = original


def test_binding_validation_and_cross_thread_rejection(store):
    asset = store.save(png(), tid="t")
    assert store.validate([asset["id"]], "t") == [asset["id"]]
    with pytest.raises(HTTPException):
        store.validate([asset["id"]], "other")
    with pytest.raises(HTTPException):
        store.validate([asset["id"]] * 2, "t")
    with pytest.raises(HTTPException):
        store.validate(["a"] * 7, "t")
    returned = store.save(png(), tid="other", source="returned")
    with pytest.raises(HTTPException):
        store.validate([returned["id"]], "other")
    unbound = store.save(png())
    store.bind([unbound["id"]], "t")
    assert store.get(unbound["id"])["thread_id"] == "t"


def test_returned_local_images_and_structured_history_are_durable(tmp_path, store, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    path = workspace / "design with spaces.png"
    path.write_bytes(png())
    row = {"id": "t", "cwd": str(workspace)}
    message = {"text": f"结果：![设计](<{path}>)，下载：[原图](<{path}>)"}
    decorated = store.decorate(message, row)
    assert len(decorated["images"]) == 1
    asset = decorated["images"][0]
    assert asset["url"] in decorated["text"] and str(path) not in decorated["text"]
    path.unlink()
    reopened = MediaStore(tmp_path / "data")
    assert reopened.path(store.get(asset["id"])).read_bytes() == png()
    assert reopened.decorate(message, row)["images"][0]["id"] == asset["id"]
    outside = tmp_path / "private.png"
    outside.write_bytes(png())
    (workspace / "escape.png").symlink_to(outside)
    for ref in [
        str(outside),
        "../private.png",
        "escape.png",
        "http://127.0.0.1/x.png",
        "file:///etc/passwd",
    ]:
        assert store.import_reference(ref, row) is None
    monkeypatch.setattr(adapter, "CODEX_HOME", tmp_path)
    folder = tmp_path / "sessions"
    folder.mkdir()
    rollout = folder / "t.jsonl"
    row["rollout_path"] = str(rollout)
    data = "data:image/png;base64," + base64.b64encode(png("blue")).decode()
    events = [
        {"type": "event_msg", "payload": {"type": "user_message", "message": "", "images": [data]}},
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "output": [{"type": "input_image", "image_url": data}],
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "ImageGeneration",
                    "id": "gen",
                    "result": base64.b64encode(png("green")).decode(),
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "McpToolCall",
                    "id": "tool",
                    "result": {
                        "content": [
                            {
                                "type": "image",
                                "mimeType": "image/png",
                                "data": base64.b64encode(png()).decode(),
                            }
                        ]
                    },
                },
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_image", "image_url": data}],
            },
        },
    ]
    rollout.write_text("".join(json.dumps(e) + "\n" for e in events))
    adapter.cache.clear()
    messages = adapter.read_rollout(row)["messages"]
    assert len(messages) == 4
    assert all(store.decorate(m, row)["images"] for m in messages)


def test_image_only_submission_and_external_session_rejection(client, store, monkeypatch):
    calls = []
    monkeypatch.setattr(adapter, "launch", lambda *a, **kw: calls.append((a, kw)) or "j")
    monkeypatch.setattr(adapter, "note", lambda *a, **kw: None)
    monkeypatch.setattr(adapter, "snapshot", lambda *a: {"status": "idle", "managed": False})
    monkeypatch.setattr(adapter, "writer_alive", lambda *a: False)
    asset = store.save(png(), tid="t")
    response = client.post("/api/threads/t/messages", json={"attachments": [asset["id"]]})
    assert response.status_code == 200, response.text
    assert calls[0][0][0] == "请分析这些图片。"
    assert calls[0][0][-1]["attachments"] == [asset["id"]]
    assert client.post("/api/threads/t/messages", json={"text": ""}).status_code == 400
    assert (
        client.post(
            "/api/threads/t/messages", json={"text": "/compact", "attachments": [asset["id"]]}
        ).status_code
        == 400
    )
    monkeypatch.setattr(adapter, "writer_alive", lambda *a: True)
    assert (
        client.post(
            "/api/threads/t/messages", json={"text": "看看", "attachments": [asset["id"]]}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/threads/t/messages",
            json={"text": "状态", "mode": "status", "attachments": [asset["id"]]},
        ).status_code
        == 400
    )
    assert len(calls) == 1


@pytest.mark.parametrize("document", [False, True])
def test_queue_and_native_input_preserve_images(tmp_path, store, monkeypatch, document):
    import sqlite3

    def db():
        c = sqlite3.connect(tmp_path / "jobs.sqlite")
        c.row_factory = sqlite3.Row
        return c

    with db() as c:
        c.executescript("""CREATE TABLE jobs(id TEXT,thread_id TEXT,title TEXT,cwd TEXT,status TEXT,pid INTEGER,created REAL,error TEXT,model TEXT,effort TEXT,options TEXT);
        CREATE TABLE pending(id TEXT,thread_id TEXT,text TEXT,created REAL,status TEXT,model TEXT,effort TEXT,options TEXT);""")
    monkeypatch.setattr(adapter, "db", db)
    monkeypatch.setattr(adapter, "note", lambda *a, **kw: None)
    monkeypatch.setattr(adapter, "snapshot", lambda *a: {"status": "running", "managed": True})
    monkeypatch.setattr(adapter, "writer_alive", lambda *a: True)
    asset = (
        store.save(b"queued document", "queued.txt", tid="t")
        if document
        else store.save(png(), tid="t")
    )
    asyncio.run(adapter.message("t", adapter.Message(text="排队看图", attachments=[asset["id"]])))
    with db() as c:
        row = c.execute("SELECT * FROM pending").fetchone()
        assert json.loads(row["options"])["attachments"] == [asset["id"]]
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
                return {"thread": {"id": "t"}, "model": "gpt-5.6-sol"}
            if method == "turn/start":
                return {"turn": {"id": "turn"}}
            if method == "thread/goal/get":
                return {"goal": None}

        async def wait_event(self, *args):
            return {"params": {"turn": {"status": "completed"}}}

    monkeypatch.setattr(adapter, "CodexRPC", RPC)

    async def run():
        adapter.launch("first", str(tmp_path), "t")
        while adapter.tasks:
            await asyncio.gather(*list(adapter.tasks))

    asyncio.run(run())
    starts = [v for k, v in calls if k == "turn/start"]
    assert len(starts) == 2 and len(starts[0]["input"]) == 1
    if document:
        assert starts[1]["input"][1]["type"] == "text"
        assert str(store.path(store.get(asset["id"]))) in starts[1]["input"][1]["text"]
    else:
        assert starts[1]["input"][1] == {
            "type": "localImage",
            "path": str(store.path(store.get(asset["id"]))),
        }
