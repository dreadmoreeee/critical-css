# critical-css

Extract the critical (above-the-fold) CSS of a page with headless Chromium,
inline it, load the full stylesheets without blocking rendering, and then
**check the result with a pixel comparison of the first paint**.

Most critical-CSS tools stop at "here is some CSS". This one renders the
rewritten page with the full stylesheets *blocked*, so what you see in the
comparison is exactly what a visitor sees before the full CSS arrives.

- Works on a URL or a local HTML file, at several viewports (default
  390x844 and 1366x768), merged into one `critical.css`.
- Keeps `@media` / `@supports` blocks that apply, `@font-face` only for
  families used above the fold, `@keyframes` only for running animations,
  and only the custom properties actually reachable through `var()` chains.
- Reads cross-origin stylesheets too (their CSSOM is locked, so the tool
  rebuilds them from the downloaded text).
- Prints the CSP `sha256-` hashes you need for the inline style and the
  `onload` swap.
- Polite by default: GET/HEAD only, robots.txt (RFC 9309), 1 s between page
  loads, every asset downloaded once per run.

## Install

```
pip install .
python -m playwright install chromium
```

Requires Python 3.10+, [Playwright](https://playwright.dev/python/) and
Pillow. It also runs without installing: `python -m critical_css ...` from
this folder.

## Usage

```
critical-css https://www.example.com/ -o out
critical-css public/index.html -o out --diff-images
critical-css https://www.example.com/ --viewport 360x740 --viewport 1920x1080 --json
```

Outputs in the `-o` folder:

| file | content |
|---|---|
| `critical.css` | merged, minified critical CSS |
| `index.html` (or the input file name) | the page with the critical CSS inlined and the full stylesheets loaded non-blocking |
| `report.json` | sizes, rules per viewport, CSP hashes, validation |
| `diff-<viewport>-*.png` | with `--diff-images`: changed pixels in red |

The rewritten `<head>` looks like this:

```html
<style>/* critical css */</style>
<link rel="stylesheet" href="/css/site.css" media="print" onload="this.media='all'">
<noscript><link rel="stylesheet" href="/css/site.css"></noscript>
```

Exit code: `0` pass, `1` the pixel comparison failed, `2` fetch error or
robots.txt disallows the page.

From Python:

```python
from critical_css import Options, run

result = run("https://www.example.com/", Options(delay=1.0))
print(result.critical_css)
print(result.report["validation"]["pass"])
```

## Options

| option | default | meaning |
|---|---|---|
| `-o, --out DIR` | `critical-out` | output folder |
| `--viewport WxH` | `390x844`, `1366x768` | repeatable |
| `--json` | off | print the report as JSON |
| `--no-validate` | off | skip the pixel comparison |
| `--threshold N` | `16` | per-channel difference (0-255) that makes a pixel "changed" |
| `--max-diff P` | `0.5` | maximum % of changed pixels to pass |
| `--diff-images` | off | save diff PNGs |
| `--keep-interactive` | off | keep `:hover`, `:focus`, `:active`... rules of visible elements |
| `--delay S` | `1.0` | minimum seconds between page loads |
| `--timeout S` | `30` | per-load timeout |
| `--user-agent UA` | `critical-css/1.0 (+https://github.com/dreadmoreeee/critical-css)` | |

## How it works

1. **Load.** The page is fetched once (robots.txt checked with an RFC 9309
   matcher, `/cdn-cgi/` skipped). Every browser render is then served from
   that copy through a request interceptor, which also caches assets,
   records stylesheet bodies and refuses anything that is not GET/HEAD.
   A local file is served from its folder on a random port.
2. **Above-the-fold elements.** For each viewport, an element is critical
   when its box starts above the bottom of the first viewport. That also
   catches skip links moved to `left:-9999px` and off-canvas menus, whose
   rules must be present or they would show up in the first paint. Hidden
   (`display:none`) children of critical elements count too, for the same
   reason. Ancestors of critical elements are always critical.
3. **CSSOM walk.** Each selector of each style rule is tested with
   `element.matches()` after removing pseudo-elements (so `.btn::before` is
   kept when `.btn` is visible). Selector lists are reduced to the parts
   that match. Rules that need user interaction (`:hover`, `:focus`...) are
   dropped unless `--keep-interactive`. `@media` and `@supports` are
   evaluated for the viewport and kept as wrappers. `@font-face` is kept for
   families in the computed `font-family` of critical elements (including
   `::before`/`::after`), `@keyframes` for their `animation-name`.
4. **Merge.** The viewports are merged by rule position, so there are no
   duplicates and the source order is kept. Custom properties are filtered:
   a `--name` survives only if a kept declaration, keyframe, font face or
   inline `style` attribute reaches it through `var()`, following chains.
   `url()` values are rebased from the stylesheet to the page.
5. **Minify.** A small minifier: comments, whitespace, last semicolons.
   Strings, `url()` and spaces inside `calc()` are left alone.
6. **Rewrite.** The `<style>` goes right before the first render-blocking
   `<link>`, so the cascade order matches the original page. Existing
   inline `<style>` blocks and `media="print"` links are left untouched.
7. **Validate.** For every viewport, three screenshots of the first
   viewport: the original page, the rewritten page after the swap, and the
   rewritten page **with the full stylesheets blocked**. The last one is the
   meaningful check: it is the first paint with critical CSS only. A pixel
   is changed when any channel differs by more than `--threshold`; the page
   passes when both comparisons stay under `--max-diff` percent.
   Animations are frozen for the screenshots.

## CSP

A strict Content-Security-Policy blocks inline styles and inline event
handlers. The report prints what to allow:

```
CSP       style-src 'sha256-...'
          onload this.media='all' -> script-src 'unsafe-hashes' 'sha256-MhtPZXr7+LpJUY5qtMutB+qWfQtMaPccfe7QXtCcEYc='
```

- `style-src 'sha256-...'` is the SHA-256 of the exact text between
  `<style>` and `</style>`. It changes every time the critical CSS changes,
  so regenerate the header (or the `<meta>` tag) together with the page.
- The `onload="this.media='all'"` swap is an inline event handler. Hashes
  only apply to handlers with `'unsafe-hashes'` in `script-src` (or
  `script-src-attr`). That hash is constant. If you would rather not allow
  it, swap the media from an external script instead; the `<noscript>`
  fallback still works.

## Limitations

- Rules are serialised from Chrome's CSSOM, not copied from your source:
  values are normalised (`0` becomes `0px`, colours become `rgb()`), and
  declarations Chrome does not understand (other vendors' prefixes, hacks)
  are gone. The output targets Chromium-based rendering first.
- `@import` inside a cross-origin stylesheet is not followed (the rebuilt
  sheet cannot import). Shadow DOM styles and `adoptedStyleSheets` are not
  extracted.
- Content that changes after load (sliders, cookie banners, A/B tests,
  lazy-loaded fonts) can make the comparison noisy; raise `--threshold` or
  `--max-diff`, or look at the diff images.
- Classes added by JavaScript after the viewport check are not seen.
- A pixel match proves the first viewport looks the same at the tested
  viewports only. Other sizes can still shift a little until the full CSS
  arrives.
- robots.txt is checked for the page URL. Assets are loaded by the
  browser as for a normal visit.

## Measured result

Offline run on 2026-09-29 against two of my own production pages; the live
sites were not changed. Each run used 8 loads (robots.txt, the page, six
renders), at least 1 s apart. The output-path line is omitted.

```
$ python -m critical_css https://demarkstudio.ca/en/ -o out/demark --diff-images
critical-css 1.0.0
page      https://demarkstudio.ca/en/
viewport  390x844    kept    93 / 945 style rules  (40 of 949 elements above the fold)
viewport  1366x768   kept    80 / 945 style rules  (42 of 949 elements above the fold)
critical     11,944 B   gzip    3,507 B
full         93,705 B   gzip   17,718 B   (2 render-blocking stylesheets)
ratio     12.7% raw, 19.8% gzip
CSP       style-src 'sha256-niXgKtbIC2CSLIJIyL4BMJjISfXpWCQ1jKW4MdaoHFI='
          onload this.media='all' -> script-src 'unsafe-hashes' 'sha256-MhtPZXr7+LpJUY5qtMutB+qWfQtMaPccfe7QXtCcEYc='
validate  threshold 16/255 per channel, max 0.50% differing pixels
          390x844    final   0.00%   critical-only   0.00%   PASS
          1366x768   final   0.00%   critical-only   0.00%   PASS
result    PASS
loads     8 (robots.txt, page, renders; >= delay apart)

$ python -m critical_css https://demo.demarkstudio.ca/riverside-pub/site -o out/pub --diff-images
critical-css 1.0.0
page      https://demo.demarkstudio.ca/riverside-pub/site
viewport  390x844    kept    56 / 647 style rules  (40 of 175 elements above the fold)
viewport  1366x768   kept    60 / 647 style rules  (37 of 175 elements above the fold)
critical      9,973 B   gzip    2,839 B
full         66,514 B   gzip   12,864 B   (1 render-blocking stylesheet)
ratio     15.0% raw, 22.1% gzip
inline    211 chars of existing <style> left in place
CSP       style-src 'sha256-z1HRxfawB4FOjPmO5a/hNTIWJBZCy6OeAgL7DSan+7c='
          onload this.media='all' -> script-src 'unsafe-hashes' 'sha256-MhtPZXr7+LpJUY5qtMutB+qWfQtMaPccfe7QXtCcEYc='
validate  threshold 16/255 per channel, max 0.50% differing pixels
          390x844    final   0.00%   critical-only   0.00%   PASS
          1366x768   final   0.00%   critical-only   0.00%   PASS
result    PASS
loads     8 (robots.txt, page, renders; >= delay apart)
```

In both cases, blocking the full stylesheets
(`sitio-*.css` + `borrador-*.css` on the first site, `negocio-lienzo-*.css`
on the second) left the first viewport pixel-identical at both sizes. The
render-blocking CSS drops from 17.7 kB to 3.5 kB gzip on the first page and
from 12.9 kB to 2.8 kB gzip on the second. The critical CSS is about 10% of
the style rules. The tests include a control run: with an empty critical CSS
and the stylesheets blocked, the same comparison reports more than 5%
changed pixels, so a 0.00% result is not a false pass.

Tests (no network; the fixture site and a second origin for the
cross-origin stylesheet are served by `http.server` on random ports):

```
$ python -m pytest -q -p no:cacheprovider --import-mode=importlib critical-css
37 passed in 13.81s
```

---

Marvin Palencia, founder of [DeMark Studio](https://demarkstudio.ca), Miramichi, New Brunswick, Canada. Portfolio: [marvin.demarkstudio.ca](https://marvin.demarkstudio.ca)

MIT License.
