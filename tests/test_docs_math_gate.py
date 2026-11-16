# tests/test_docs_math_gate.py
"""
Documentation gate: every formula of the built site is typeset.

Chromium (Playwright) opens every page of ``site/`` -- the output of
``mkdocs build`` -- waits until MathJax has typeset it, and fails on

* a MathJax error node (``mjx-merror``): TeX MathJax could not parse;
* TeX source left visible outside code (hidden elements do not count) (``\\frac``, ``\\(``, ``$$``, ...): a
  formula that was never handed to MathJax -- reST ``.. math::`` in a
  docstring, a doubled backslash, a delimiter MathJax does not process there;
* a request for MathJax to another host: the site serves its own copy.

The gate needs a built site and a browser. Without ``site/`` it is skipped;
with ``GI_DOCS_GATE=1`` (the documentation job of CI, after the build) a
missing site or browser is a failure. ``GI_MATH_SCREENS=<dir>`` saves a
screenshot of every page that holds formulas.
"""

import functools
import http.server
import os
import pathlib
import re
import threading
from typing import List

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
GATE = os.environ.get("GI_DOCS_GATE", "").strip() not in ("", "0", "false", "no")

#: TeX that must not be visible as text once MathJax has run.
TEX_SOURCE = re.compile(r"\\(frac|sqrt|mathrm|text\{|sum_|cdot|rho|varphi|underline|qquad)|\\\(|\\\[|\$\$")

#: Elements whose text is code or MathJax output, not prose.
SKIP_TAGS = ("PRE", "CODE", "SCRIPT", "STYLE", "TEXTAREA", "MJX-CONTAINER", "MJX-ASSISTIVE-MML")

_VISIBLE_TEX = """
([pattern, tags]) => {
  const re = new RegExp(pattern);
  const skip = new Set(tags);
  const hits = [];
  const walker = document.createTreeWalker(document.querySelector("article") || document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    let parent = node.parentElement, inside = false;
    while (parent) { if (skip.has(parent.tagName)) { inside = true; break; } parent = parent.parentElement; }
    // Only what a reader sees: hidden copies (mkdocs-jupyter's copy buffer
    // "clipboard-copy-txt") hold the cell source verbatim.
    const shown = node.parentElement && node.parentElement.checkVisibility();
    if (!inside && shown && re.test(node.textContent)) hits.push(node.textContent.trim().slice(0, 80));
  }
  return hits;
}
"""

#: Wait for the page's own typesetting (no extra typeset call: the gate checks
#: what a reader sees, with the containers the page hands to MathJax). The
#: page sets ``window.giMathDone`` when it starts its one typeset per page;
#: a page that never does fails here. The 60 s are a ceiling against a hang,
#: not a speed criterion: on a loaded machine (a fresh ``site/`` being synced)
#: MathJax took more than 10 s to start.
_MERRORS = """
() => [...document.querySelectorAll("mjx-merror")]
  .map(e => e.getAttribute("data-mjx-error") || e.textContent)
"""

_TYPESET = """
async () => {
  for (let k = 0; k < 1200 && window.giMathDone === undefined; k++) {
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  if (window.giMathDone === undefined) {
    throw new Error("the page never typeset its formulas (window.giMathDone is not set)");
  }
  await window.giMathDone;
  return document.querySelectorAll("mjx-container").length;
}
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Static file handler without a log line per request."""

    def log_message(self, format, *args):  # noqa: A002 -- signature of the base class
        pass


class _Server(http.server.ThreadingHTTPServer):
    """
    The site's server, with a listen queue for a browser's parallel connections.

    ``socketserver``'s default queue of 5 overflowed on a loaded machine:
    Chromium opens several connections per page, the accept loop lagged, and
    the request for MathJax itself was reset (``ERR_CONNECTION_RESET``) -- a
    page without formulas that was the server's fault, not the site's.
    """

    request_queue_size = 128
    daemon_threads = True


