from critical_css.minify import minify_css


def test_comments_and_whitespace():
    css = "/* header */\nbody  {\n  margin : 0 ;\n  color: red;  /* trailing */\n}\n"
    assert minify_css(css) == "body{margin:0;color:red}"


def test_trailing_semicolons_and_empty_decls():
    assert minify_css("a{color:red;;}b{top:0;}") == "a{color:red}b{top:0}"


def test_strings_are_untouched():
    css = '.q::before { content: "  a ; b { c } /* not a comment */  "; }'
    assert minify_css(css) == '.q::before{content:"  a ; b { c } /* not a comment */  "}'


def test_escaped_quotes_in_strings():
    css = r'.x { content: "say \"hi\" ; ok"; font-family: ' + "'A B', serif; }"
    assert minify_css(css) == r'.x{content:"say \"hi\" ; ok";font-family:' + "'A B',serif}"


def test_unquoted_url_is_preserved():
    css = ".a { background: url( img/a b;c.png ) no-repeat; }"
    assert minify_css(css) == ".a{background:url(img/a b;c.png) no-repeat}"


def test_quoted_url_and_data_uri():
    css = ".a { background: url( \"x y.png\" ); mask: url(data:image/svg+xml;utf8,<svg></svg>); }"
    assert minify_css(css) == '.a{background:url("x y.png");mask:url(data:image/svg+xml;utf8,<svg></svg>)}'


def test_calc_spaces_are_kept():
    css = ".a { width: calc( 100% - (2 * var(--gap)) ); margin: -1px + 2px; }"
    out = minify_css(css)
    assert "calc(100% - (2 * var(--gap)))" in out


def test_selector_combinators_tightened_outside_parens():
    css = "ul  >  li + li ~ p , .a   .b { x: 1 }\nli:nth-child( 2n + 1 ) { y: 2 }"
    assert minify_css(css) == "ul>li+li~p,.a .b{x:1}li:nth-child(2n + 1){y:2}"


def test_descendant_pseudo_space_is_kept():
    # ".a :is(.b)" (descendant) differs from ".a:is(.b)" (same element).
    assert minify_css(".a :is(.b) { c: d }") == ".a :is(.b){c:d}"


def test_media_queries():
    css = "@media screen and ( min-width : 600px ), print { .a { b: c } }"
    assert minify_css(css) == "@media screen and (min-width:600px),print{.a{b:c}}"


def test_important():
    assert minify_css("a { color: red ! important ; }") == "a{color:red!important}"


def test_comment_between_words_keeps_separation():
    assert minify_css("a{margin:1px/**/2px}") == "a{margin:1px 2px}"
    assert minify_css(".a/**/.b{c:d}") == ".a.b{c:d}"


def test_nested_and_keyframes():
    css = "@keyframes x { from { opacity: 0 } to { opacity: 1 } }\n.a { & > .b { c: d } }"
    assert minify_css(css) == "@keyframes x{from{opacity:0}to{opacity:1}}.a{&>.b{c:d}}"
