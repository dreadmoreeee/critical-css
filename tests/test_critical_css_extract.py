"""End-to-end tests against the local fixture site (headless Chromium)."""

import json
import re

import pytest
from PIL import Image

from critical_css import Options, run
from critical_css.cli import main
from critical_css.cssutil import csp_hash


@pytest.fixture(scope="module")
def result(fixture_site, tmp_path_factory):
    out = tmp_path_factory.mktemp("critical_css_diffs")
    res = run(fixture_site["url"], Options(delay=0, diff_dir=str(out)))
    res.diff_dir = out
    return res


def test_kept_rules(result):
    css = result.critical_css
    for needle in [
        "html{font-size:16px}",
        "body{margin:0px;",
        ".skip-link{",            # off-screen element starting above the fold
        ".mobile-menu{display:none}",  # rule that hides an above-the-fold element
        ".hero-title::before{",   # pseudo-element of a visible element
        ".hero{background:var(--brand-dark)",
        ".ext-banner{",           # cross-origin stylesheet
        "@supports (display:grid){.grid{",
    ]:
        assert needle in css, needle


def test_dropped_rules(result):
    css = result.critical_css
    for needle in [".btn:hover", ".below", ".footer", "Footer Serif", "@keyframes spin",
                   ".footer-spinner", ".ext-footer", "print", ".below-wide", ".inline-note"]:
        assert needle not in css, needle


def test_selector_list_reduced(result):
    assert "h1{font-size:32px" in result.critical_css
    assert ".footer h2" not in result.critical_css


def test_media_blocks_wrapped_per_viewport(result):
    css = result.critical_css
    assert "@media (min-width:1000px){.hero{padding:48px}.nav{display:flex;gap:24px}}" in css
    assert "@media (max-width:600px){.hero-title{font-size:24px}}" in css
    kept = {v["viewport"]: v["rules_kept"] for v in result.report["viewports"]}
    assert kept["390x844"] != kept["1366x768"]


def test_font_face_and_keyframes(result):
    css = result.critical_css
    assert css.count("@font-face") == 1
    assert '@font-face{font-family:"Fixture Sans"' in css
    assert 'url("fonts/fixture-sans.woff2")' in css  # rebased from css/ to the page
    assert "@keyframes fade-in{" in css
    vp = result.report["viewports"][0]
    assert "fixture sans" in vp["font_families"] and "fade-in" in vp["animations"]


def test_custom_properties_resolved(result):
    root = re.search(r":root\{([^}]*)\}", result.critical_css).group(1)
    names = sorted(re.findall(r"(--[\w-]+):", root))
    # --brand via .ext-banner, --brand-dark/--brand-deep via var() chain,
    # --accent via an inline style attribute, --space via .hero and calc().
    assert names == ["--accent", "--brand", "--brand-dark", "--brand-deep", "--space"]


def test_cross_origin_url_absolute(result, fixture_site):
    assert 'url("%s/banner-bg.png")' % fixture_site["ext_origin"] in result.critical_css


def test_html_rewrite(result):
    html = result.html
    assert html.count("<style>") == 2  # the page's own inline style is left in place
    assert '<link rel="stylesheet" href="css/main.css" media="print" onload="this.media=\'all\'">' in html
    assert '<noscript><link rel="stylesheet" href="css/main.css"></noscript>' in html
    assert '<link rel="stylesheet" href="css/print.css" media="print">' in html
    assert html.index("<style>:root") < html.index('href="css/main.css"')


def test_csp_hash_of_inline_style(result):
    body = re.search(r"<style>(:root.*?)</style>", result.html, re.S).group(1)
    assert body == result.critical_css
    assert result.report["csp"]["style_src"] == csp_hash(body)
    assert result.report["csp"]["onload_handlers"][0]["handler"] == "this.media='all'"


def test_sizes(result):
    rep = result.report
    assert rep["critical"]["bytes"] == len(result.critical_css.encode())
    assert 0 < rep["critical"]["bytes"] < rep["full"]["bytes"]
    urls = [s["url"] for s in rep["full"]["stylesheets"]]
    assert len(urls) == 2 and not any("print.css" in u for u in urls)


def test_pixel_compare_passes(result):
    val = result.report["validation"]
    assert val["pass"] is True
    assert len(val["blocked_stylesheets"]) == 2
    for r in val["results"]:
        assert r["critical_only_diff_percent"] <= 0.5
        assert r["final_diff_percent"] <= 0.5
    diff = Image.open(result.diff_dir / "diff-390x844-critical-only.png")
    assert diff.size == (390, 844)


def test_blocked_check_detects_missing_critical_css(fixture_site, result):
    # Same pipeline with an empty critical CSS: with the full stylesheets
    # blocked, the first viewport must differ a lot from the original.
    from playwright.sync_api import sync_playwright

    from critical_css.browser import Session, screenshot
    from critical_css.compare import compare_images
    from critical_css.fetch import Throttle
    from critical_css.htmlrewrite import rewrite_html

    original = (fixture_site["root"] / "index.html").read_bytes()
    empty, _ = rewrite_html(original.decode("utf-8"), "")
    blocked = set(result.report["validation"]["blocked_stylesheets"])
    ctype = "text/html; charset=utf-8"
    with sync_playwright() as pw:
        session = Session(pw, fixture_site["url"], "critical-css-test", Throttle(0))
        try:
            a = session.render((390, 844), original, ctype, screenshot)
            b = session.render((390, 844), empty.encode("utf-8"), ctype, screenshot, block=blocked)
        finally:
            session.close()
    assert compare_images(a, b)["percent"] > 5.0


def test_cli_local_file_json(fixture_site, tmp_path, capsys):
    # Local-file mode: the file is served from its folder on a random port.
    # Only same-origin assets are reachable offline, so drop the external link.
    page = fixture_site["root"] / "local.html"
    text = (fixture_site["root"] / "index.html").read_text(encoding="utf-8")
    page.write_text(re.sub(r'<link rel="stylesheet" href="http[^"]+">\n', "", text), encoding="utf-8")
    out = tmp_path / "out"
    code = main([str(page), "-o", str(out), "--json", "--delay", "0",
                 "--viewport", "390x844", "--threshold", "16"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["validation"]["pass"] is True
    assert [v["viewport"] for v in report["viewports"]] == ["390x844"]
    assert (out / "critical.css").read_text(encoding="utf-8").startswith(":root{")
    assert "media=\"print\" onload" in (out / "local.html").read_text(encoding="utf-8")
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["url"].endswith("/local.html")