def _unavailable(reason: str):
    if GATE:
        pytest.fail(f"GI_DOCS_GATE=1: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def site_url():
    if not (SITE / "index.html").exists():
        _unavailable("the site is not built (mkdocs build)")
    handler = functools.partial(_QuietHandler, directory=str(SITE))
    server = _Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("Playwright is not installed")
    with sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 -- any launch failure means no usable browser
            _unavailable(f"Chromium is not available: {exc}")
        yield instance
        instance.close()


def _pages():
    if not SITE.exists():
        return []
    return sorted(p.parent.relative_to(SITE).as_posix() for p in SITE.rglob("index.html")
                  if "search" not in p.parts and not p.parent.name.startswith("_"))


def _check(page, path: str) -> "tuple[int, List[str]]":
    """Typeset count and problems of the page loaded in ``page``."""
    count = page.evaluate(_TYPESET)
    problems = []
    errors = page.evaluate(_MERRORS)
    if errors:
        problems.append(f"{path}: {len(errors)} MathJax error(s): {errors[:3]}")
    visible = page.evaluate(_VISIBLE_TEX, [TEX_SOURCE.pattern, list(SKIP_TAGS)])
    if visible:
        problems.append(f"{path}: TeX source visible: {visible[:3]}")
    return count, problems


#: What the page knows about MathJax when it did not typeset: loaded at all, how far it started.
_MATHJAX_STATE = """
() => ({version: window.MathJax && window.MathJax.version,
        typeset: typeof (window.MathJax && window.MathJax.typesetPromise),
        material: typeof document$ !== "undefined"})
"""


def _check_or_explain(page, path: str, failed: List[str]) -> "tuple[int, List[str]]":
    """:func:`_check`, and on a page that never typeset, what MathJax and the network said."""
    from playwright.sync_api import Error

    try:
        return _check(page, path)
    except Error as exc:
        state = page.evaluate(_MATHJAX_STATE)
        raise AssertionError(f"{path}: {exc}; MathJax state {state}; failed requests {failed[-5:]}") from exc


def test_every_formula_of_the_site_is_typeset(site_url, browser):
    pages = _pages()
    assert len(pages) > 50
    screens = os.environ.get("GI_MATH_SCREENS")
    problems, typeset = [], 0
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    requests, failed = [], []
    page.on("request", lambda r: requests.append(r.url) if "mathjax" in r.url.lower() else None)
    page.on("requestfailed", lambda r: failed.append(f"{r.url} ({r.failure})"))
    for path in pages:
        url = f"{site_url}/{path}/" if path != "." else f"{site_url}/"
        page.goto(url, wait_until="load")
        count, found = _check_or_explain(page, path, failed)
        typeset += count
        problems += found
        if screens and count:
            target = pathlib.Path(screens) / (path.replace("/", "_") or "index")
            target.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=f"{target}.png", full_page=True)
    context.close()
    foreign = sorted({u for u in requests if not u.startswith(site_url)})
    assert foreign == [], f"MathJax loaded from elsewhere: {foreign}"
    assert typeset > 100, "almost no formula was typeset: is MathJax loaded at all?"
    assert problems == [], "\n".join(problems)


#: A page with the site's scripts and one case of everything the gate decides.
_PROBE = """<!doctype html><html><head><meta charset="utf-8"></head><body><article>
<p id="raw">An unconverted formula: \\frac{a}{b}</p>
<p><span class="arithmatex" id="broken">\\(\\frac{a}\\)</span></p>
<p><span class="arithmatex" id="good">\\(Z_E = U_E / I_E\\)</span></p>
<p id="price">A table cell of $5 and $6 stays text.</p>
<div class="jp-RenderedMarkdown"><p id="cell">In a cell $x^2$ is math.</p></div>
<pre><code>print(r"$\\rho$")</code></pre>
<div class="clipboard-copy-txt" style="display:none">ax.set_xlabel(r"$\\rho$")</div>
</article>
<script src="/javascripts/mathjax.js"></script>
<script src="/javascripts/mathjax/tex-mml-chtml.js"></script>
</body></html>"""


