"""User terminal text stays literal; assistant Markdown and attachments still render.

All API requests use fixtures. No real conversation is modified.
"""

import argparse
import base64
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import expect, sync_playwright

TERMINAL = (
    "user@host:~/project$ git remote add origin https://github.com/example/project.git\n"
    "error: remote origin already exists.\n"
    "user@host:~/project$ git branch -M main\n"
    "user@host:~/project$ git push -u origin main\n"
    "error: src refspec main does not match any\n"
    "\n~path~  ~~literal~~  $HOME  $PATH  a_b_c  <tag>  **text**\n"
    "    缩进及中文保持原样\n"
    "\t制表符\n"
    "价格 $10 和 $20。\n"
    "https://example.com/" + "long-path/" * 25
)
REPLY = (
    "**命令说明**，~~已废弃~~。\n\n"
    "```bash\nuser@host:~/project$ git push -u origin main\n```\n\n"
    "| 项目 | 数值 |\n| --- | --- |\n| 结果 | 42 |\n\n"
    "行内公式 $x^2$。\n\n$$x^2+y^2=z^2$$\n"
)

MATRIX = r"\begin{bmatrix}1 & 2 \\ 3 & 4\end{bmatrix}"
ALIGNED = "\\begin{aligned}\na &= b+c \\\\\nd &= \\frac{1}{2}\n\\end{aligned}"
LONG_MATH = " + ".join(f"x_{{{i}}}" for i in range(50))
# Each case checks the actual TeX delivered to KaTeX, not only a wrapper class.
MATH_CASES = [
    ("paren", r"行内 \(\frac{a_1}{b^2}\) 继续。", r"\frac{a_1}{b^2}", False),
    ("bracket", r"\[E=mc^2\]", "E=mc^2", True),
    ("dollars", "$$x^2+y^2=z^2$$", "x^2+y^2=z^2", True),
    ("prose", r"前文 $$\sum_{i=1}^{n}i$$ 后文", r"\sum_{i=1}^{n}i", True),
    ("matrix", "\\[\n" + MATRIX + "\n\\]", MATRIX, True),
    ("aligned", "$$\n" + ALIGNED + "\n$$", ALIGNED, True),
    ("compact-aligned", "$$" + ALIGNED + "$$", ALIGNED, True),
    ("blank", "\\[\na+b\n\nc+d\n\\]", "a+b\n\nc+d", True),
    ("quote", "> \\[\n> " + MATRIX + "\n> \\]", MATRIX, True),
    ("list", "1. 推导\n\n   \\[\n   x=y\n   \\]", "x=y", True),
    ("quoted-dollars", "> $$x=y$$", "x=y", True),
    ("table", "| 式子 |\n| --- |\n| \\(a_i^2\\) |", "a_i^2", False),
    ("math-fence", "```math\n" + MATRIX + "\n```", MATRIX + "\n", True),
    ("crlf", "\\[\r\nx=y\r\n\\]", "x=y", True),
    ("long", "\\[" + LONG_MATH + "\\]", LONG_MATH, True),
    ("escaped-dollar", r"\(\text{cost: }\$10\)", r"\text{cost: }\$10", False),
    ("triple-dollar", "$$$x^2$$$", "x^2", False),
]
LITERAL_MATH = (
    "`\\(x\\)` and ``\\[y\\]``\n\n"
    "``\n$$x$$\n``\n\n"
    "> ```text\n> $$x^2$$\n> \\[y\\]\n> ```\n\n"
    "- 示例\n\n  ```latex\n  \\(z\\)\n  ```\n\n"
    "缩进代码：\n\n    $$a=b$$\n\n"
    r"转义：\\(x\\)、\$5、\$10。"
)


