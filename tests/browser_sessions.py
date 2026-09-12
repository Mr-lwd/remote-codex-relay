"""Check conversation isolation with synthetic sessions and deliberately delayed replies.

Only static frontend assets come from --url; all API traffic is intercepted.
"""

import argparse
import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


def check_sessions(page):
    def fixture(tid):
        return {
            "id": tid,
            "title": f"会话 {tid}",
            "cwd": f"/tmp/session-{tid}",
            "status": "running",
            "managed": True,
            "modelSwitchAllowed": True,
            "model": "gpt-6-astra" if tid == "A" else "gpt-5.6-sol",
            "reasoningEffort": "high" if tid == "A" else "low",
            "tokens": 0,
            "messages": [
                {
                    "id": f"message-{tid}",
                    "role": "assistant",
                    "text": f"消息归属 {tid}",
                    "at": "2026-01-01T00:00:00Z",
                }
            ],
            "notes": [],
            "commandRecords": [],
            "activities": [],
            "requests": [],
            "pendingMessages": []
            if tid == "C"
            else [{"id": "shared-queue-id", "text": f"队列归属 {tid}", "attachments": []}],
            "pendingCount": 0 if tid == "C" else 1,
            "executionSettings": {"collaboration": "default", "permission": "workspace"},
            "goal": None
            if tid == "C"
            else {"objective": f"目标归属 {tid}", "status": "active", "time_used_seconds": 12},
            "history": {"mode": "recent", "hasMore": True, "total": 20},
        }

    state = {tid: fixture(tid) for tid in "ABC"}
    held, submitted, calls, errors, warnings = [], [], [], [], []
    assets = {}
    hold = {"path": None}
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: warnings.append(message.text)
        if message.type in ("warning", "error")
        else None,
    )
    page.add_init_script("""localStorage.setItem('relay-thread', 'A');
      localStorage.removeItem('relay-model-prefs');
      localStorage.removeItem('relay-control-prefs');
      window.fixtureStreams = [];
      window.EventSource = class {
        constructor(url) { this.url = url; this.closed = false;
          window.fixtureStreams.push(this); }
        addEventListener() {}
        close() { this.closed = true; }
      };
    """)

    def route_api(route):
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path
        calls.append((request.method, path))
        if hold["path"] == path or hold["path"] == path + "?" + parsed.query:
            held.append(route)
            hold["path"] = None
            return
        if path == "/api/threads":
            route.fulfill(json={"threads": list(state.values()), "defaultCwd": "/tmp"})
        elif path == "/api/media":
            params = parse_qs(parsed.query)
            name, tid = params["name"][0], params["thread"][0]
            asset = {
                "id": name,
                "name": name,
                "kind": "file",
                "mime": "text/plain",
                "size": 12,
                "url": "/fixture.txt",
            }
            assets[name] = tid
            route.fulfill(json=asset)
        elif path.endswith("/messages"):
            tid = path.split("/")[3]
            submitted.append((tid, request.post_data_json))
            route.fulfill(json={"kind": "queued"})
        elif path.endswith("/cancel"):
            tid = path.split("/")[3]
            state[tid]["pendingMessages"] = []
            route.fulfill(json={"ok": True})
        elif path.endswith("/goal"):
            tid = path.split("/")[3]
            action = request.post_data_json["action"]
            if action == "clear":
                state[tid]["goal"] = None
            else:
                state[tid]["goal"]["status"] = "paused"
            route.fulfill(json={"message": "目标已更新"})
        elif path == "/api/tasks":
            route.fulfill(json={"jobId": "fixture-job"})
        elif path.startswith("/api/jobs/"):
            route.fulfill(json={"thread_id": "C"})
        elif "/requests/" in path:
            tid = path.split("/")[3]
            state[tid]["requests"] = []
            route.fulfill(json={"ok": True})
        elif path.startswith("/api/threads/") and request.method == "GET":
            tid = path.split("/")[3]
            result = copy.deepcopy(state[tid])
            if parse_qs(parsed.query).get("history") == ["all"]:
                result["history"] = {"mode": "all", "hasMore": False, "total": 20}
            route.fulfill(json=result)
        else:
            route.fulfill(status=404, json={"detail": "Unexpected fixture request"})

    page.route("**/api/**", route_api)
    page.reload()
    field = page.get_by_label("向 Codex 发送消息", exact=True)

    def choose(tid, wait=True):
        if page.viewport_size["width"] < 1024:
            page.locator(".mobile-menu").click()
        page.locator(".thread").filter(has_text=f"会话 {tid}").click()
        if wait:
            expect(page.locator(".messages .message")).to_contain_text(f"消息归属 {tid}")

    def emit(tid, *, closed=False, payload=None):
        page.evaluate(
            """({tid, closed, payload}) => {
          const streams = window.fixtureStreams.filter(s =>
            new URL(s.url, location.origin).searchParams.get('thread') === tid &&
            s.closed === closed);
          const stream = streams.at(-1);
          if (!stream) throw Error('Missing fixture stream');
          stream.onmessage({data: JSON.stringify(payload)});
        }""",
            {
                "tid": tid,
                "closed": closed,
                "payload": payload or {"threads": list(state.values()), "selected": state[tid]},
            },
        )

    def settle():
        # Wait for fetch promise continuations and React paints after fulfilling a held route.
        page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )

    def release(body, status=200):
        assert held, "Expected a delayed request"
        route = held.pop(0)
        route.fulfill(status=status, json=body)
        settle()
        return route.request

    def delay(path, action):
        hold["path"] = path
        with page.expect_request(lambda request: urlsplit(request.url).path == path.split("?")[0]):
            action()
        settle()
        assert held, path

    def upload(name):
        page.locator(".composer input[type=file]").set_input_files(
            {"name": name, "mimeType": "text/plain", "buffer": b"reference"}
        )

    send = page.get_by_role("button", name="发送消息", exact=True)
    stop = page.get_by_role("button", name="停止回复", exact=True)
    model = page.get_by_role("button", name="本条指令模型", exact=True)

    def check(tid):
        expect(page.locator(".goal-control:visible")).to_have_count(0 if tid == "C" else 1)
        expect(page.locator(".goal-control")).to_have_count(0 if tid == "C" else 1)
        expect(page.locator(".message-queue")).to_have_count(0 if tid == "C" else 1)
        if tid != "C":
            expect(page.locator(".goal-summary")).to_contain_text(f"目标归属 {tid}")
            expect(page.locator(".message-queue li")).to_have_text(f"队列归属 {tid}")
        expect(page.locator(".messages .message")).to_contain_text(f"消息归属 {tid}")

    expect(field).to_be_enabled()
    check("A")
    for tid in "BCABACABCA":
        choose(tid)
        check(tid)

    # Draft text, command tokens, uploads, model/effort and busy flags stay with their owner.
    field.fill("/goal ")
    field.fill("A 的目标草稿")
    delay("/api/media", lambda: upload("A.txt"))
    choose("B")
    expect(field).to_have_value("")
    expect(page.locator(".command-token")).to_have_count(0)
    expect(page.locator(".composer-attachments")).to_have_count(0)
    field.fill("B 的草稿")
    upload("B.txt")
    expect(page.locator(".composer-attachments")).to_contain_text("已就绪")
    assets["A.txt"] = "A"
    release({"id": "A.txt", "name": "A.txt", "kind": "file", "size": 12})
    expect(page.locator(".composer-attachments")).to_contain_text("B.txt")
    expect(page.locator(".composer-attachments")).not_to_contain_text("A.txt")
    choose("A")
    expect(field).to_have_value("A 的目标草稿")
    expect(page.locator(".command-token")).to_have_text("/goal")
    expect(page.locator(".composer-attachments")).to_contain_text("A.txt")
    expect(model).to_have_attribute("title", "GPT-6-Astra · high")
    delay("/api/threads/A/messages", send.click)
    field.fill("A 后续修改的草稿")
    choose("B")
    expect(field).to_have_value("B 的草稿")
    expect(send).to_be_enabled()
    expect(model).to_have_attribute("title", "GPT-5.6-Sol · low")
    send.click()
    expect(stop).to_be_enabled()
    field.fill("B 的下一条草稿")
    req = release(
        {
            "kind": "queued",
            "message": "A 队列回执",
            "pendingMessage": {"id": "new-A", "text": "A 新排队消息", "attachments": []},
        }
    )
    assert req.post_data_json["text"] == "/goal A 的目标草稿"
    assert req.post_data_json["attachments"] == ["A.txt"]
    assert req.post_data_json["model"] == "gpt-6-astra"
    assert submitted[-1][0] == "B"
    assert submitted[-1][1]["attachments"] == ["B.txt"]
    assert submitted[-1][1]["model"] == "gpt-5.6-sol"
    assert submitted[-1][1]["effort"] == "low"
    check("B")
    expect(field).to_have_value("B 的下一条草稿")
    expect(page.locator(".toast")).not_to_contain_text("A 队列回执")
    choose("A")
    expect(field).to_have_value("A 后续修改的草稿")
    expect(page.locator(".composer-attachments")).to_have_count(0)

    # A→B→A while a send is pending cannot clear a newer A draft or invoke old /help UI.
    page.locator(".command-token").click()
    field.fill("/help ")
    delay("/api/threads/A/messages", send.click)
    choose("B")
    choose("A")
    page.locator(".command-token").click()
    field.fill("往返后新增的草稿")
    release({"kind": "command"})
    expect(field).to_have_value("往返后新增的草稿")
    expect(page.locator(".command-menu")).to_have_count(0)

    # A delayed delete must not hide B's entry, even with the same synthetic item ID.
    delay(
        "/api/threads/A/queue/shared-queue-id/cancel",
        lambda: page.get_by_role("button", name="删除排队消息：队列归属 A", exact=True).click(),
    )
    choose("B")
    state["A"]["pendingMessages"] = []
    release({"ok": True})
    check("B")
    expect(page.locator(".toast")).to_have_count(0)
    page.get_by_role("button", name="删除排队消息：队列归属 B", exact=True).click()
    expect(page.locator(".message-queue")).to_have_count(0)
    assert ("POST", "/api/threads/B/queue/shared-queue-id/cancel") in calls

    # Delayed failures also belong to their originating conversation.
    field.fill("延迟失败")
    delay("/api/threads/B/messages", send.click)
    choose("C")
    field.fill("C 不受影响")
    release({"detail": "B 专属发送失败"}, status=500)
    expect(field).to_have_value("C 不受影响")
    expect(page.locator(".composer-context [role=alert]")).to_have_count(0)
    choose("B")
    expect(page.locator(".composer-context [role=alert]")).to_contain_text("B 专属发送失败")
    expect(field).to_have_value("延迟失败")

    # Delayed GETs, closed streams and malformed thread ownership cannot replace current data.
    choose("C")
    delay("/api/threads/A", lambda: choose("A", wait=False))
    expect(page.locator(".goal-control")).to_have_count(0)
    expect(send).to_be_disabled()
    old = copy.deepcopy(state["A"])
    old["goal"]["objective"] = "不应出现的旧目标"
    choose("B")
    choose("A")
    release(old)
    expect(page.locator(".goal-summary")).to_contain_text("目标归属 A")
    emit("B", closed=True)
    emit("A", payload={"threads": list(state.values()), "selected": state["B"]})
    expect(page.locator(".goal-summary")).to_contain_text("目标归属 A")

    # A newer SSE snapshot wins over a GET that was started earlier on the same view.
    choose("B")
    delay("/api/threads/A", lambda: choose("A", wait=False))
    emit("A")
    release(old)
    expect(page.locator(".goal-summary")).to_contain_text("目标归属 A")

    # Old history loads cannot set another view to full history, including a round trip.
    delay(
        "/api/threads/A?history=all",
        lambda: page.get_by_role("button", name="向上滚动加载更早记录", exact=True).click(),
    )
    choose("B")
    expect(page.get_by_role("button", name="向上滚动加载更早记录", exact=True)).to_be_enabled()
    choose("A")
    old["history"] = {"mode": "all", "hasMore": False, "total": 20}
    release(old)
    expect(page.get_by_role("button", name="向上滚动加载更早记录", exact=True)).to_be_enabled()
    expect(page.locator(".goal-summary")).to_contain_text("目标归属 A")

    # Pause and stop acknowledgments cannot replace B's active goal or disable its composer.
    delay(
        "/api/threads/A/goal",
        lambda: page.get_by_role("button", name="暂停目标", exact=True).click(),
    )
    choose("B")
    state["A"]["goal"]["status"] = "paused"
    release({"message": "A 已暂停"})
    expect(page.locator(".goal-summary")).to_contain_text("进行中的目标")
    expect(page.locator(".toast")).to_have_count(0)
    choose("A")
    field.fill("")
    delay("/api/threads/A/stop", stop.click)
    choose("B")
    expect(send).to_be_enabled()
    release({"message": "A 已停止"})
    expect(send).to_be_enabled()
    expect(page.locator(".goal-summary")).to_contain_text("目标归属 B")

    # Identical approval IDs must not retain answers or pending submission state across threads.
    question = {
        "id": "same-request",
        "type": "input",
        "questions": [{"id": "answer", "header": "验收回答", "question": "请输入回答"}],
    }
    state["A"]["requests"] = [copy.deepcopy(question)]
    state["B"]["requests"] = [copy.deepcopy(question)]
    choose("A")
    page.get_by_label("验收回答", exact=True).fill("A 的回答")
    delay(
        "/api/threads/A/requests/same-request",
        lambda: page.get_by_role("button", name="提交回答", exact=True).click(),
    )
    choose("B")
    expect(page.get_by_label("验收回答", exact=True)).to_have_value("")
    page.get_by_label("验收回答", exact=True).fill("B 的回答")
    release({"ok": True})
    expect(page.get_by_label("验收回答", exact=True)).to_have_value("B 的回答")
    expect(page.get_by_role("button", name="提交回答", exact=True)).to_be_enabled()

    # External removal while a goal modal is open cleans up its portal and focus locks.
    page.get_by_role("button", name="展开目标", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    emit("B", payload={"threads": [state["A"], state["C"]], "removedThread": "B"})
    expect(page.get_by_role("dialog")).to_have_count(0)
    expect(page.locator(".goal-summary")).to_contain_text("目标归属 A")
    expect(page.locator("body")).not_to_have_attribute("data-scroll-locked", "1")
    field.fill("删除会话后的输入仍可操作")
    expect(send).to_be_enabled()
    emit("A")

    # Finishing creation after a manual switch must not hijack the chosen conversation.
    if page.viewport_size["width"] < 1024:
        page.locator(".mobile-menu").click()
    page.get_by_role("button", name="新建任务", exact=False).click()
    page.get_by_label("任务内容", exact=True).fill("新任务隔离验收")
    delay(
        "/api/jobs/fixture-job",
        lambda: page.get_by_role("button", name="启动任务", exact=True).click(),
    )
    choose("B")
    release({"thread_id": "C"})
    expect(page.locator(".pending")).to_have_count(0)
    expect(page.locator(".messages .message")).to_contain_text("消息归属 B")
    assert not errors, errors
    assert not any("same key" in message for message in warnings), warnings
    return {
        "repeatedSwitches": True,
        "draftAndAttachmentIsolation": True,
        "delayedSendsAndFailures": True,
        "queueOwnership": True,
        "staleSnapshotsAndHistory": True,
        "goalStopAndApprovalIsolation": True,
        "externalModalCleanup": True,
        "creationDoesNotHijackSelection": True,
        "consoleWarnings": warnings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cdp")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp) if args.cdp else p.chromium.launch()
        try:
            for width, height in [(1440, 900), (390, 844)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                try:
                    page.goto(args.url)
                    result = check_sessions(page)
                    results.append({"viewport": [width, height], "status": "passed", **result})
                    page.screenshot(path=str(args.output / f"{width}.png"))
                except Exception as exc:
                    results.append(
                        {"viewport": [width, height], "status": "failed", "error": str(exc)}
                    )
                    page.screenshot(path=str(args.output / f"{width}-failed.png"))
                    raise
                finally:
                    context.close()
        finally:
            (args.output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2)
            )
            if not args.cdp:
                browser.close()


if __name__ == "__main__":
    main()
