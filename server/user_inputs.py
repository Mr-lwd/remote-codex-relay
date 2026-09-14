"""Structured question discovery and one-shot, server-side answer deadlines."""

import asyncio
import hashlib
import json
import logging
import re
import time


TIMEOUT_SECONDS = 30
REPLY_TAG = "send_user_message_question_reply"


def normalize_questions(questions, call_id=None):
    result = []
    if not isinstance(questions, list):
        return result
    for index, question in enumerate(questions[:10]):
        if not isinstance(question, dict):
            continue
        title = question.get("question") or question.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        ident = (
            json.dumps(["request_user_input_async", call_id, index], separators=(",", ":"))
            if call_id is not None
            else question.get("id")
        )
        if not isinstance(ident, str) or not ident:
            continue
        options = []
        for option in question.get("options") or []:
            option = {"label": option} if isinstance(option, str) else option
            if isinstance(option, dict) and isinstance(option.get("label"), str):
                options.append(
                    {
                        "label": option["label"],
                        "description": str(option.get("description") or ""),
                    }
                )
        result.append(
            {
                "id": ident,
                "question": title,
                "header": question.get("header") or title,
                "isSecret": bool(question.get("isSecret")),
                "options": options,
            }
        )
    return result


def observe_questions(state, event):
    """Read only explicit tool calls and reply envelopes, never prose guesses."""
    calls = state.setdefault("inputCalls", {})
    payload = event.get("payload", {})
    kind = payload.get("type")
    if event.get("type") == "event_msg" and kind == "task_started":
        calls.clear()
    if event.get("type") == "response_item":
        name = str(payload.get("name", "")).rsplit(".", 1)[-1]
        if kind == "function_call" and name == "request_user_input_async":
            ident = payload.get("call_id")
            try:
                args = payload.get("arguments", {})
                args = json.loads(args) if isinstance(args, str) else args
                questions = normalize_questions(args.get("questions"), ident)
            except (ValueError, TypeError, AttributeError):
                return
            if isinstance(ident, str) and questions:
                calls[ident] = {
                    "id": "async-" + hashlib.sha256(ident.encode()).hexdigest()[:32],
                    "type": "input",
                    "source": "async",
                    "callId": ident,
                    "questions": questions,
                    "accepted": False,
                    "createdAt": event.get("timestamp"),
                }
        elif kind == "function_call_output" and payload.get("call_id") in calls:
            try:
                value = payload.get("output")
                value = json.loads(value) if isinstance(value, str) else value
            except (ValueError, TypeError):
                value = None
            call = calls[payload["call_id"]]
            call["accepted"] = isinstance(value, dict) and value.get("accepted") is True

    texts = []
    if kind == "user_message":
        texts.append(payload.get("message", ""))
    elif kind == "message" and payload.get("role") == "user":
        texts.extend(part.get("text", "") for part in payload.get("content", []))
    elif kind == "item_completed":
        item = payload.get("item", {})
        if str(item.get("type", "")).lower() == "usermessage":
            texts.append(item.get("text", ""))
            texts.extend(part.get("text", "") for part in item.get("content", []))
    for text in texts:
        if not isinstance(text, str):
            continue
        for match in re.finditer(rf"<{REPLY_TAG}>\s*(.*?)\s*</{REPLY_TAG}>", text, re.S):
            try:
                answers = json.loads(match[1])
                ids = {a["questionItemId"] for a in answers if isinstance(a, dict)}
            except (ValueError, TypeError, KeyError):
                continue
            for call in calls.values():
                call["questions"] = [q for q in call["questions"] if q["id"] not in ids]


def pending_questions(state, tid):
    return [
        {**call, "threadId": tid}
        for call in state.get("inputCalls", {}).values()
        if call["accepted"] and call["questions"]
    ]


def default_answers(request, draft=None):
    draft = draft or {}
    answers = {}
    for question in request["questions"]:
        existing = draft.get(question["id"], [])
        options = question.get("options") or []
        chosen = next(
            (o for o in options if re.search(r"推荐|recommended", o["label"], re.I)),
            options[0] if options else None,
        )
        value = next((value for value in existing if value.strip()), None)
        if value is None and chosen:
            value = chosen["label"]
        answers[question["id"]] = [
            "（30 秒未提交，系统自动回复）" + (value or "用户未填写，请勿视为已确认的信息。")
        ]
    return answers


def validate_answers(request, answers, *, partial=False):
    expected = {question["id"] for question in request["questions"]}
    if not isinstance(answers, dict) or (
        set(answers) - expected if partial else set(answers) != expected
    ):
        raise ValueError("请回答每个问题")
    for values in answers.values():
        if (
            not isinstance(values, list)
            or not all(isinstance(value, str) for value in values)
            or sum(len(value) for value in values) > 10000
            or (not partial and not any(value.strip() for value in values))
        ):
            raise ValueError("回答为空或过长")


