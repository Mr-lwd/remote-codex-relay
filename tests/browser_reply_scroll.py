"""Keep reply beginnings visible using synthetic messages and SSE snapshots."""

import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


def check_reply_scroll(page):
    def message(mid, role, text, second):
        return {"id": mid, "role": role, "text": text, "at": f"2026-01-01T00:00:{second:02d}Z"}

    def long_reply(label, count=45):
        return "\n\n".join(
            f"{label}第 {i + 1} 段：这是需要从头阅读的回答内容。" for i in range(count)
        )

    def thread(tid):
        return {
            "id": tid,
            "title": f"滚动会话 {tid}",
            "cwd": "/tmp/scroll-fixture",
            "status": "idle",
            "model": "gpt-6-astra",
            "reasoningEffort": "high",
            "modelSwitchAllowed": True,
            "tokens": 0,
            "goal": None,
            "messages": [
                message(tid + "-question", "user", "请详细回答", 10),
                message(tid + "-answer", "assistant", long_reply(tid), 11),
            ],
            "notes": [],
            "activities": [],
            "requests": [],
            "commandRecords": [],
            "pendingMessages": [],
            "pendingCount": 0,
            "history": {"mode": "recent", "hasMore": True, "total": 8},
        }

    state = {tid: thread(tid) for tid in ("A", "B")}
    errors = []
    held = []
    hold_send = [False]
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("""localStorage.setItem('relay-thread', 'A');
      window.scrollStreams = [];
      window.EventSource = class {
        constructor(url) { this.url = url; this.closed = false; window.scrollStreams.push(this); }
        addEventListener() {} close() { this.closed = true; }
      };
    """)

    def route_api(route):
        url = urlsplit(route.request.url)
        parts = url.path.split("/")
        if url.path == "/api/threads":
            route.fulfill(json={"threads": list(state.values()), "defaultCwd": "/tmp"})
        elif len(parts) == 4 and parts[3] in state:
            detail = state[parts[3]]
            if parse_qs(url.query).get("history") == ["all"] and detail["history"]["hasMore"]:
                detail["messages"] = [
                    message(f"old-{i}", "assistant", long_reply("旧记录", 4), i) for i in range(6)
                ] + detail["messages"]
                detail["history"] = {
                    "mode": "all",
                    "hasMore": False,
                    "total": len(detail["messages"]),
                }
            route.fulfill(json=detail)
        elif url.path.endswith("/messages"):
            if hold_send[0]:
                held.append(route)
            else:
                detail = state[parts[3]]
                detail["messages"].append(
                    message("sent", "user", route.request.post_data_json["text"], 20)
                )
                detail["status"] = "running"
                route.fulfill(json={"kind": "started"})
        else:
            route.fulfill(status=404, json={"detail": "Unexpected scroll fixture request"})

    page.route("**/api/**", route_api)
    page.reload()
    viewport = page.locator(".messages")

    def settle():
        page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )

    def push(tid="A"):
        page.evaluate(
            """payload => {
              const stream = window.scrollStreams.findLast(s => !s.closed);
              stream.onmessage({data: JSON.stringify(payload)});
            }""",
            {"threads": list(state.values()), "selected": state[tid]},
        )
        settle()

    def top():
        return viewport.evaluate("el => el.scrollTop")

    def pinned(mid):
        page.wait_for_function(
            """id => {
              const viewport = document.querySelector('.messages');
              if (!viewport) return false;
              const message = [...viewport.querySelectorAll('[data-message-id]')]
                .find(el => el.dataset.messageId === id);
              return message && Math.abs(message.getBoundingClientRect().top -
                viewport.getBoundingClientRect().top - parseFloat(getComputedStyle(viewport).paddingTop)) < 3;
            }""",
            arg=mid,
        )

    def choose(tid):
        if page.viewport_size["width"] < 1024:
            page.get_by_role("button", name="打开会话列表", exact=True).click()
        page.locator(".thread").filter(has_text=f"滚动会话 {tid}").click()
        expect(page.locator(".conversation-title h2")).to_have_text(f"滚动会话 {tid}")

    # Opening an existing long answer starts at its beginning.
    pinned("A-answer")
    assert viewport.evaluate("el => el.scrollHeight - el.scrollTop - el.clientHeight > 500")

    # Start a fresh turn, then grow a short reply through several SSE updates.
    page.get_by_label("向 Codex 发送消息", exact=True).fill("请继续说明")
    page.get_by_role("button", name="发送消息", exact=True).click()
    pinned("sent")
    answer = message("stream-answer", "assistant", "回答开头。", 21)
    state["A"]["messages"].append(answer)
    push()
    pinned("stream-answer")
    for count in (3, 12, 45):
        answer["text"] = long_reply("新回答", count)
        push()
        pinned("stream-answer")
    state["A"]["status"] = "idle"
    state["A"]["notes"] = [
        {"id": "done", "role": "system", "text": "执行完毕", "created": 1767225622}
    ]
    push()
    pinned("stream-answer")

    # Resize (including a mobile keyboard) does not chase the answer's end.
    size = page.viewport_size.copy()
    page.set_viewport_size({**size, "height": size["height"] - 180})
    settle()
    pinned("stream-answer")
    page.set_viewport_size(size)

    # The reader can scroll down or up; completion/status/new replies preserve it.
    start = top()
    viewport.hover()
    page.mouse.wheel(0, 240)
    page.wait_for_function(
        "start => document.querySelector('.messages').scrollTop >= start + 200", arg=start
    )
    settle()
    manual = top()
    answer["text"] = long_reply("新回答", 60)
    push()
    assert abs(top() - manual) < 3
    state["A"]["messages"].append(message("followup", "assistant", long_reply("后续说明"), 23))
    push()
    assert abs(top() - manual) < 3
    page.set_viewport_size({**size, "height": size["height"] - 100})
    settle()
    assert abs(top() - manual) < 3
    page.set_viewport_size(size)

    # Loading older history preserves the same visible message and pixel offset.
    viewport.evaluate("el => el.scrollTop = 120")
    settle()
    before = page.locator('[data-message-id="A-answer"]').evaluate(
        "el => el.getBoundingClientRect().top"
    )
    page.evaluate("document.querySelector('.history-more').click()")
    expect(page.locator('[data-message-id="old-0"]')).to_be_attached()
    settle()
    after = page.locator('[data-message-id="A-answer"]').evaluate(
        "el => el.getBoundingClientRect().top"
    )
    assert abs(after - before) < 3, (before, after)

    # Each conversation and a reload locate their own latest answer.
    choose("B")
    pinned("B-answer")
    choose("A")
    pinned("followup")
    viewport.evaluate("el => el.scrollTop += 200")
    settle()
    page.reload()
    pinned("followup")

    # A slow send receipt must not override scrolling performed while awaiting it.
    hold_send[0] = True
    field = page.get_by_label("向 Codex 发送消息", exact=True)
    field.fill("延迟回执")
    page.get_by_role("button", name="发送消息", exact=True).click()
    expect(field).to_have_value("延迟回执")
    viewport.evaluate("el => el.scrollTop += 220")
    settle()
    manual = top()
    assert len(held) == 1
    held.pop().fulfill(json={"kind": "started"})
    expect(field).to_have_value("")
    state["A"]["messages"].append(message("late-answer", "assistant", long_reply("延迟回复"), 24))
    push()
    assert abs(top() - manual) < 3
    assert not errors, errors
    return {
        "replyStart": True,
        "streamAndCompletion": True,
        "manualReading": True,
        "resize": True,
        "historyAnchor": True,
        "sessionsAndReload": True,
        "delayedSend": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for width, height in [(1440, 900), (390, 844)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                try:
                    page = context.new_page()
                    page.goto(args.url)
                    results.append({"width": width, **check_reply_scroll(page)})
                finally:
                    context.close()
        finally:
            browser.close()
    (args.output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print("Reply scroll checks passed on desktop and mobile.")


if __name__ == "__main__":
    main()
