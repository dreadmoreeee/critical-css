"""Find the CSS rules used above the fold, per viewport, and merge them.

The browser part (``EXTRACT_JS``) runs in the page after it has loaded:

1. Mark "critical" elements: every element whose box starts above the
   bottom of the first viewport (``top < innerHeight``, which also covers
   skip links moved to negative offsets and horizontally off-canvas items),
   hidden children of critical elements (so the rule that hides them is
   kept) and all their ancestors.
2. Collect the font families and animation names they use (including
   ``::before``/``::after``).
3. Walk ``document.styleSheets``. Cross-origin sheets, whose ``cssRules``
   cannot be read, are rebuilt from the response body captured by Python
   (``new CSSStyleSheet().replaceSync(text)``). Style rules are kept when a
   selector, with pseudo-elements removed, matches a critical element;
   ``@media``/``@supports`` are evaluated for the current viewport and kept
   as wrappers; ``@font-face`` and ``@keyframes`` are kept when used.

The Python part merges the per-viewport trees (dedupe by rule position, so
source order is preserved), keeps only custom properties reachable from a
var() use, rebases url() values and serialises the result.
"""

from __future__ import annotations

from .cssutil import (
    filter_declarations,
    rebase_urls,
    split_declarations,
    used_custom_properties,
)

