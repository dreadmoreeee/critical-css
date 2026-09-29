"""robots.txt parsing and matching as specified by RFC 9309.

Differences from urllib.robotparser that matter in practice:

* the most specific (longest) matching rule wins, and ``allow`` wins a tie;
* ``*`` wildcards and the ``$`` end anchor are supported;
* all groups naming the same user agent are merged;
* percent-encoding is normalised before comparing paths;
* ``/robots.txt`` itself is always allowed.

MIT License, Copyright (c) 2026 Marvin Palencia.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

__all__ = ["RobotsTxt", "normalize_path", "product_token"]

_UNRESERVED = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_HEX = frozenset(b"0123456789abcdefABCDEF")
_TOKEN_RE = re.compile(r"[A-Za-z_-]+")


def product_token(user_agent: str) -> str:
    """Return the product token of a User-Agent string, lower-cased.

    ``"critical-css/1.0 (+https://example.com)"`` -> ``"critical-css"``.
    """
    m = _TOKEN_RE.match(user_agent.strip())
    return m.group(0).lower() if m else ""


def normalize_path(path: str) -> str:
    """Normalise percent-encoding so equivalent paths compare equal.

    Escaped unreserved characters are decoded, other escapes get upper-case
    hex digits, and non-ASCII or control bytes are percent-encoded as UTF-8.
    """
    raw = path.encode("utf-8")
    out = []
    i = 0
    n = len(raw)
    while i < n:
        b = raw[i]
        if b == 0x25 and i + 2 < n and raw[i + 1] in _HEX and raw[i + 2] in _HEX:
            value = int(raw[i + 1:i + 3], 16)
            if value in _UNRESERVED:
                out.append(chr(value))
            else:
                out.append("%" + raw[i + 1:i + 3].decode("ascii").upper())
            i += 3
            continue
        if b >= 0x80 or b <= 0x20 or b == 0x7F:
            out.append("%%%02X" % b)
        else:
            out.append(chr(b))
        i += 1
    return "".join(out)


class _Rule:
    __slots__ = ("allow", "pattern", "length", "regex")

    def __init__(self, allow: bool, pattern: str):
        self.allow = allow
        self.pattern = normalize_path(pattern)
        self.length = len(self.pattern)
        body = self.pattern
        anchored = body.endswith("$")
        if anchored:
            body = body[:-1]
        parts = [re.escape(p) for p in body.split("*")]
        self.regex = re.compile(".*".join(parts) + (r"\Z" if anchored else ""), re.S)

    def matches(self, path: str) -> bool:
        return self.regex.match(path) is not None


class _Group:
    def __init__(self):
        self.agents: list[str] = []
        self.rules: list[_Rule] = []


class RobotsTxt:
    """A parsed robots.txt file."""

    def __init__(self, groups=None, sitemaps=None, default_allow: bool = True):
        self.groups: list[_Group] = list(groups or [])
        self.sitemaps: list[str] = list(sitemaps or [])
        self.default_allow = default_allow

    @classmethod
    def allow_all(cls) -> "RobotsTxt":
        """Used when robots.txt is unavailable (HTTP 4xx)."""
        return cls(default_allow=True)

    @classmethod
    def disallow_all(cls) -> "RobotsTxt":
        """Used when robots.txt is unreachable (HTTP 5xx, network error)."""
        return cls(default_allow=False)

    @classmethod
    def parse(cls, text: str) -> "RobotsTxt":
        groups: list[_Group] = []
        sitemaps: list[str] = []
        current = None
        last_was_agent = False
        for line in text.lstrip("\ufeff").splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = _Group()
                    groups.append(current)
                token = "*" if value.startswith("*") else product_token(value)
                if token:
                    current.agents.append(token)
                last_was_agent = True
                continue
            if key in ("allow", "disallow"):
                last_was_agent = False
                if current is not None and value:
                    current.rules.append(_Rule(key == "allow", value))
                continue
            if key == "sitemap":
                if value:
                    sitemaps.append(value)
                continue
            last_was_agent = False
        return cls(groups, sitemaps)

    def rules_for(self, user_agent: str) -> list[_Rule] | None:
        """Rules of all groups matching the agent, or None if no group applies."""
        token = product_token(user_agent)
        named = [g for g in self.groups if token and token in g.agents]
        if named:
            return [r for g in named for r in g.rules]
        star = [g for g in self.groups if "*" in g.agents]
        if star:
            return [r for g in star for r in g.rules]
        return None

    def can_fetch(self, user_agent: str, url: str) -> bool:
        parts = urlsplit(url)
        path = parts.path or "/"
        if path == "/robots.txt":
            return True
        if not self.groups:
            return self.default_allow
        if parts.query:
            path += "?" + parts.query
        path = normalize_path(path)
        rules = self.rules_for(user_agent)
        if not rules:
            return True
        best = None
        for rule in rules:
            if not rule.matches(path):
                continue
            if best is None or rule.length > best.length or (
                rule.length == best.length and rule.allow and not best.allow
            ):
                best = rule
        return True if best is None else best.allow
