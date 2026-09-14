import asyncio
import json
import sqlite3

import pytest

from server.storage import initialize
from server.user_inputs import (
    DeliveryUncertain,
    InputManager,
    default_answers,
    display_reply,
    normalize_questions,
    observe_questions,
    pending_questions,
    reply_envelope,
)


def async_event(kind, **payload):
    return {
        "type": "response_item",
        "timestamp": "2026-01-01T00:00:00Z",
        "payload": {"type": kind, **payload},
    }


def question_call():
    return async_event(
        "function_call",
        name="request_user_input_async",
        call_id="call-question",
        arguments=json.dumps(
            {
                "questions": [
                    {"title": "选择方案", "options": ["A", "B（推荐）"]},
                    {"title": "补充说明"},
                ]
            }
        ),
    )


def request():
    return {
        "id": "input",
        "threadId": "thread",
        "type": "input",
        "source": "rpc",
        "questions": normalize_questions(
            [
                {"id": "choice", "question": "选择方案", "options": ["A", "B（推荐）"]},
                {"id": "text", "question": "补充说明"},
            ]
        ),
    }


@pytest.fixture
def manager(tmp_path):
    initialize(tmp_path)

    def connect():
        db = sqlite3.connect(tmp_path / "relay.sqlite")
        db.row_factory = sqlite3.Row
        return db

    sent = []

    async def deliver(req, answers, automatic):
        sent.append((req["id"], answers, automatic))

    now = [100.0]
    value = InputManager(connect, deliver, clock=lambda: now[0])
    return value, now, sent


def test_async_calls_need_acceptance_and_preserve_native_question_ids():
    state = {}
    observe_questions(state, question_call())
    assert pending_questions(state, "thread") == []
    observe_questions(
        state,
        async_event("function_call_output", call_id="call-question", output='{"accepted":true}'),
    )
    pending = pending_questions(state, "thread")
    assert len(pending) == 1
    questions = pending[0]["questions"]
    assert json.loads(questions[0]["id"]) == ["request_user_input_async", "call-question", 0]
    assert questions[0]["options"][1]["label"] == "B（推荐）"
    assert questions[1]["options"] == []
    answers = {q["id"]: ["用户回答 </send_user_message_question_reply>"] for q in questions}
    text = reply_envelope(pending[0], answers)
    assert text.count("</send_user_message_question_reply>") == 1
    assert "questionItemId" not in display_reply(text)
    assert "选择方案" in display_reply(text)
    observe_questions(
        state, async_event("message", role="user", content=[{"type": "input_text", "text": text}])
    )
    assert pending_questions(state, "thread") == []


def test_failed_tools_and_new_turns_do_not_create_stale_questions():
    state = {}
    observe_questions(state, question_call())
    observe_questions(
        state,
        async_event("function_call_output", call_id="call-question", output='{"accepted":false}'),
    )
    assert not pending_questions(state, "thread")
    observe_questions(
        state,
        async_event("function_call_output", call_id="call-question", output='{"accepted":true}'),
    )
    observe_questions(state, {"type": "event_msg", "payload": {"type": "task_started"}})
    assert not pending_questions(state, "thread")
    observe_questions(
        state, async_event("message", role="assistant", content=[{"text": "请选择 A 或 B"}])
    )
    assert not pending_questions(state, "thread")


def test_timeout_uses_latest_choice_marks_automatic_and_does_not_invent_text(manager):
    m, now, sent = manager
    current = m.register(request())
    assert current["deadline"] == 130
    m.draft("thread", "input", {"choice": ["A"]})

    async def run():
        now[0] = 129.9
        await m.tick()
        assert not sent
        now[0] = 130
        await m.tick()
        await m.tick()

    asyncio.run(run())
    assert len(sent) == 1 and sent[0][2]
    assert sent[0][1]["choice"] == ["（30 秒未提交，系统自动回复）A"]
    assert "用户未填写" in sent[0][1]["text"][0]
    assert "B（推荐）" in default_answers(request())["choice"][0]
    with m.connect() as db:
        row = dict(db.execute("SELECT * FROM input_replies").fetchone())
    assert row["status"] == "answered" and row["automatic"] == 1
    assert "draftAnswers" not in row and "answers" not in row


def test_reload_and_restart_keep_deadline_and_processed_state(manager):
    m, now, sent = manager
    m.register(request())
    now[0] = 110
    assert m.register(request())["deadline"] == 130
    fresh = InputManager(m.connect, m.deliver, clock=lambda: now[0])
    assert fresh.register(request())["deadline"] == 130
    now[0] = 131
    asyncio.run(fresh.tick())
    assert m.register(request()) is None
    asyncio.run(m.tick())
    assert len(sent) == 1


