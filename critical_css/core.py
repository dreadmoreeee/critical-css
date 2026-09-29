"""Orchestration: load, extract per viewport, merge, rewrite, validate."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urljoin

from . import __version__
from .browser import Session, screenshot
from .compare import compare_images
from .cssutil import csp_hash, decode_css, gzip_size
from .extract import EXTRACT_JS, build_css, merge_items
from .fetch import USER_AGENT, Fetcher, LocalServer, Throttle, sniff_encoding
from .htmlrewrite import rewrite_html
from .minify import minify_css

DEFAULT_VIEWPORTS = ((390, 844), (1366, 768))


@dataclass
class Options:
    viewports: tuple = DEFAULT_VIEWPORTS
    validate: bool = True
    threshold: int = 16
    max_diff: float = 0.5
    diff_dir: str | None = None
    delay: float = 1.0
    timeout: float = 30.0
    keep_interactive: bool = False
    user_agent: str = USER_AGENT
    respect_robots: bool = True


@dataclass
class Result:
    page_url: str
    critical_css: str
    html: str
    encoding: str
    report: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        v = self.report.get("validation")
        return True if not v else bool(v["pass"])


def vp_name(vp) -> str:
    return "%dx%d" % tuple(vp)


def run(target: str, options: Options | None = None) -> Result:
    """Extract critical CSS for a URL or a local HTML file."""
    options = options or Options()
    throttle = Throttle(options.delay)
    if target.startswith(("http://", "https://")):
        fetcher = Fetcher(options.user_agent, timeout=options.timeout,
                          respect_robots=options.respect_robots, throttle=throttle)
        page_url, ctype, body = fetcher.get_page(target)
        return _run(page_url, ctype, body, options, throttle)
    path = Path(target).resolve()
    body = path.read_bytes()
    with LocalServer(str(path.parent)) as server:
        page_url = server.base + quote(path.name)
        return _run(page_url, "text/html", body, options, throttle)


def _run(page_url, ctype, body, options, throttle) -> Result:
    from playwright.sync_api import sync_playwright

    encoding = sniff_encoding(ctype, body)
    html_text = body.decode(encoding, "replace")
    content_type = "text/html; charset=%s" % encoding
    per_vp = []
    shots = {}
    with sync_playwright() as pw:
        session = Session(pw, page_url, options.user_agent, throttle, options.timeout)
        try:
            for vp in options.viewports:
                def extract(page, vp=vp):
                    if options.validate:
                        shots[vp] = screenshot(page)
                    cross = {u: decode_css(b) for u, b in session.css.items()}
                    return page.evaluate(EXTRACT_JS, {
                        "keepInteractive": options.keep_interactive,
                        "crossTexts": cross,
                    })
                per_vp.append((vp, session.render(vp, body, content_type, extract)))

            items = merge_items([data["items"] for _, data in per_vp])
            usage = [s for _, data in per_vp for s in data["inlineStyles"]]
            critical = minify_css(build_css(items, page_url, usage))
            new_html, info = rewrite_html(html_text, critical)
            new_body = new_html.encode(encoding, "xmlcharrefreplace")

            blocked = _blocked_urls(page_url, info["links"], per_vp)
            report = _report(page_url, per_vp, session, critical, info, blocked)
            if options.validate:
                report["validation"] = _validate(
                    session, options, shots, new_body, content_type, blocked)
            report["page_loads"] = throttle.count
        finally:
            session.close()
    return Result(page_url, critical, new_html, encoding, report)


def _blocked_urls(page_url, links, per_vp) -> set[str]:
    urls = {urljoin(page_url, href) for href in links}
    for _, data in per_vp:
        for sheet in data["sheets"]:
            if sheet["href"] in urls:
                urls.update(sheet["imports"])
    return urls


def _report(page_url, per_vp, session, critical, info, blocking) -> dict:
    """``blocking``: URLs of the render-blocking stylesheets (and their imports)."""
    sheets = {}
    inline_bytes = 0
    for _, data in per_vp:
        for sheet in data["sheets"]:
            if sheet["inline"] or sheet["href"] not in blocking:
                continue
            for href in [sheet["href"]] + sheet["imports"]:
                if href in session.css:
                    sheets[href] = session.css[href]
    first = per_vp[0][1] if per_vp else {"sheets": []}
    for sheet in first["sheets"]:
        if sheet["inline"]:
            inline_bytes += sheet["bytes"]
    full = b"".join(sheets.values())
    crit_bytes = critical.encode("utf-8")
    unreadable = sorted({u for _, d in per_vp for u in d["unreadable"] if u})
    return {
        "tool": "critical-css",
        "version": __version__,
        "url": page_url,
        "viewports": [
            {
                "viewport": vp_name(vp),
                "rules_kept": d["kept"],
                "rules_total": d["total"],
                "critical_elements": d["criticalElements"],
                "elements": d["elements"],
                "font_families": sorted(d["families"]),
                "animations": sorted(d["anims"]),
            }
            for vp, d in per_vp
        ],
        "critical": {"bytes": len(crit_bytes), "gzip": gzip_size(crit_bytes)},
        "full": {
            "bytes": len(full),
            "gzip": gzip_size(full),
            "stylesheets": [{"url": u, "bytes": len(b)} for u, b in sheets.items()],
        },
        "inline_style_chars": inline_bytes,
        "unreadable_stylesheets": unreadable,
        "deferred_links": info["links"],
        "csp": {
            "style_src": csp_hash(info["style_text"]),
            "onload_handlers": [
                {"handler": h, "hash": csp_hash(h)} for h in info["handlers"]
            ],
        },
    }


def _validate(session, options, shots, new_body, content_type, blocked) -> dict:
    results = []
    for vp in options.viewports:
        name = vp_name(vp)

        def final_shot(page):
            try:
                page.wait_for_function(
                    "() => !document.querySelector('link[media=\"print\"][onload]')",
                    timeout=5000)
            except Exception:
                pass
            return screenshot(page)

        final = session.render(vp, new_body, content_type, final_shot)
        crit = session.render(vp, new_body, content_type, screenshot, block=blocked)
        diff_final = diff_crit = None
        if options.diff_dir:
            os.makedirs(options.diff_dir, exist_ok=True)
            diff_final = os.path.join(options.diff_dir, "diff-%s-final.png" % name)
            diff_crit = os.path.join(options.diff_dir, "diff-%s-critical-only.png" % name)
        c_final = compare_images(shots[vp], final, options.threshold, diff_final)
        c_crit = compare_images(shots[vp], crit, options.threshold, diff_crit)
        ok = c_final["percent"] <= options.max_diff and c_crit["percent"] <= options.max_diff
        results.append({
            "viewport": name,
            "final_diff_percent": c_final["percent"],
            "critical_only_diff_percent": c_crit["percent"],
            "pass": ok,
        })
    return {
        "threshold": options.threshold,
        "max_diff_percent": options.max_diff,
        "blocked_stylesheets": sorted(blocked),
        "results": results,
        "pass": all(r["pass"] for r in results),
    }