EXTRACT_JS = r"""
(args) => {
  const vh = window.innerHeight;
  const keepInteractive = !!args.keepInteractive;
  const crossTexts = args.crossTexts || {};
  const critical = new Set();
  const root = document.documentElement;
  critical.add(root);
  if (document.body) critical.add(document.body);
  const SKIP = new Set(['SCRIPT', 'STYLE', 'LINK', 'META', 'NOSCRIPT', 'TEMPLATE', 'TITLE', 'BASE']);
  const addUp = (el) => { while (el && !critical.has(el)) { critical.add(el); el = el.parentElement; } };
  const rendered = (el) => el.getClientRects().length > 0 || getComputedStyle(el).display === 'contents';
  const all = document.body ? Array.from(document.body.querySelectorAll('*')) : [];
  for (const el of all) {
    if (SKIP.has(el.tagName)) continue;
    const rects = el.getClientRects();
    let crit = false;
    if (rects.length) {
      let top = Infinity;
      for (const r of rects) top = Math.min(top, r.top);
      crit = top < vh;
    } else {
      const p = el.parentElement;
      crit = !!p && critical.has(p) && rendered(p);
    }
    if (crit) addUp(el);
  }

  const families = new Set();
  const anims = new Set();
  const inlineStyles = [];
  const unquote = (s) => s.trim().replace(/^["']|["']$/g, '').trim().toLowerCase();
  for (const el of critical) {
    const st = el.getAttribute && el.getAttribute('style');
    if (st) inlineStyles.push(st);
    for (const pseudo of [null, '::before', '::after']) {
      const cs = getComputedStyle(el, pseudo);
      if (pseudo && (cs.content === 'none' || cs.content === 'normal')) continue;
      for (const f of (cs.fontFamily || '').split(',')) { const n = unquote(f); if (n) families.add(n); }
      const an = cs.animationName || 'none';
      if (an !== 'none') for (const a of an.split(',')) anims.add(a.trim());
    }
  }

  const splitList = (text) => {
    const out = []; let depth = 0, cur = '', q = null;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) { cur += c; if (c === '\\') { cur += text[++i] || ''; } else if (c === q) q = null; continue; }
      if (c === '"' || c === "'") { q = c; cur += c; continue; }
      if (c === '(' || c === '[') depth++;
      else if (c === ')' || c === ']') depth--;
      if (c === ',' && depth === 0) { out.push(cur.trim()); cur = ''; continue; }
      cur += c;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  };
  const INTERACTIVE = /:(?:hover|active|focus-visible|focus-within|focus|visited|target-within|target)(?![-\w])/i;
  const INTERACTIVE_G = new RegExp(INTERACTIVE.source, 'gi');
  const stripPseudo = (sel) => {
    if (INTERACTIVE.test(sel)) {
      if (!keepInteractive) return null;
      sel = sel.replace(INTERACTIVE_G, '');
    }
    sel = sel.replace(/::[-\w]+(\([^)]*\))?/g, '')
             .replace(/:(?:before|after|first-line|first-letter)(?![-\w])/gi, '');
    if (/^\s*$/.test(sel)) return '*';
    if (/\s$/.test(sel) || /[>+~(,]\s*$/.test(sel)) sel = sel.trimEnd() + ' *';
    return sel.trim();
  };
  const critArr = Array.from(critical);
  const cache = new Map();
  const selMatches = (sel) => {
    if (cache.has(sel)) return cache.get(sel);
    let res;
    const s = stripPseudo(sel);
    if (s === null) res = false;
    else {
      try { res = critArr.some((el) => el.matches(s)); }
      catch (e) { res = true; }  // cannot evaluate: keep, to be safe
    }
    cache.set(sel, res);
    return res;
  };

  const is = (rule, name) => { const C = window[name]; return typeof C === 'function' && rule instanceof C; };
  const mediaOk = (text) => !text || text === 'all' || window.matchMedia(text).matches;
  const headOf = (rule) => { const t = rule.cssText; const i = t.indexOf('{'); return (i < 0 ? t : t.slice(0, i)).trim(); };
  const unreadable = [];
  const rulesOf = (sheet) => {
    try { return sheet.cssRules; } catch (e) {
      const t = crossTexts[sheet.href];
      if (t == null) { unreadable.push(sheet.href); return null; }
      const cs = new CSSStyleSheet();
      try { cs.replaceSync(t); } catch (e2) { unreadable.push(sheet.href); return null; }
      return cs.cssRules;
    }
  };
  const countAll = (rules, hrefs) => {
    let n = 0;
    for (const r of rules) {
      if (is(r, 'CSSStyleRule')) n++;
      if (is(r, 'CSSImportRule')) {
        if (r.styleSheet) {
          if (r.styleSheet.href) hrefs.push(r.styleSheet.href);
          const rs = rulesOf(r.styleSheet);
          if (rs) n += countAll(rs, hrefs);
        }
      } else if (r.cssRules && !is(r, 'CSSStyleRule') && !is(r, 'CSSKeyframesRule')) {
        n += countAll(r.cssRules, hrefs);
      }
    }
    return n;
  };
  let kept = 0;
  const walk = (rules, key, base) => {
    const out = [];
    for (let i = 0; i < rules.length; i++) {
      const r = rules[i];
      const k = key.concat([i]);
      if (is(r, 'CSSStyleRule')) {
        const sels = splitList(r.selectorText);
        const m = [];
        sels.forEach((s, j) => { if (selMatches(s)) m.push(j); });
        if (m.length) {
          kept++;
          const nested = r.cssRules && r.cssRules.length ? r.cssText : null;
          out.push({t: 'style', k, sels, m, body: r.style.cssText, nested, b: base});
        }
      } else if (is(r, 'CSSImportRule')) {
        const sub = r.styleSheet;
        const media = r.media ? r.media.mediaText : '';
        if (!sub || !mediaOk(media)) continue;
        const rs = rulesOf(sub);
        if (!rs) continue;
        let kids = walk(rs, k, sub.href || base);
        if (!kids.length) continue;
        if (r.layerName != null) kids = [{t: 'group', k, head: r.layerName ? '@layer ' + r.layerName : '@layer', kids}];
        if (media && media !== 'all') kids = [{t: 'group', k, head: '@media ' + media, kids}];
        out.push(...kids);
      } else if (is(r, 'CSSMediaRule')) {
        if (!mediaOk(r.media.mediaText)) continue;
        const kids = walk(r.cssRules, k, base);
        if (kids.length) out.push({t: 'group', k, head: '@media ' + r.media.mediaText, kids});
      } else if (is(r, 'CSSSupportsRule')) {
        if (!CSS.supports(r.conditionText)) continue;
        const kids = walk(r.cssRules, k, base);
        if (kids.length) out.push({t: 'group', k, head: '@supports ' + r.conditionText, kids});
      } else if (is(r, 'CSSFontFaceRule')) {
        if (families.has(unquote(r.style.getPropertyValue('font-family')))) out.push({t: 'raw', k, text: r.cssText, b: base});
      } else if (is(r, 'CSSKeyframesRule')) {
        if (anims.has(r.name)) out.push({t: 'raw', k, text: r.cssText, b: base});
      } else if (is(r, 'CSSPropertyRule')) {
        out.push({t: 'prop', k, name: r.name, text: r.cssText});
      } else if (is(r, 'CSSLayerStatementRule') || is(r, 'CSSNamespaceRule') || is(r, 'CSSCounterStyleRule')
                 || is(r, 'CSSFontFeatureValuesRule') || is(r, 'CSSFontPaletteValuesRule')) {
        out.push({t: 'raw', k, text: r.cssText, b: base});
      } else if (is(r, 'CSSPageRule') || is(r, 'CSSStartingStyleRule')) {
        continue;
      } else if (r.cssRules) {
        const kids = walk(r.cssRules, k, base);
        if (kids.length) out.push({t: 'group', k, head: headOf(r), kids});
      }
    }
    return out;
  };

  const items = [];
  const sheets = [];
  let total = 0;
  const list = document.styleSheets;
  for (let s = 0; s < list.length; s++) {
    const sh = list[s];
    const inline = !sh.href;
    const info = {href: sh.href, inline, media: sh.media.mediaText, readable: true, rules: 0, imports: [],
                  bytes: inline && sh.ownerNode ? sh.ownerNode.textContent.length : 0};
    sheets.push(info);
    if (inline && !args.includeInline) continue;
    const rs = rulesOf(sh);
    if (!rs) { info.readable = false; continue; }
    info.rules = countAll(rs, info.imports);
    total += info.rules;
    if (sh.disabled || !mediaOk(info.media)) continue;
    let kids = walk(rs, [s], sh.href || location.href);
    if (kids.length && info.media && info.media !== 'all') kids = [{t: 'group', k: [s], head: '@media ' + info.media, kids}];
    items.push(...kids);
  }
  return {
    items, sheets, total, kept, unreadable, inlineStyles,
    families: Array.from(families), anims: Array.from(anims),
    criticalElements: critical.size, elements: all.length + 2,
    viewport: [window.innerWidth, window.innerHeight],
  };
}
"""


