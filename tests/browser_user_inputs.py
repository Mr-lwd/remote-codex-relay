"""Interactive questions and deadline display with isolated API/SSE fixtures."""

import argparse
import copy
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def check_user_inputs(page):
    received, drafts, held = [], [], []
    now = time.time()
    request = {
        "id": "async-question",
        "type": "input",
        "source": "async",
        "deadline": now + 30,
        "serverNow": now,
        "replyStatus": "pending",
        "questions": [
            {
                "id": "choice",
                "question": "选择执行方案",
                "header": "执行方案",
                "options": [
                    {"label": "方案 A", "description": "第一种选择"},
                    {"label": "方案 B（推荐）", "description": "推荐的选择"},
                ],
            },
            {"id": "detail", "question": "补充设备信息", "header": "设备信息"},
        ],
    }
    detail = {
        "id": "questions",
        "title": "问答验收",
        "cwd": "/tmp/questions",
        "status": "running",
        "model": "gpt-6-astra",
        "reasoningEffort": "high",
        "modelSwitchAllowed": True,
        "tokens": 0,
        "goal": None,
        "messages": [
            {
                "id": "long",
                "role": "assistant",
                "text": "较长的回复内容。\n\n" * 80,
                "at": "2026-01-01T00:00:00Z",
            }
        ],
        "notes": [],
        "activities": [],
        "commandRecords": [],
        "pendingMessages": [],
        "pendingCount": 0,
        "requests": [request],
        "history": {"mode": "recent", "hasMore": False, "total": 1},
    }

    def route_api(route):
        path = urlsplit(route.request.url).path
        if path.endswith("/draft"):
            drafts.append(route.request.post_data_json)
            request["savedAnswers"] = route.request.post_data_json["answers"]
            route.fulfill(json={"ok": True})
        elif "/requests/" in path:
            received.append(route.request.post_data_json)
            held.append(route)
        elif path == "/api/threads":
            route.fulfill(json={"threads": [detail], "defaultCwd": "/tmp"})
        elif path == "/api/threads/questions":
            route.fulfill(json=detail)
        else:
            route.fulfill(status=404, json={"detail": "fixture"})

    page.route("**/api/**", route_api)
    page.add_init_script("""localStorage.setItem('relay-thread','questions');
      window.EventSource=class {constructor(){window.questionStream=this;}addEventListener(){}close(){}};
    """)
    page.clock.install()
    page.reload()
    panel = page.get_by_label("待回答的问题", exact=True)
    expect(panel).to_be_visible()
    bounds = panel.bounding_box()
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= page.viewport_size["height"]
    expect(page.get_by_role("radio", name="方案 B（推荐）", exact=False)).to_be_checked()
    submit = page.get_by_role("button", name="提交回答", exact=True)
    expect(submit).to_be_disabled()
    # Let React install its passive timer effect before advancing the clock;
    # mobile WebKit may paint the card before that effect has run.
    page.clock.run_for(300)
    page.clock.fast_forward(20000)
    page.clock.run_for(300)
    countdown = panel.locator(".interaction-title > span").inner_text()
    seconds = int(re.search(r"(\d+) 秒", countdown)[1])
    assert 8 <= seconds <= 10, countdown
    page.get_by_role("radio", name="方案 A", exact=False).check()
    with page.expect_response(
        lambda response: response.url.endswith("/draft")
        and response.request.post_data_json["answers"].get("detail") == ["测试设备"]
    ):
        page.get_by_label("设备信息", exact=True).fill("测试设备")
    expect(submit).to_be_enabled()
    # Snapshot refreshes must preserve an in-progress answer and its deadline.
    page.evaluate(
        "d=>questionStream.onmessage({data:JSON.stringify({threads:[d],selected:d})})", detail
    )
    expect(page.get_by_role("radio", name="方案 A", exact=False)).to_be_checked()
    expect(page.get_by_label("设备信息", exact=True)).to_have_value("测试设备")
    request["serverNow"] = now + 20
    page.reload()
    expect(page.get_by_role("radio", name="方案 A", exact=False)).to_be_checked()
    expect(page.get_by_label("设备信息", exact=True)).to_have_value("测试设备")
    countdown = panel.locator(".interaction-title > span").inner_text()
    assert 8 <= int(re.search(r"(\d+) 秒", countdown)[1]) <= 10, countdown
    submit.click()
    expect(submit).to_be_disabled()
    assert len(received) == 1
    assert received[0]["answers"] == {"choice": ["方案 A"], "detail": ["测试设备"]}
    detail["requests"] = []
    held.pop().fulfill(json={"ok": True})
    expect(panel).to_have_count(0)
    assert drafts[-1]["answers"]["choice"] == ["方案 A"]

    # The server owns submission. A suspended/resumed page only shows the due
    # state; it does not create another answer from a client-side timer.
    next_request = copy.deepcopy(request)
    next_request.update(id="async-timeout", deadline=now + 31, serverNow=now + 20)
    detail["requests"] = [next_request]
    page.evaluate(
        "d=>questionStream.onmessage({data:JSON.stringify({threads:[d],selected:d})})", detail
    )
    expect(panel).to_be_visible()
    page.clock.fast_forward(12000)
    expect(panel).to_contain_text("正在自动回复")
    assert len(received) == 1
    detail["requests"] = []
    page.evaluate(
        "d=>questionStream.onmessage({data:JSON.stringify({threads:[d],selected:d})})", detail
    )
    expect(panel).to_have_count(0)
    return {
        "manualAnswer": True,
        "draftSynchronized": True,
        "serverDeadline": True,
        "visibleAboveComposer": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for width, height in [(1440, 900), (390, 844)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                try:
                    page.goto(args.url)
                    results.append(
                        {"viewport": [width, height], **check_user_inputs(page), "status": "passed"}
                    )
                except Exception:
                    page.screenshot(path=args.output / f"failure-{width}.png")
                    raise
                finally:
                    context.close()
        finally:
            browser.close()
            (args.output / "results.json").write_text(json.dumps(results, indent=2))
    print("Question interaction checks passed.")


if __name__ == "__main__":
    main()
