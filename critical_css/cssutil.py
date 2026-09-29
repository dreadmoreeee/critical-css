"""Helpers for CSS text: declarations, custom properties, url() rebasing, sizes."""

from __future__ import annotations

import base64
import gzip
import hashlib
import posixpath
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

__all__ = [
    "split_declarations",
    "var_refs",
    "used_custom_properties",
    "filter_declarations",
    "rebase_urls",
    "rebase_url",
    "gzip_size",
    "csp_hash",
    "decode_css",
]

_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)")
_URL_RE = re.compile(
    r"""url\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|([^)\s"']*))\s*\)""",
    re.I | re.S,
)


def split_declarations(body: str) -> list[tuple[str, str]]:
    """Split a declaration block into ``(name, full_text)`` pairs.

    Semicolons inside strings, parentheses and brackets are respected.
    ``name`` is lower-cased except for custom properties (case-sensitive).
    """
    out: list[tuple[str, str]] = []
    depth = 0
    quote = None
    cur: list[str] = []
    i = 0
    n = len(body)
    while i < n:
        c = body[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < n:
                cur.append(body[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth == 0:
            _add_decl(out, "".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    _add_decl(out, "".join(cur))
    return out


def _add_decl(out, text):
    text = text.strip()
    if not text or ":" not in text:
        return
    name = text.split(":", 1)[0].strip()
    if not name.startswith("--"):
        name = name.lower()
    out.append((name, text))


def var_refs(text: str) -> set[str]:
    """Names of custom properties referenced with var() in ``text``."""
    return set(_VAR_RE.findall(text))


def used_custom_properties(usage_texts, definitions) -> set[str]:
    """Resolve var() chains.

    ``usage_texts``: texts whose var() references are real uses (normal
    declarations, keyframes, inline style attributes ...).
    ``definitions``: iterable of ``(name, value_text)`` custom property
    definitions; a definition only counts once its own name is used.
    """
    defs: dict[str, list[str]] = {}
    for name, value in definitions:
        defs.setdefault(name, []).append(value)
    used: set[str] = set()
    for text in usage_texts:
        used |= var_refs(text)
    todo = list(used)
    while todo:
        name = todo.pop()
        for value in defs.get(name, ()):
            for ref in var_refs(value):
                if ref not in used:
                    used.add(ref)
                    todo.append(ref)
    return used


def filter_declarations(body: str, used: set[str]) -> str:
    """Drop custom property declarations that are not in ``used``."""
    kept = [
        text for name, text in split_declarations(body)
        if not name.startswith("--") or name in used
    ]
    return "; ".join(kept) + (";" if kept else "")


def rebase_url(url: str, base: str, page_url: str) -> str:
    """Make ``url`` (relative to ``base``) work from ``page_url``.

    Same-origin targets become paths relative to the page, others absolute.
    """
    raw = url.strip()
    if not raw or raw.startswith("#") or re.match(r"(?i)(data|about|blob|javascript):", raw):
        return url
    target = urljoin(base, raw)
    t = urlsplit(target)
    p = urlsplit(page_url)
    if (t.scheme, t.netloc) != (p.scheme, p.netloc):
        return target
    page_dir = p.path if p.path.endswith("/") else posixpath.dirname(p.path)
    page_dir = page_dir or "/"
    rel = posixpath.relpath(t.path or "/", page_dir)
    if t.path.endswith("/") and not rel.endswith("/"):
        rel += "/"
    return urlunsplit(("", "", rel, t.query, t.fragment))


def rebase_urls(text: str, base: str | None, page_url: str) -> str:
    """Rebase every url(...) in ``text`` from ``base`` to ``page_url``."""
    if not base or base == page_url:
        return text

    def repl(m):
        value = m.group(1) if m.group(1) is not None else (
            m.group(2) if m.group(2) is not None else m.group(3))
        new = rebase_url(value, base, page_url)
        if new == value:
            return m.group(0)
        return 'url("%s")' % new.replace("\\", "\\\\").replace('"', '\\"')

    return _URL_RE.sub(repl, text)


def gzip_size(data: bytes | str) -> int:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return len(gzip.compress(data, compresslevel=9, mtime=0))


def csp_hash(text: str) -> str:
    """CSP source expression for an inline <style>/<script> body or handler."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "'sha256-%s'" % base64.b64encode(digest).decode("ascii")


def decode_css(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    m = re.match(rb'@charset "([^"]+)";', data)
    if m:
        try:
            return data.decode(m.group(1).decode("ascii"), "replace")
        except LookupError:
            pass
    return data.decode("utf-8", "replace")
