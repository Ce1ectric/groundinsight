// MathJax 3 configuration for groundinsight.
//
// Where math is typeset -- and only there:
//   * ".arithmatex": Markdown pages and API docstrings go through
//     pymdownx.arithmatex (generic mode), which wraps every formula in an
//     element of this class with \(...\) or \[...\] delimiters;
//   * ".jp-RenderedMarkdown", ".jp-RenderedLatex": notebooks rendered by
//     mkdocs-jupyter keep $...$ and $$...$$ in their markdown cells and LaTeX
//     outputs.
// MathJax is handed exactly these containers (startup.elements and every
// typeset call), so a dollar sign in a table, a code block or a log line
// elsewhere is never read as math: the $ delimiters act only inside notebook
// cells. Material's own recipe (ignoreHtmlClass ".*|" plus processHtmlClass)
// does not reach these: its ignore pattern also matches the empty class of
// every <p> inside a notebook cell, so only text sitting directly in the
// container would be typeset.
//
// MathJax itself is served from docs/javascripts/mathjax/ (vendored by
// scripts/vendor_mathjax.py), not from a CDN.
window.giMathElements = [".arithmatex", ".jp-RenderedMarkdown", ".jp-RenderedLatex"];

window.MathJax = {
  tex: {
    inlineMath: [["\\(", "\\)"], ["$", "$"]],
    displayMath: [["\\[", "\\]"], ["$$", "$$"]],
    processEscapes: true,
    processEnvironments: true,
  },
  startup: {
    elements: window.giMathElements,
    // No typesetting of its own: on the site Material's document$ (below)
    // typesets every page, the first one included. Two typesets racing each
    // other -- MathJax's at startup, then a clear and a new one when
    // document$ fires -- showed the TeX source for a moment and could leave a
    // page half done. Without Material (a page of its own) pageReady typesets.
    typeset: false,
    pageReady() {
      return MathJax.startup.defaultPageReady().then(() => {
        if (typeof document$ === "undefined") {
          window.giMathDone = MathJax.typesetPromise(window.giMathElements);
        }
      });
    },
  },
};

// Material's instant loading swaps the page without a reload: clear MathJax's
// caches and typeset the new page (otherwise formulas of the previous page
// leak into the new one's numbering and cache). document$ also fires for the
// first page, possibly before MathJax has started; every typeset is therefore
// queued on MathJax.startup.promise, as MathJax documents. window.giMathDone
// is the promise of the last typeset (the documentation test waits for it).
if (typeof document$ !== "undefined") {
  document$.subscribe(() => {
    if (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise) {
      window.giMathDone = MathJax.startup.promise = MathJax.startup.promise.then(() => {
        MathJax.startup.output.clearCache();
        MathJax.typesetClear();
        MathJax.texReset();
        return MathJax.typesetPromise(window.giMathElements);
      });
    }
  });
}
