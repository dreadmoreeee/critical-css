"""A small, conservative CSS minifier.

It removes comments, collapses whitespace and drops the last semicolon of
each block. Strings and ``url(...)`` values are never touched, spaces inside
parentheses that may be significant (``calc(1px + 2px)``, ``and (``) are
kept, and selector combinators are only tightened outside parentheses.
It does not rewrite values (colours, units, zeros).
"""

from __future__ import annotations

import re

__all__ = ["minify_css"]

_WORD = re.compile(r"[\w-]")
_URL_START = re.compile(r"url\(", re.I)


def _protect(css: str) -> tuple[str, list[str]]:
    """Replace strings, unquoted url() and comments; return text + saved parts."""
    saved: list[str] = []
    out: list[str] = []
    i = 0
    n = len(css)
    start = 0
    while i < n:
        c = css[i]
        if c == "/" and css.startswith("/*", i):
            end = css.find("*/", i + 2)
            end = n if end < 0 else end + 2
            out.append(css[start:i])
            before = css[i - 1] if i else ""
            after = css[end] if end < n else ""
            if before and after and _WORD.match(before) and _WORD.match(after):
                out.append(" ")
            i = start = end
            continue
        if c in "\"'":
            j = i + 1
            while j < n:
                if css[j] == "\\":
                    j += 2
                    continue
                if css[j] == c:
                    j += 1
                    break
                if css[j] == "\n":
                    break
                j += 1
            out.append(css[start:i])
            out.append("\x00%d\x00" % len(saved))
            saved.append(css[i:j])
            i = start = j
            continue
        if c in "uU" and _URL_START.match(css, i) and (i == 0 or not _WORD.match(css[i - 1])):
            j = i + 4
            while j < n and css[j] in " \t\r\n\f":
                j += 1
            if j < n and css[j] not in "\"'":
                end = css.find(")", j)
                end = n if end < 0 else end + 1
                inner = css[j:end - 1].strip() if end <= n else css[j:]
                out.append(css[start:i])
                out.append("\x00%d\x00" % len(saved))
                saved.append("url(" + inner + ")")
                i = start = end
                continue
        i += 1
    out.append(css[start:])
    return "".join(out), saved


def _restore(text: str, saved: list[str]) -> str:
    return re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], text)


def _tighten_combinators(sel: str) -> str:
    """Remove spaces around > + ~ at parenthesis depth 0."""
    out: list[str] = []
    depth = 0
    i = 0
    n = len(sel)
    while i < n:
        c = sel[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        if depth == 0 and c in ">+~":
            while out and out[-1] == " ":
                out.pop()
            out.append(c)
            i += 1
            while i < n and sel[i] == " ":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _prelude(seg: str) -> str:
    seg = seg.strip()
    seg = re.sub(r"\(\s+", "(", seg)
    seg = re.sub(r"\s+\)", ")", seg)
    if seg.startswith("@"):
        # Only inside parentheses: "(min-width: 600px)" -> "(min-width:600px)".
        return re.sub(r"(\([^()]*?)\s*:\s*", r"\1:", seg)
    return _tighten_combinators(seg)


def _declaration(seg: str) -> str:
    seg = seg.strip()
    if not seg or seg.startswith("@"):
        return _prelude(seg) if seg else seg
    m = re.match(r"([^:]+?)\s*:\s*(.*)\Z", seg, re.S)
    if not m:
        return seg
    name, value = m.group(1), m.group(2)
    value = re.sub(r"\(\s+", "(", value)
    value = re.sub(r"\s+\)", ")", value)
    value = re.sub(r"\s*!\s*important\Z", "!important", value, flags=re.I)
    return name + ":" + value


def minify_css(css: str) -> str:
    """Return a minified copy of ``css``."""
    text, saved = _protect(css)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([{};,])\s*", r"\1", text)
    parts = re.split(r"([{};])", text)
    out: list[str] = []
    for idx in range(0, len(parts), 2):
        seg = parts[idx]
        delim = parts[idx + 1] if idx + 1 < len(parts) else ""
        if delim == "{":
            out.append(_prelude(seg))
        else:
            out.append(_declaration(seg))
        out.append(delim)
    text = "".join(out)
    text = re.sub(r";+", ";", text)
    text = text.replace(";}", "}").replace("{;", "{")
    return _restore(text.strip(), saved)