def _key(item) -> tuple:
    return (tuple(item["k"]), item["t"], item.get("head", ""))


def merge_items(lists: list[list[dict]]) -> list[dict]:
    """Union of per-viewport rule trees, in source order."""
    merged: dict[tuple, dict] = {}
    for items in lists:
        for it in items:
            key = _key(it)
            cur = merged.get(key)
            if cur is None:
                cur = dict(it)
                if it["t"] == "group":
                    cur["_lists"] = [it["kids"]]
                merged[key] = cur
            elif it["t"] == "style":
                cur["m"] = sorted(set(cur["m"]) | set(it["m"]))
            elif it["t"] == "group":
                cur["_lists"].append(it["kids"])
    out = []
    for key in sorted(merged, key=lambda k: (k[0], k[1] != "group", k[2])):
        it = merged[key]
        if it["t"] == "group":
            it["kids"] = merge_items(it.pop("_lists"))
        out.append(it)
    return out


def _walk_leaves(items):
    for it in items:
        if it["t"] == "group":
            yield from _walk_leaves(it["kids"])
        else:
            yield it


def build_css(items: list[dict], page_url: str, extra_usage=()) -> str:
    """Serialise merged items into (unminified) CSS text.

    Custom properties are kept only if reachable from a var() use in the
    kept rules, keyframes, font faces or ``extra_usage`` (inline styles).
    """
    usage = list(extra_usage)
    definitions = []
    for it in _walk_leaves(items):
        if it["t"] == "style":
            if it.get("nested"):
                usage.append(it["nested"])
            for name, text in split_declarations(it["body"]):
                if name.startswith("--"):
                    definitions.append((name, text.split(":", 1)[1]))
                else:
                    usage.append(text)
        elif it["t"] == "raw":
            usage.append(it["text"])
    used = used_custom_properties(usage, definitions)
    return "\n".join(_serialise(items, page_url, used))


def _serialise(items, page_url, used):
    for it in items:
        t = it["t"]
        if t == "group":
            inner = list(_serialise(it["kids"], page_url, used))
            if inner:
                yield "%s {\n%s\n}" % (it["head"], "\n".join(inner))
        elif t == "style":
            if it.get("nested"):
                yield rebase_urls(it["nested"], it.get("b"), page_url)
                continue
            body = filter_declarations(it["body"], used)
            if not body:
                continue
            sels = ", ".join(it["sels"][j] for j in it["m"])
            yield "%s { %s }" % (sels, rebase_urls(body, it.get("b"), page_url))
        elif t == "raw":
            yield rebase_urls(it["text"], it.get("b"), page_url)
        elif t == "prop":
            if it["name"] in used:
                yield it["text"]
