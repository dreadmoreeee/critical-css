"""Polite HTTP fetching (robots.txt, delay, redirects) and a local file server."""

from __future__ import annotations

import functools
import http.server
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit, urlunsplit

from .rfc9309 import RobotsTxt

USER_AGENT = "critical-css/1.0 (+https://github.com/dreadmoreeee/critical-css)"
MAX_REDIRECTS = 5
_RESERVED_PORTS = {8765, 8737, 8787}


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


class Throttle:
    """Keeps at least ``delay`` seconds between consecutive calls to wait()."""

    def __init__(self, delay: float):
        self.delay = max(0.0, delay)
        self._last = None
        self.count = 0

    def wait(self):
        if self._last is not None:
            remaining = self.delay - (time.monotonic() - self._last)
            if remaining > 0:
                time.sleep(remaining)
        self._last = time.monotonic()
        self.count += 1


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def is_skipped_url(url: str) -> bool:
    """URLs the tool never requests (Cloudflare internals)."""
    return "/cdn-cgi/" in urlsplit(url).path


def sniff_encoding(content_type: str, data: bytes) -> str:
    enc = "utf-8"
    m = re.search(r"charset=([\w.-]+)", content_type or "", re.I)
    if m:
        enc = m.group(1)
    else:
        mb = re.search(rb"""<meta[^>]+charset=["']?([\w.-]+)""", data[:4096], re.I)
        if mb:
            enc = mb.group(1).decode("ascii", "replace")
    try:
        "".encode(enc)
    except LookupError:
        enc = "utf-8"
    return enc


class Fetcher:
    """GET-only fetcher that honours robots.txt (RFC 9309) and a delay."""

    def __init__(self, user_agent: str = USER_AGENT, delay: float = 1.0,
                 timeout: float = 30.0, respect_robots: bool = True,
                 throttle: Throttle | None = None):
        self.user_agent = user_agent
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.throttle = throttle or Throttle(delay)
        self._robots: dict[str, RobotsTxt] = {}
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _raw_get(self, url: str):
        self.throttle.wait()
        req = urllib.request.Request(url, headers={
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
        })
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            body = err.read() if err.fp else b""
            return err.code, dict(err.headers or {}), body

    def robots_for(self, url: str) -> RobotsTxt:
        parts = urlsplit(url)
        origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        robots = None
        try:
            for _ in range(MAX_REDIRECTS + 1):
                status, headers, body = self._raw_get(robots_url)
                if status in (301, 302, 303, 307, 308) and headers.get("Location"):
                    robots_url = urljoin(robots_url, headers["Location"])
                    continue
                if 200 <= status < 300:
                    robots = RobotsTxt.parse(body.decode("utf-8", "replace"))
                elif 400 <= status < 500:
                    robots = RobotsTxt.allow_all()
                else:
                    robots = RobotsTxt.disallow_all()
                break
        except (OSError, urllib.error.URLError):
            robots = RobotsTxt.disallow_all()
        if robots is None:
            robots = RobotsTxt.disallow_all()
        self._robots[origin] = robots
        return robots

    def allowed(self, url: str) -> bool:
        if is_skipped_url(url):
            return False
        if not self.respect_robots:
            return True
        return self.robots_for(url).can_fetch(self.user_agent, url)

    def get_page(self, url: str) -> tuple[str, str, bytes]:
        """Return ``(final_url, content_type, body)``; follows redirects."""
        for _ in range(MAX_REDIRECTS + 1):
            if not self.allowed(url):
                raise RobotsDisallowed("robots.txt disallows %s" % url)
            try:
                status, headers, body = self._raw_get(url)
            except (OSError, urllib.error.URLError) as err:
                raise FetchError("cannot fetch %s: %s" % (url, err)) from err
            if status in (301, 302, 303, 307, 308) and headers.get("Location"):
                url = urljoin(url, headers["Location"])
                continue
            if status != 200:
                raise FetchError("HTTP %d for %s" % (status, url))
            ctype = headers.get("Content-Type") or headers.get("content-type") or "text/html"
            return url, ctype, body
        raise FetchError("too many redirects for %s" % url)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _bind_free_port(server_cls, handler):
    for _ in range(20):
        server = server_cls(("127.0.0.1", 0), handler)
        if server.server_address[1] not in _RESERVED_PORTS:
            return server
        server.server_close()
    raise OSError("no free port")


class LocalServer:
    """Serve a directory on 127.0.0.1 with a random free port."""

    def __init__(self, directory: str):
        handler = functools.partial(_QuietHandler, directory=directory)
        self.server = _bind_free_port(http.server.ThreadingHTTPServer, handler)
        self.port = self.server.server_address[1]
        self.base = "http://127.0.0.1:%d/" % self.port
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


__all__ = [
    "USER_AGENT", "Fetcher", "FetchError", "RobotsDisallowed", "Throttle",
    "LocalServer", "is_skipped_url", "sniff_encoding",
]
