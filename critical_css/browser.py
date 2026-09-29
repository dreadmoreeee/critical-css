"""Headless Chromium session with a request cache and HTML override.

Every render goes through one route handler that

* serves the page HTML from memory (the original or the rewritten copy),
  so the site is fetched once and both versions share the same origin;
* answers repeated GETs from an in-memory cache, so each asset is
  downloaded once per run;
* aborts anything that is not GET/HEAD, anything under /cdn-cgi/, and the
  URLs listed in ``block`` (used to simulate "full CSS not loaded yet");
* records stylesheet bodies, used for sizes and cross-origin CSSOM access.
"""

from __future__ import annotations

from urllib.parse import urldefrag

from .fetch import Throttle, is_skipped_url

_DROP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}


class Session:
    def __init__(self, playwright, page_url: str, user_agent: str,
                 throttle: Throttle, timeout: float = 30.0):
        self.page_url = urldefrag(page_url)[0]
        self.user_agent = user_agent
        self.throttle = throttle
        self.timeout_ms = int(timeout * 1000)
        self.browser = playwright.chromium.launch()
        self.cache: dict[str, tuple[int, dict, bytes]] = {}
        self.css: dict[str, bytes] = {}
        self.renders = 0

    def close(self):
        self.browser.close()

    def _handler(self, html: bytes, content_type: str, block: set[str]):
        def handle(route, request):
            url = request.url
            if request.is_navigation_request() and urldefrag(url)[0] == self.page_url:
                route.fulfill(status=200, headers={"content-type": content_type}, body=html)
                return
            if (request.method not in ("GET", "HEAD") or is_skipped_url(url)
                    or url in block or not url.startswith(("http:", "https:"))):
                route.abort()
                return
            hit = self.cache.get(url)
            if hit is None:
                try:
                    resp = route.fetch(timeout=self.timeout_ms)
                    body = resp.body()
                except Exception:
                    route.abort()
                    return
                headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_HEADERS}
                hit = (resp.status, headers, body)
                self.cache[url] = hit
            status, headers, body = hit
            if request.resource_type == "stylesheet" and 200 <= status < 300:
                self.css[url] = body
            route.fulfill(status=status, headers=headers, body=body)
        return handle

    def render(self, viewport: tuple[int, int], html: bytes, content_type: str,
               fn, block=()):
        """Open the page at ``viewport`` with ``html`` and return ``fn(page)``."""
        width, height = viewport
        ctx = self.browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            user_agent=self.user_agent,
            service_workers="block",
        )
        try:
            page = ctx.new_page()
            page.set_default_timeout(self.timeout_ms)
            page.route("**/*", self._handler(html, content_type, set(block)))
            self.throttle.wait()
            self.renders += 1
            page.goto(self.page_url, wait_until="load", timeout=self.timeout_ms)
            settle(page)
            return fn(page)
        finally:
            ctx.close()


def settle(page, idle_ms: int = 5000):
    try:
        page.wait_for_load_state("networkidle", timeout=idle_ms)
    except Exception:
        pass
    page.evaluate("() => document.fonts ? document.fonts.ready.then(() => true) : true")
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(250)


def screenshot(page) -> bytes:
    return page.screenshot(animations="disabled", caret="hide")