def check_message_rendering(page, enforce=True):
    raw = io.BytesIO()
    Image.new("RGB", (40, 30), "green").save(raw, "PNG")
    asset = {
        "id": "preview",
        "name": "reference.png",
        "kind": "image",
        "width": 40,
        "height": 30,
        "url": "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode(),
    }
    messages = [
        {
            "id": "user-terminal",
            "role": "user",
            "text": TERMINAL,
            "images": [asset],
            "files": [
                {
                    "id": "document",
                    "name": "reference.txt",
                    "url": "/api/media/document",
                    "size": 12,
                }
            ],
            "at": "2026-01-01T00:00:00Z",
        },
        {"id": "reply", "role": "assistant", "text": REPLY, "at": "2026-01-01T00:00:01Z"},
    ]
    messages.extend(
        {"id": "math-" + name, "role": "assistant", "text": text} for name, text, _, _ in MATH_CASES
    )
    messages.extend(
        [
            {"id": "math-literal", "role": "assistant", "text": LITERAL_MATH},
            {"id": "math-invalid", "role": "assistant", "text": r"\[\frac{a}{\]"},
            {"id": "math-stream", "role": "assistant", "text": r"推导：\[\frac{"},
        ]
    )
    submitted = []

    def detail():
        return {
            "id": "literal-fixture",
            "title": "原文显示验收",
            "cwd": "/tmp/literal-fixture",
            "status": "idle",
            "model": "gpt-6-astra",
            "reasoningEffort": "high",
            "modelSwitchAllowed": True,
            "tokens": 0,
            "goal": None,
            "messages": messages,
            "notes": [],
            "activities": [],
            "requests": [],
            "commandRecords": [],
            "pendingMessages": [],
            "pendingCount": 0,
            "history": {"mode": "recent", "hasMore": False, "total": len(messages)},
        }

    def route_api(route):
        path = urlsplit(route.request.url).path
        if path == "/api/threads":
            route.fulfill(json={"threads": [detail()], "defaultCwd": "/tmp"})
        elif path == "/api/threads/literal-fixture":
            route.fulfill(json=detail())
        elif path.endswith("/messages"):
            text = route.request.post_data_json["text"]
            submitted.append(text)
            messages.append(
                {"id": "sent", "role": "user", "text": text, "at": "2026-01-01T00:00:02Z"}
            )
            route.fulfill(json={"kind": "started"})
        else:
            route.fulfill(status=404, json={"detail": "Unexpected fixture request"})

    page.route("**/api/**", route_api)
    page.add_init_script("""localStorage.setItem('relay-thread', 'literal-fixture');
      window.EventSource = class {
        constructor() { window.mathStream = this; } addEventListener() {} close() {}
      };
    """)
    page.reload()
    user = page.locator("[data-message-id=user-terminal]")
    reply = page.locator("[data-message-id=reply]")
    expect(user).to_be_attached()
    result = {
        "userMathNodes": user.locator(".katex").count(),
        "userStrikethroughNodes": user.locator("del").count(),
    }
    if not enforce:
        return result
    expect(
        user.locator(".message-body .katex, .message-body del, .message-body em, .message-body tag")
    ).to_have_count(0)
    literal = user.locator(".message-user-text")
    assert literal.text_content() == TERMINAL
    expect(literal).to_have_css("white-space", "pre-wrap")
    expect(reply.locator(".message-body strong")).to_have_text("命令说明")
    expect(reply.locator("del")).to_have_text("已废弃")
    expect(reply.locator("pre code")).to_have_text("user@host:~/project$ git push -u origin main\n")
    expect(reply.locator("table")).to_contain_text("42")
    expect(reply.locator(".katex")).to_have_count(2)
    expect(reply.locator(".katex-display")).to_have_count(1)
    for name, _, tex, display in MATH_CASES:
        formula = page.locator(f"[data-message-id=math-{name}]")
        expect(formula.locator(".katex")).to_have_count(1)
        expect(formula.locator(".katex-error")).to_have_count(0)
        expect(formula.locator(".katex-display")).to_have_count(int(display))
        actual = formula.locator('annotation[encoding="application/x-tex"]').text_content()
        assert actual == tex, (name, actual, tex)
    code = page.locator("[data-message-id=math-literal]")
    expect(code.locator(".katex")).to_have_count(0)
    expect(code.locator("p > code").nth(2)).to_have_text("$$x$$")
    assert code.locator("pre code").all_text_contents() == [
        "$$x^2$$\n\\[y\\]\n",
        "\\(z\\)\n",
        "$$a=b$$\n",
    ]
    expect(page.locator("[data-message-id=math-invalid] .katex-error")).to_have_text(r"\frac{a}{")
    streamed = page.locator("[data-message-id=math-stream]")
    expect(streamed.locator(".katex")).to_have_count(0)
    for text in [r"推导：\[\frac{a}{b}", r"推导：\[\frac{a}{b}\] 完成。"]:
        messages[-1]["text"] = text
        page.evaluate(
            "payload => window.mathStream.onmessage({data:JSON.stringify(payload)})",
            {"threads": [detail()], "selected": detail()},
        )
        if text.endswith("完成。"):
            expect(streamed.locator(".katex-display")).to_have_count(1)
            expect(streamed).to_contain_text("完成。")
        else:
            expect(streamed).to_contain_text(r"\frac{a}{b}")
    expect(streamed.locator(".katex-error")).to_have_count(0)
    display = page.locator("[data-message-id=math-long] .katex-display")
    expect(display).to_have_css("overflow-x", "auto")
    assert display.evaluate("el => el.scrollWidth > el.clientWidth")
    assert page.evaluate("async () => (await document.fonts.load('16px KaTeX_Main')).length > 0")
    expect(user.get_by_role("link", name="下载 reference.txt", exact=True)).to_have_attribute(
        "href", "/api/media/document"
    )
    user.get_by_role("button", name="预览 reference.png", exact=True).click()
    expect(page.get_by_role("dialog")).to_contain_text("reference.png")
    page.get_by_role("button", name="关闭图片预览", exact=True).click()
    field = page.get_by_label("向 Codex 发送消息", exact=True)
    field.fill(TERMINAL)
    page.get_by_role("button", name="发送消息", exact=True).click()
    expect(field).to_have_value("")
    assert submitted == [TERMINAL]
    assert page.locator("[data-message-id=sent] .message-user-text").text_content() == TERMINAL
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    result.update(
        literalTextAndWhitespace=True,
        assistantMarkdownMathAndCode=True,
        userAttachmentsPreserved=True,
        sendPayloadUnchanged=True,
        latexDelimitersAndContainers=len(MATH_CASES),
        mathCodePreserved=True,
        streamedMath=True,
        invalidMathFallback=True,
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cdp")
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp) if args.cdp else p.chromium.launch()
        try:
            for width, height in [(1440, 900), (390, 844)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                errors = []
                try:
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(args.url)
                    result = check_message_rendering(page, enforce=not args.baseline)
                    assert not errors, errors
                    results.append({"viewport": [width, height], "status": "passed", **result})
                    page.screenshot(path=str(args.output / f"{width}.png"))
                except Exception as error:
                    results.append(
                        {"viewport": [width, height], "status": "failed", "error": str(error)}
                    )
                    raise
                finally:
                    context.close()
        finally:
            (args.output / "results.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2)
            )
            if not args.cdp:
                browser.close()
    print("Message rendering checks passed.")


if __name__ == "__main__":
    main()