def reply_envelope(request, answers):
    items = [
        {
            "questionItemId": q["id"],
            "question": q["question"],
            "answer": "\n".join(answers[q["id"]]),
        }
        for q in request["questions"]
    ]
    encoded = json.dumps(items, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    return f"<{REPLY_TAG}>\n{encoded}\n</{REPLY_TAG}>"


def display_reply(text):
    def replace(match):
        try:
            items = json.loads(match[1])
            if not isinstance(items, list) or not all(
                isinstance(item, dict)
                and isinstance(item.get("question"), str)
                and isinstance(item.get("answer"), str)
                for item in items
            ):
                return match[0]
            return "\n\n".join(item["question"] + "\n" + item["answer"] for item in items)
        except (ValueError, TypeError):
            return match[0]

    return re.sub(rf"<{REPLY_TAG}>\s*(.*?)\s*</{REPLY_TAG}>", replace, text, flags=re.S)


class DeliveryUncertain(RuntimeError):
    """The transport may have accepted the answer; do not retry automatically."""


class InputManager:
    def __init__(self, connect, deliver, *, clock=time.time):
        self.connect = connect
        self.deliver = deliver
        self.clock = clock
        self.pending = {}
        self.locks = {}

    def register(self, request, *, automatic=True):
        key = (request["threadId"], request["id"])
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO input_replies(thread_id,id,status,deadline) VALUES(?,?,'pending',?)",
                (*key, self.clock() + TIMEOUT_SECONDS if automatic else None),
            )
            record = db.execute(
                "SELECT * FROM input_replies WHERE thread_id=? AND id=?", key
            ).fetchone()
        if record["status"] == "sending" and key in self.pending:
            current = self.pending[key]
            current.update(replyStatus="sending", deadline=None)
            return current
        if record["status"] not in ("pending", "failed"):
            self.pending.pop(key, None)
            return None
        previous = self.pending.get(key, {})
        current = {**request, "deadline": record["deadline"], "replyStatus": record["status"]}
        current["draftAnswers"] = previous.get("draftAnswers", {})
        self.pending[key] = current
        return current

    def draft(self, tid, ident, answers):
        request = self.pending.get((tid, ident))
        if not request or request["replyStatus"] != "pending":
            raise ValueError("该问题已提交或已失效")
        validate_answers(request, answers, partial=True)
        request["draftAnswers"] = answers

    def expire(self, tid, ident):
        self.pending.pop((tid, ident), None)
        with self.connect() as db:
            db.execute(
                "UPDATE input_replies SET status='expired',deadline=NULL WHERE thread_id=? AND id=? AND status IN ('pending','failed')",
                (tid, ident),
            )

    async def answer(self, tid, ident, answers=None, *, automatic=False):
        key = (tid, ident)
        async with self.locks.setdefault(key, asyncio.Lock()):
            request = self.pending.get(key)
            if not request or request["replyStatus"] not in ("pending", "failed"):
                raise ValueError("该问题已提交或已失效")
            if automatic:
                answers = default_answers(request, request["draftAnswers"])
            validate_answers(request, answers)
            with self.connect() as db:
                changed = db.execute(
                    "UPDATE input_replies SET status='sending',deadline=NULL WHERE thread_id=? AND id=? AND status IN ('pending','failed')",
                    key,
                ).rowcount
            if not changed:
                raise ValueError("该问题已提交或正在提交")
            request["replyStatus"] = "sending"
            try:
                await self.deliver(request, answers, automatic)
            except Exception as error:
                status = "uncertain" if isinstance(error, DeliveryUncertain) else "failed"
                with self.connect() as db:
                    db.execute(
                        "UPDATE input_replies SET status=?,deadline=NULL WHERE thread_id=? AND id=?",
                        (status, *key),
                    )
                request.update(replyStatus=status, deadline=None)
                if status == "uncertain":
                    self.pending.pop(key, None)
                raise
            with self.connect() as db:
                db.execute(
                    "UPDATE input_replies SET status='answered',automatic=?,answered_at=? WHERE thread_id=? AND id=?",
                    (int(automatic), self.clock(), *key),
                )
            self.pending.pop(key, None)

    async def tick(self):
        due = [
            key
            for key, request in list(self.pending.items())
            if request["replyStatus"] == "pending"
            and request["deadline"] is not None
            and request["deadline"] <= self.clock()
        ]

        async def submit(tid, ident):
            try:
                await self.answer(tid, ident, automatic=True)
            except Exception as error:
                # Failed delivery stays visible for an explicit retry. No loop
                # can send the same answer again just because the timer expired.
                logging.getLogger(__name__).warning(
                    "Question reply failed: %s", type(error).__name__
                )

        await asyncio.gather(*(submit(tid, ident) for tid, ident in due))