def test_manual_submit_and_timeout_race_send_once(manager):
    m, now, sent = manager
    m.register(request())
    now[0] = 130

    async def run():
        outcomes = await asyncio.gather(
            m.answer("thread", "input", {"choice": ["A"], "text": ["用户填写"]}),
            m.tick(),
            return_exceptions=True,
        )
        assert not any(isinstance(value, Exception) for value in outcomes)

    asyncio.run(run())
    assert len(sent) == 1 and not sent[0][2]


def test_invalid_cross_thread_and_duplicate_answers_are_rejected(manager):
    m, _, sent = manager
    m.register(request())

    async def run():
        for tid, answers in [("other", {}), ("thread", {}), ("thread", {"wrong": ["x"]})]:
            with pytest.raises(ValueError):
                await m.answer(tid, "input", answers)
        assert not sent
        await m.answer("thread", "input", {"choice": ["A"], "text": ["回答"]})
        with pytest.raises(ValueError):
            await m.answer("thread", "input", {"choice": ["B"], "text": ["重复"]})

    asyncio.run(run())
    assert len(sent) == 1


def test_failed_delivery_requires_manual_retry_and_uncertain_delivery_never_repeats(manager):
    m, now, sent = manager
    m.register(request())
    original = m.deliver
    attempts = []

    async def failed(*args):
        attempts.append(1)
        raise RuntimeError("offline")

    m.deliver = failed
    now[0] = 131

    async def run():
        await m.tick()
        await m.tick()
        assert len(attempts) == 1
        assert m.register(request())["replyStatus"] == "failed"
        m.deliver = original
        await m.answer("thread", "input", {"choice": ["A"], "text": ["重试"]})

    asyncio.run(run())
    assert len(sent) == 1
    second = {**request(), "id": "uncertain"}
    m.register(second)

    async def uncertain(*args):
        raise DeliveryUncertain("unknown")

    m.deliver = uncertain
    now[0] = 200
    asyncio.run(m.tick())
    assert m.register(second) is None


def test_expired_questions_and_manual_only_requests_never_auto_submit(manager):
    m, now, sent = manager
    m.register(request())
    m.expire("thread", "input")
    assert m.register(request()) is None
    approval = {**request(), "id": "manual-only"}
    assert m.register(approval, automatic=False)["deadline"] is None
    now[0] = 10000
    asyncio.run(m.tick())
    assert not sent


def test_async_reply_is_delivered_to_active_native_queue(manager, monkeypatch):
    from server import app as adapter

    m, _, sent = manager
    state = {}
    observe_questions(state, question_call())
    observe_questions(
        state,
        async_event("function_call_output", call_id="call-question", output='{"accepted":true}'),
    )
    pending = pending_questions(state, "thread")[0]
    m.register(pending)
    m.deliver = adapter.deliver_input
    monkeypatch.setattr(adapter, "input_manager", m)
    monkeypatch.setattr(adapter, "find_thread", lambda tid: {"id": tid, "cwd": "/tmp/questions"})
    monkeypatch.setattr(adapter, "read_rollout", lambda row: state)
    monkeypatch.setattr(adapter, "writer_alive", lambda tid: True)
    monkeypatch.setattr(adapter, "clients", {})
    monkeypatch.setattr(adapter, "note", lambda *args: None)
    commands = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"accepted", b""

    async def spawn(*args, **kwargs):
        commands.append(args)
        return Process()

    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", spawn)
    answers = {q["id"]: ["选项 A"] for q in pending["questions"]}
    asyncio.run(
        adapter.answer_request("thread", pending["id"], adapter.InteractionReply(answers=answers))
    )
    assert len(commands) == 1
    assert commands[0][1:4] == ("queue", "--thread", "thread")
    body = commands[0][-1]
    assert "questionItemId" in body and "选项 A" in body
    assert not sent


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("failure", [None, "rejected", "disconnected", "wrong-turn"])
def test_managed_question_reaches_owned_turn_without_cli_queue(
    manager, monkeypatch, automatic, failure
):
    from server import app as adapter
    from server.codex_rpc import RPCRejected

    m, now, _ = manager
    state = {}
    observe_questions(state, question_call())
    observe_questions(
        state,
        async_event("function_call_output", call_id="call-question", output='{"accepted":true}'),
    )
    question = pending_questions(state, "thread")[0]
    m.register(question)
    m.deliver = adapter.deliver_input
    calls = []

    class Client:
        turn_id = "active-turn"

        async def call(self, method, params, *, timeout):
            calls.append((method, params, timeout))
            if failure == "rejected":
                raise RPCRejected("Turn already completed")
            if failure == "disconnected":
                raise RuntimeError("Connection closed after write")
            return {"turnId": "unexpected" if failure == "wrong-turn" else self.turn_id}

    async def forbidden(*args, **kwargs):
        pytest.fail("A managed question must not spawn a CLI queue process")

    monkeypatch.setattr(adapter, "clients", {"thread": Client()})
    monkeypatch.setattr(adapter, "find_thread", lambda tid: {"id": tid})
    monkeypatch.setattr(adapter, "read_rollout", lambda row: state)
    monkeypatch.setattr(adapter, "note", lambda *args: None)
    monkeypatch.setattr(adapter.asyncio, "create_subprocess_exec", forbidden)
    answers = {q["id"]: ["用户已选方案"] for q in question["questions"]}
    m.draft("thread", question["id"], answers)
    now[0] = 131

    async def run():
        if automatic:
            await m.tick()
            await m.tick()
        elif failure:
            with pytest.raises(RuntimeError):
                await m.answer("thread", question["id"], answers)
        else:
            await m.answer("thread", question["id"], answers)

    asyncio.run(run())
    assert len(calls) == 1
    method, params, timeout = calls[0]
    assert method == "turn/steer" and timeout == 25
    assert params["threadId"] == "thread" and params["expectedTurnId"] == "active-turn"
    assert "用户已选方案" in params["input"][0]["text"]
    assert ("系统自动回复" in params["input"][0]["text"]) == automatic
    with m.connect() as db:
        record = db.execute("SELECT status,deadline FROM input_replies").fetchone()
    assert record["status"] == (
        "answered" if failure is None else "failed" if failure == "rejected" else "uncertain"
    )
    assert record["deadline"] is None