def test_the_gate_finds_what_it_is_meant_to_find(site_url, browser):
    page = browser.new_page()
    page.route(f"{site_url}/probe/", lambda route: route.fulfill(body=_PROBE, content_type="text/html"))
    page.goto(f"{site_url}/probe/", wait_until="load")
    count, problems = _check(page, "probe")
    typeset = page.evaluate("() => [...document.querySelectorAll('mjx-container')]"
                            ".map(e => e.closest('[id]') && e.closest('[id]').id)")
    page.close()
    assert sorted(typeset) == ["broken", "cell", "good"]
    assert count == 3
    assert len(problems) == 2
    assert "MathJax error" in problems[0] and "Missing argument" in problems[0]
    assert "TeX source visible" in problems[1] and "frac{a}{b}" in problems[1]


#: A page whose ``document$`` already holds the document when ``mathjax.js`` subscribes -- the
#: order Material produces when the page is parsed before its bundle runs (slow I/O after a
#: fresh build) -- so the subscription fires before MathJax itself is loaded.
_EARLY_DOCUMENT = """<!doctype html><html><head><meta charset="utf-8"></head><body><article>
<p><span class="arithmatex" id="good">\\(Z_E = U_E / I_E\\)</span></p>
</article>
<script>window.document$ = {subscribe(f) { f(document); }};</script>
<script src="/javascripts/mathjax.js"></script>
<script src="/javascripts/mathjax/tex-mml-chtml.js"></script>
</body></html>"""


def test_formulas_are_typeset_when_the_page_is_announced_before_mathjax_loads(site_url, browser):
    page = browser.new_page()
    page.route(f"{site_url}/early/", lambda route: route.fulfill(body=_EARLY_DOCUMENT, content_type="text/html"))
    page.goto(f"{site_url}/early/", wait_until="load")
    count, problems = _check(page, "early")
    page.close()
    assert (count, problems) == (1, [])


def _vendor_script():
    """``scripts/vendor_mathjax.py`` as a module (``scripts/`` is no package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("vendor_mathjax", ROOT / "scripts" / "vendor_mathjax.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_vendored_mathjax_matches_its_checksums():
    assert _vendor_script().check() == []


def test_the_site_loads_mathjax_from_itself_only():
    import yaml

    config = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    scripts = yaml.safe_load("\n".join(line for line in config.splitlines() if "!!python" not in line))
    scripts = scripts["extra_javascript"]
    assert scripts == ["javascripts/mathjax.js", "javascripts/mathjax/tex-mml-chtml.js"]
    assert (ROOT / "docs" / scripts[1]).is_file()
    assert "://" not in (ROOT / "docs" / scripts[0]).read_text(encoding="utf-8")


def test_formulas_are_typeset_after_an_instant_page_change(site_url, browser):
    """Material swaps the page without a reload (navigation.instant); the new
    page's formulas are typeset, and the old page's are not carried over."""
    page = browser.new_page()
    # Material loads instantly only the pages its sitemap lists, by their
    # published address; served here, the site has another one.
    published = re.search(r"^site_url:\s*(\S+)", (ROOT / "mkdocs.yml").read_text(encoding="utf-8"), re.M).group(1)
    sitemap = (SITE / "sitemap.xml").read_text(encoding="utf-8").replace(published.rstrip("/"), site_url)
    page.route(f"{site_url}/sitemap.xml",
               lambda route: route.fulfill(body=sitemap, content_type="application/xml"))
    page.goto(f"{site_url}/reference/glossary/", wait_until="load")
    _check(page, "reference/glossary")
    page.evaluate("() => { window.giMathDone = undefined; window.giSamePage = true; }")
    page.locator("article a[href*='touch-and-transfer']").first.click()
    page.wait_for_url("**/explanation/touch-and-transfer/**")
    count, problems = _check(page, "explanation/touch-and-transfer (instant)")
    same_page = page.evaluate("() => window.giSamePage === true")
    page.close()
    assert same_page, "the link reloaded the page: instant loading is not what was tested"
    assert count > 20 and problems == []
