"""Rewrite an HTML document: inline critical CSS, load full CSS without blocking.

Each render-blocking ``<link rel="stylesheet">`` becomes::

    <link rel="stylesheet" href="..." media="print" onload="this.media='all'">
    <noscript><link rel="stylesheet" href="..."></noscript>

and ``<style>critical css</style>`` is inserted just before the first such
link (or before ``</head>``), so the cascade order of the original page is
kept once the full stylesheets arrive.
"""

from __future__ import annotations

import html as htmlmod
import re

__all__ = ["find_stylesheet_links", "rewrite_html", "parse_attrs", "StylesheetLink"]

_SKIP_BLOCK_RE = re.compile(
    r"<!--.*?-->|<(script|noscript|template|textarea)\b[^>]*>.*?</\1\s*>",
    re.I | re.S,
)
_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
_ATTR_RE = re.compile(
    r"""([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""",
    re.S,
)


class StylesheetLink:
    def __init__(self, start: int, end: int, tag: str, attrs: list[tuple[str, str | None]]):
        self.start = start
        self.end = end
        self.tag = tag
        self.attrs = attrs

    def get(self, name: str):
        for k, v in self.attrs:
            if k == name:
                return v
        return None

    @property
    def href(self) -> str:
        return htmlmod.unescape(self.get("href") or "")

    @property
    def media(self) -> str:
        return htmlmod.unescape(self.get("media") or "").strip()

    @property
    def blocking(self) -> bool:
        """True when the stylesheet blocks rendering on screen."""
        media = self.media.lower()
        if self.get("disabled") is not None:
            return False
        if media in ("", "all", "screen"):
            return True
        return media not in ("print", "speech", "none")


def parse_attrs(tag: str) -> list[tuple[str, str | None]]:
    inner = re.sub(r"^<link\b", "", tag, flags=re.I).rstrip(">").rstrip("/")
    out = []
    for m in _ATTR_RE.finditer(inner):
        name = m.group(1).lower()
        value = next((g for g in m.group(2, 3, 4) if g is not None), None)
        out.append((name, value))
    return out


def _skipped_spans(doc: str) -> list[tuple[int, int]]:
    return [m.span() for m in _SKIP_BLOCK_RE.finditer(doc)]


def find_stylesheet_links(doc: str) -> list[StylesheetLink]:
    spans = _skipped_spans(doc)
    links = []
    for m in _LINK_RE.finditer(doc):
        if any(a <= m.start() < b for a, b in spans):
            continue
        attrs = parse_attrs(m.group(0))
        rel = ""
        for k, v in attrs:
            if k == "rel":
                rel = (v or "").lower()
        tokens = rel.split()
        if "stylesheet" not in tokens or "alternate" in tokens:
            continue
        link = StylesheetLink(m.start(), m.end(), m.group(0), attrs)
        if link.href:
            links.append(link)
    return links


def _attr_text(attrs) -> str:
    parts = []
    for k, v in attrs:
        if v is None:
            parts.append(k)
        else:
            parts.append('%s="%s"' % (k, v.replace('"', "&quot;")))
    return " ".join(parts)


def onload_handler(media: str) -> str:
    return "this.media='%s'" % (media or "all").replace("\\", "\\\\").replace("'", "\\'")


def rewrite_html(doc: str, critical_css: str) -> tuple[str, dict]:
    """Return ``(new_html, info)``.

    ``info`` has ``links`` (hrefs made non-blocking), ``handlers``
    (distinct onload handler strings) and ``style_text`` (the exact inline
    style body), both needed for CSP hashes.
    """
    links = [link for link in find_stylesheet_links(doc) if link.blocking]
    style_text = critical_css.replace("</style", "<\\/style")
    style_tag = "<style>%s</style>" % style_text
    handlers: list[str] = []
    pieces = []
    pos = 0
    for link in links:
        handler = onload_handler(link.media or "all")
        if handler not in handlers:
            handlers.append(handler)
        attrs = [(k, v) for k, v in link.attrs if k not in ("media", "onload")]
        attrs.append(("media", "print"))
        attrs.append(("onload", handler))
        new_tag = "<link %s>" % _attr_text(attrs)
        pieces.append(doc[pos:link.start])
        pieces.append(new_tag)
        pieces.append("<noscript>%s</noscript>" % link.tag)
        pos = link.end
    pieces.append(doc[pos:])
    out = "".join(pieces)

    if links:
        # Insert before the (rewritten) first link: its offset did not move.
        first = links[0].start
        out = out[:first] + style_tag + out[first:]
    else:
        spans = _skipped_spans(out)
        m = next((m for m in re.finditer(r"</head\s*>", out, re.I)
                  if not any(a <= m.start() < b for a, b in spans)), None)
        if m:
            out = out[:m.start()] + style_tag + out[m.start():]
        else:
            m = re.search(r"<head\b[^>]*>", out, re.I)
            if m:
                out = out[:m.end()] + style_tag + out[m.end():]
            else:
                out = style_tag + out
    return out, {
        "links": [link.href for link in links],
        "handlers": handlers,
        "style_text": style_text,
    }
