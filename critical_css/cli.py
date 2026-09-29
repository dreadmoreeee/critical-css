"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import __version__
from .core import DEFAULT_VIEWPORTS, Options, run
from .fetch import FetchError, USER_AGENT


def _viewport(text: str):
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", text)
    if not m:
        raise argparse.ArgumentTypeError("viewport must look like 390x844")
    return int(m.group(1)), int(m.group(2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="critical-css",
        description="Extract above-the-fold CSS with headless Chromium, inline it "
                    "and check the first paint with a pixel comparison.",
    )
    p.add_argument("target", help="page URL (http/https) or local HTML file")
    p.add_argument("-o", "--out", default="critical-out",
                   help="output directory (default: critical-out)")
    p.add_argument("--viewport", action="append", type=_viewport, metavar="WxH",
                   help="viewport, repeatable (default: 390x844 and 1366x768)")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    p.add_argument("--no-validate", action="store_true", help="skip the pixel comparison")
    p.add_argument("--threshold", type=int, default=16,
                   help="per-channel difference (0-255) that counts as a changed pixel (default: 16)")
    p.add_argument("--max-diff", type=float, default=0.5,
                   help="maximum %% of changed pixels to pass (default: 0.5)")
    p.add_argument("--diff-images", action="store_true",
                   help="save diff PNGs (changed pixels in red) to the output directory")
    p.add_argument("--keep-interactive", action="store_true",
                   help="also keep :hover/:focus/:active/... rules of visible elements")
    p.add_argument("--delay", type=float, default=1.0,
                   help="minimum seconds between page loads (default: 1.0)")
    p.add_argument("--timeout", type=float, default=30.0, help="per-load timeout in seconds")
    p.add_argument("--user-agent", default=USER_AGENT, help="User-Agent header")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    return p


def _num(n: int) -> str:
    return "{:,}".format(n)


def format_report(report: dict, written: list[str]) -> str:
    lines = ["critical-css %s" % report["version"], "page      %s" % report["url"]]
    for v in report["viewports"]:
        lines.append("viewport  %-10s kept %5d / %d style rules  (%d of %d elements above the fold)" % (
            v["viewport"], v["rules_kept"], v["rules_total"],
            v["critical_elements"], v["elements"]))
    c, f = report["critical"], report["full"]
    lines.append("critical  %9s B   gzip %8s B" % (_num(c["bytes"]), _num(c["gzip"])))
    lines.append("full      %9s B   gzip %8s B   (%d render-blocking stylesheet%s)" % (
        _num(f["bytes"]), _num(f["gzip"]), len(f["stylesheets"]),
        "" if len(f["stylesheets"]) == 1 else "s"))
    if f["bytes"]:
        lines.append("ratio     %.1f%% raw, %.1f%% gzip" % (
            100.0 * c["bytes"] / f["bytes"], 100.0 * c["gzip"] / max(f["gzip"], 1)))
    if report["inline_style_chars"]:
        lines.append("inline    %s chars of existing <style> left in place" % _num(report["inline_style_chars"]))
    for u in report["unreadable_stylesheets"]:
        lines.append("warning   could not read stylesheet %s" % u)
    lines.append("CSP       style-src %s" % report["csp"]["style_src"])
    for h in report["csp"]["onload_handlers"]:
        lines.append("          onload %s -> script-src 'unsafe-hashes' %s" % (h["handler"], h["hash"]))
    val = report.get("validation")
    if val:
        lines.append("validate  threshold %d/255 per channel, max %.2f%% differing pixels" % (
            val["threshold"], val["max_diff_percent"]))
        for r in val["results"]:
            lines.append("          %-10s final %6.2f%%   critical-only %6.2f%%   %s" % (
                r["viewport"], r["final_diff_percent"], r["critical_only_diff_percent"],
                "PASS" if r["pass"] else "FAIL"))
        lines.append("result    %s" % ("PASS" if val["pass"] else "FAIL"))
    lines.append("loads     %d (robots.txt, page, renders; >= delay apart)" % report["page_loads"])
    if written:
        lines.append("wrote     %s" % ", ".join(written))
    return "\n".join(lines)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out = Path(args.out)
    options = Options(
        viewports=tuple(args.viewport) if args.viewport else DEFAULT_VIEWPORTS,
        validate=not args.no_validate,
        threshold=args.threshold,
        max_diff=args.max_diff,
        diff_dir=str(out) if args.diff_images else None,
        delay=args.delay,
        timeout=args.timeout,
        keep_interactive=args.keep_interactive,
        user_agent=args.user_agent,
    )
    local = not args.target.startswith(("http://", "https://"))
    if local and (out / Path(args.target).name).resolve() == Path(args.target).resolve():
        print("error: output would overwrite the input file; choose another --out",
              file=sys.stderr)
        return 2
    try:
        result = run(args.target, options)
    except FetchError as err:
        print("error: %s" % err, file=sys.stderr)
        return 2
    except FileNotFoundError as err:
        print("error: %s" % err, file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    html_name = Path(args.target).name if local else "index.html"
    (out / "critical.css").write_text(result.critical_css, encoding="utf-8", newline="")
    (out / html_name).write_bytes(result.html.encode(result.encoding, "xmlcharrefreplace"))
    written = [os.path.join(str(out), n) for n in ("critical.css", html_name, "report.json")]
    report = dict(result.report)
    report["outputs"] = written
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report, written))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
