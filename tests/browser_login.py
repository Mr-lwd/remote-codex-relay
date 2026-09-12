"""Login regression checks for stale cookies and blocked cookie storage."""

from urllib.parse import urlsplit

from playwright.sync_api import expect


def check_login_cookies(browser, base, password):
    context = browser.new_context()
    try:
        context.add_cookies(
            [
                {
                    "name": "relay_session",
                    "value": "stale-session",
                    "domain": urlsplit(base).hostname,
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "Strict",
                }
            ]
        )
        page = context.new_page()
        page.goto(base)
        page.get_by_label("访问口令", exact=True).fill(password)
        thread_requests = []
        page.on(
            "request",
            lambda request: thread_requests.append(request)
            if request.url == base + "/api/threads"
            else None,
        )
        page.get_by_role("button", name="进入工作台").click()
        expect(page.locator("#access")).to_have_count(0)
        assert len(thread_requests) == 1
        page.reload()
        expect(page.locator(".app-shell")).to_be_visible()
        assert context.request.get(base + "/api/threads").status == 200
        assert (
            context.request.post(base + "/api/logout", headers={"X-Relay-Request": "1"}).status
            == 200
        )
        assert context.request.get(base + "/api/threads").status == 401
    finally:
        context.close()

    context = browser.new_context()
    try:
        page = context.new_page()

        def block_cookie(route):
            response = route.fetch()
            headers = {k: v for k, v in response.headers.items() if k.lower() != "set-cookie"}
            # Playwright's fetch shares the browser cookie jar; discard its Set-Cookie too.
            context.clear_cookies()
            route.fulfill(status=response.status, body=response.body(), headers=headers)

        page.route("**/api/login", block_cookie)
        page.goto(base)
        page.get_by_label("访问口令", exact=True).fill(password)
        page.get_by_role("button", name="进入工作台").click()
        expect(page.get_by_role("alert")).to_contain_text("口令已验证")
        expect(page.locator("#access")).to_be_visible()
        assert context.request.get(base + "/api/threads").status == 401
    finally:
        context.close()