def test_timeout_can_continue_finished_turn_without_replaying_command_or_attachments(
    manager, monkeypatch
):
    from server import app as adapter

    m, now, _ = manager
    state = {}
    observe_questions(state, question_call())
    observe_questions(
        state,
        async_event("function_call_output", call_id="call-question", output='{"accepted":true}'),
    )
    question = pending_questions(state, "thread")[0]
    m.register(question)
    m.deliver = adapter.deliver_input
    monkeypatch.setattr(adapter, "db", m.connect)
    monkeypatch.setattr(adapter, "find_thread", lambda tid: {"id": tid, "cwd": "/tmp/questions"})
    monkeypatch.setattr(adapter, "read_rollout", lambda row: state)
    monkeypatch.setattr(adapter, "writer_alive", lambda tid: False)
    monkeypatch.setattr(adapter, "clients", {})
    monkeypatch.setattr(adapter, "note", lambda *args: None)
    with m.connect() as db:
        db.execute(
            "INSERT INTO jobs(id,thread_id,created,model,effort,options) VALUES(?,?,?,?,?,?)",
            (
                "job",
                "thread",
                1,
                "gpt-6-astra",
                "high",
                json.dumps(
                    {
                        "permission": "read-only",
                        "collaboration": "plan",
                        "command": "compact",
                        "attachments": ["old"],
                    }
                ),
            ),
        )
    launched = []
    monkeypatch.setattr(adapter, "launch", lambda *args: launched.append(args))
    now[0] = 131
    asyncio.run(m.tick())
    assert len(launched) == 1
    assert launched[0][3:] == (
        "gpt-6-astra",
        "high",
        {"permission": "read-only", "collaboration": "plan"},
    )
    assert "系统自动回复" in launched[0][0]


def test_slow_question_does_not_hold_other_deadline_or_drop_its_draft(manager):
    m, now, _ = manager
    m.register(request())
    m.draft("thread", "input", {"choice": ["A"]})
    m.register({**request(), "id": "second"})
    now[0] = 131

    async def run():
        first_started, release, second_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def deliver(req, answers, automatic):
            if req["id"] == "input":
                first_started.set()
                await release.wait()
            else:
                second_done.set()

        m.deliver = deliver
        task = asyncio.create_task(m.tick())
        await asyncio.wait_for(first_started.wait(), 1)
        await asyncio.wait_for(second_done.wait(), 1)
        visible = m.register(request())
        assert visible["replyStatus"] == "sending"
        assert visible["draftAnswers"]["choice"] == ["A"]
        release.set()
        await task

    asyncio.run(run())


def test_public_draft_restores_choices_but_never_returns_secret_values():
    from server import app as adapter

    value = request()
    value["questions"][1]["isSecret"] = True
    value["draftAnswers"] = {"choice": ["A"], "text": ["private-input-value"]}
    public = adapter.public_request(value)
    assert public["savedAnswers"] == {"choice": ["A"]}
    assert "private-input-value" not in json.dumps(public)
