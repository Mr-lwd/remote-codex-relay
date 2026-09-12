import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from server import app as adapter
from server import directories
from test_commands import drain


def authenticated():
    client = TestClient(adapter.app)
    client.cookies.set(adapter.SESSION_COOKIE, adapter.SESSION)
    client.headers["X-Relay-Request"] = "1"
    return client


def test_real_directories_boundary_search_and_paging(tmp_path, monkeypatch):
    monkeypatch.setattr(directories, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(adapter, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(adapter, "rows", lambda: [])
    for name in ["项目10", "项目2", ".hidden", "space folder"]:
        (tmp_path / name).mkdir()
    (tmp_path / "file.pdf").write_text("document")
    (tmp_path / "escape").symlink_to("/etc", target_is_directory=True)
    (tmp_path / "alias").symlink_to(tmp_path / "项目2", target_is_directory=True)
    c = authenticated()
    data = c.get("/api/directories", params={"path": str(tmp_path)}).json()
    assert [e["name"] for e in data["entries"]] == ["alias", "space folder", "项目2", "项目10"]
    assert data["parent"] is None
    assert (
        c.get("/api/directories", params={"path": str(tmp_path), "q": "项目", "offset": 1}).json()[
            "entries"
        ][0]["name"]
        == "项目10"
    )
    assert any(
        e["name"] == ".hidden"
        for e in c.get("/api/directories", params={"path": str(tmp_path), "hidden": True}).json()[
            "entries"
        ]
    )
    nested = c.get("/api/directories", params={"path": str(tmp_path / "alias")}).json()
    assert nested["path"] == str(tmp_path / "项目2") and nested["parent"] == str(tmp_path)
    for path in [
        "/etc",
        str(tmp_path / "escape"),
        str(tmp_path / "missing"),
        str(tmp_path / "file.pdf"),
    ]:
        assert c.get("/api/directories", params={"path": path}).status_code == 400
    assert TestClient(adapter.app).get("/api/directories").status_code == 401
    assert (
        c.get("/api/directories", params={"path": str(tmp_path), "offset": -1}).status_code == 422
    )
    for i in range(105):
        (tmp_path / f"page{i}").mkdir()
    assert (
        len(
            c.get("/api/directories", params={"path": str(tmp_path), "q": "page"}).json()["entries"]
        )
        == 100
    )
    assert (
        len(
            c.get(
                "/api/directories", params={"path": str(tmp_path), "q": "page", "offset": 100}
            ).json()["entries"]
        )
        == 5
    )


@pytest.fixture
def native_goal(relay, monkeypatch):
    goal = {
        "objective": "原始目标",
        "status": "paused",
        "tokensUsed": 321,
        "timeUsedSeconds": 18,
        "tokenBudget": 5000,
    }
    calls = []

    async def metadata(tid, method, params):
        calls.append((method, params))
        if method == "thread/goal/get":
            return {"goal": dict(goal) if goal else None}
        if method == "thread/goal/set":
            goal.update(params)
            return {"goal": dict(goal)}
        if method == "thread/goal/clear":
            goal.clear()
            return {}

    monkeypatch.setattr(adapter, "thread_metadata_call", metadata)
    return goal, calls


def test_edit_preserves_status_budget_usage_and_rejects_stale_draft(relay, native_goal):
    goal, calls = native_goal
    c = authenticated()
    for status in ["paused", "active", "complete", "blocked"]:
        goal["status"] = status
        response = c.post(
            "/api/threads/t/goal",
            json={
                "action": "edit",
                "objective": "新目标\n" + status,
                "expectedObjective": goal["objective"],
            },
        )
        assert response.status_code == 200, response.text
        assert (
            goal["status"] == status and goal["tokenBudget"] == 5000 and goal["tokensUsed"] == 321
        )
        assert calls[-1] == ("thread/goal/set", {"objective": "新目标\n" + status})
    assert (
        c.post(
            "/api/threads/t/goal",
            json={"action": "edit", "objective": "other", "expectedObjective": "stale"},
        ).status_code
        == 409
    )
    assert (
        c.post(
            "/api/threads/t/goal",
            json={"action": "edit", "objective": "  ", "expectedObjective": goal["objective"]},
        ).status_code
        == 400
    )
    records = adapter.snapshot(relay[0], True)["commandRecords"]
    assert len(records) == 4 and all(r["execution_status"] == "completed" for r in records)


def test_pause_clear_cancel_all_queued_work(relay, native_goal):
    goal, calls = native_goal
    c = authenticated()
    with adapter.db() as db:
        for i, command in enumerate(["goal-resume", "goal-set", None]):
            db.execute(
                "INSERT INTO pending(id,thread_id,text,created,status,options) VALUES(?,?,?,?,?,?)",
                (str(i), "t", "text", i, "queued", json.dumps({"command": command})),
            )
    assert c.post("/api/threads/t/goal", json={"action": "pause"}).status_code == 200
    assert goal["status"] == "paused"
    with adapter.db() as db:
        assert [r["status"] for r in db.execute("SELECT status FROM pending ORDER BY created")] == [
            "cancelled",
            "cancelled",
            "cancelled",
        ]
    assert c.post("/api/threads/t/goal", json={"action": "clear"}).status_code == 200
    assert not goal
    assert (
        c.post("/api/threads/t/goal", json={"action": "edit", "objective": "new"}).status_code
        == 409
    )


def test_goal_auth_external_writer_and_native_failure(relay, native_goal, monkeypatch):
    assert (
        TestClient(adapter.app).post("/api/threads/t/goal", json={"action": "clear"}).status_code
        == 401
    )
    c = authenticated()
    c.headers.pop("X-Relay-Request")
    assert c.post("/api/threads/t/goal", json={"action": "clear"}).status_code == 403
    c = authenticated()
    monkeypatch.setattr(adapter, "writer_alive", lambda _: True)
    assert c.post("/api/threads/t/goal", json={"action": "pause"}).status_code == 409
    assert not native_goal[1]
    monkeypatch.setattr(adapter, "writer_alive", lambda _: False)

    async def fail(tid, method, params):
        if method == "thread/goal/get":
            return {"goal": native_goal[0]}
        raise adapter.HTTPException(409, "native failed")

    monkeypatch.setattr(adapter, "thread_metadata_call", fail)
    assert c.post("/api/threads/t/goal", json={"action": "clear"}).status_code == 409
    assert native_goal[0]
    assert adapter.snapshot(relay[0], True)["commandRecords"][-1]["execution_status"] == "failed"


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-astra"])
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_resume_button_uses_selected_model_effort_and_permission(relay, model, effort):
    async def run():
        result = await adapter.control_goal(
            "t",
            adapter.GoalControl(
                action="resume", model=model, effort=effort, permission="read-only"
            ),
        )
        assert result["kind"] == "started"
        await drain()

    asyncio.run(run())
    starts = [p for m, p in relay[1] if m == "turn/start"]
    assert starts[0]["collaborationMode"]["settings"]["model"] == model
    assert starts[0]["collaborationMode"]["settings"]["reasoning_effort"] == effort
    assert next(p for m, p in relay[1] if m == "thread/resume")["sandbox"] == "read-only"
