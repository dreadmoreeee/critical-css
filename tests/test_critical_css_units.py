import base64
import hashlib

from PIL import Image

from critical_css.compare import compare_images
from critical_css.cssutil import (
    csp_hash,
    filter_declarations,
    rebase_url,
    rebase_urls,
    split_declarations,
    used_custom_properties,
)
from critical_css.extract import build_css, merge_items
from critical_css.htmlrewrite import rewrite_html
from critical_css.rfc9309 import RobotsTxt


def test_split_declarations_respects_strings_and_parens():
    body = 'content: "a;b"; background: url(data:x;y); --x: { a; b }; color: red;'
    names = [n for n, _ in split_declarations(body)]
    assert names == ["content", "background", "--x", "color"]


def test_var_chain_resolution():
    defs = [("--a", "var(--b)"), ("--b", "var(--c, 1px)"), ("--c", "2px"), ("--z", "var(--a)")]
    used = used_custom_properties(["width: var(--a)"], defs)
    assert used == {"--a", "--b", "--c"}


def test_filter_declarations():
    body = "--keep: 1px; --drop: 2px; width: var(--keep);"
    assert filter_declarations(body, {"--keep"}) == "--keep: 1px; width: var(--keep);"


def test_rebase_url():
    page = "https://example.com/shop/index.html"
    assert rebase_url("../img/a.png", "https://example.com/css/main.css", page) == "../img/a.png"
    assert rebase_url("a.png", "https://example.com/shop/css/x.css", page) == "css/a.png"
    assert rebase_url("f.woff2", "https://cdn.example.ca/f/x.css", page) == "https://cdn.example.ca/f/f.woff2"
    assert rebase_url("data:image/png;base64,AA", "https://cdn.example.ca/x.css", page) == "data:image/png;base64,AA"
    out = rebase_urls('a{b:url("../i.png") url(j.png)}', "https://example.com/css/m.css", page)
    assert out == 'a{b:url("../i.png") url("../css/j.png")}'


def test_csp_hash_matches_manual_sha256():
    text = "body{margin:0}"
    expected = base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
    assert csp_hash(text) == "'sha256-%s'" % expected


def test_rewrite_html_links_noscript_and_order():
    doc = (
        "<html><head><title>t</title>\n"
        "<!-- <link rel=\"stylesheet\" href=\"commented.css\"> -->\n"
        "<link rel=\"stylesheet\" href=\"a.css\">\n"
        "<link rel=stylesheet href='b.css?v=1&amp;x=2' media=\"screen and (min-width: 600px)\">\n"
        "<link rel=\"stylesheet\" href=\"p.css\" media=\"print\">\n"
        "<link rel=\"alternate stylesheet\" href=\"alt.css\">\n"
        "</head><body></body></html>"
    )
    out, info = rewrite_html(doc, "body{margin:0}")
    assert info["links"] == ["a.css", "b.css?v=1&x=2"]
    assert out.index("<style>body{margin:0}</style>") < out.index('href="a.css"')
    assert '<link rel="stylesheet" href="a.css" media="print" onload="this.media=\'all\'">' in out
    assert '<noscript><link rel="stylesheet" href="a.css"></noscript>' in out
    assert "onload=\"this.media='screen and (min-width: 600px)'\"" in out
    assert "href=\"b.css?v=1&amp;x=2\"" in out
    assert '<link rel="stylesheet" href="p.css" media="print">' in out  # untouched
    assert "commented.css\"> -->" in out
    assert info["handlers"] == ["this.media='all'", "this.media='screen and (min-width: 600px)'"]
    assert info["style_text"] == "body{margin:0}"


def test_rewrite_html_without_links_goes_before_head_end():
    out, info = rewrite_html("<html><head><title>x</title></head><body>", "a{b:c}")
    assert out == "<html><head><title>x</title><style>a{b:c}</style></head><body>"
    assert info["links"] == []


def test_merge_keeps_source_order_and_unions_selectors():
    mobile = [
        {"t": "style", "k": [0, 1], "sels": ["h1", ".x"], "m": [0], "body": "a: b;", "nested": None, "b": None},
        {"t": "group", "k": [0, 5], "head": "@media (max-width: 600px)", "kids": [
            {"t": "style", "k": [0, 5, 0], "sels": [".m"], "m": [0], "body": "c: d;", "nested": None, "b": None}]},
    ]
    desktop = [
        {"t": "style", "k": [0, 0], "sels": ["html"], "m": [0], "body": "e: f;", "nested": None, "b": None},
        {"t": "style", "k": [0, 1], "sels": ["h1", ".x"], "m": [1], "body": "a: b;", "nested": None, "b": None},
    ]
    merged = merge_items([mobile, desktop])
    assert [tuple(i["k"]) for i in merged] == [(0, 0), (0, 1), (0, 5)]
    assert merged[1]["m"] == [0, 1]
    css = build_css(merged, "https://example.com/")
    assert css.index("html") < css.index("h1, .x") < css.index("@media")


def test_compare_images_threshold_and_diff(tmp_path):
    a = Image.new("RGB", (10, 10), (100, 100, 100))
    b = a.copy()
    b.putpixel((0, 0), (110, 100, 100))   # below threshold
    b.putpixel((1, 0), (200, 100, 100))   # above threshold
    res = compare_images(a, b, threshold=16, diff_path=str(tmp_path / "d.png"))
    assert res["differing"] == 1 and res["percent"] == 1.0
    diff = Image.open(tmp_path / "d.png").convert("RGB")
    assert diff.getpixel((1, 0)) == (230, 0, 0)


def test_rfc9309_longest_match_and_wildcards():
    robots = RobotsTxt.parse(
        "User-agent: *\nDisallow: /private\nAllow: /private/ok\nDisallow: /*.pdf$\n\n"
        "User-agent: critical-css\nDisallow: /nocss/\n"
    )
    ua = "critical-css/1.0 (+https://github.com/dreadmoreeee/critical-css)"
    assert robots.can_fetch(ua, "https://example.com/private") is True  # own group applies
    assert robots.can_fetch(ua, "https://example.com/nocss/x") is False
    other = "otherbot/2.0"
    assert robots.can_fetch(other, "https://example.com/private/x") is False
    assert robots.can_fetch(other, "https://example.com/private/ok/x") is True
    assert robots.can_fetch(other, "https://example.com/a.pdf") is False
    assert robots.can_fetch(other, "https://example.com/a.pdf?x") is True
    assert robots.can_fetch(other, "https://example.com/robots.txt") is True


def test_rfc9309_tie_allow_and_percent_encoding():
    robots = RobotsTxt.parse("User-agent: *\nDisallow: /a%3cb\nAllow: /page\nDisallow: /page\n")
    assert robots.can_fetch("x", "https://example.com/page") is True
    assert robots.can_fetch("x", "https://example.com/a%3Cb") is False
    assert RobotsTxt.disallow_all().can_fetch("x", "https://example.com/") is False
    assert RobotsTxt.allow_all().can_fetch("x", "https://example.com/") is True
