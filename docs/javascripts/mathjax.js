// MathJax 3 configuration for groundinsight.
//
// We need to cover two sources of math in the site:
//   * Markdown pages processed by pymdownx.arithmatex (generic mode) which
//     emits \(...\) for inline and \[...\] for display math.
//   * Jupyter notebooks rendered through mkdocs-jupyter, where math cells
//     still use the raw $...$ and $$...$$ delimiters.
//
// Enabling both delimiter sets in MathJax 3 is enough to render everything.
window.MathJax = {
  tex: {
    inlineMath: [
      ["\\(", "\\)"],
      ["$", "$"],
    ],
    displayMath: [
      ["\\[", "\\]"],
      ["$$", "$$"],
    ],
    processEscapes: true,
    processEnvironments: true,
  },
  options: {
    // Do not skip any container; arithmatex wraps output in <span class="arithmatex">,
    // while notebook markdown lands in generic <div class="jp-RenderedHTMLCommon">
    // without a dedicated class, so we must allow global processing.
    ignoreHtmlClass: "tex2jax_ignore",
    processHtmlClass: "tex2jax_process",
  },
};

// MkDocs Material uses an instant-loading router that swaps the document body
// without a full page reload. Re-typeset whenever the document has changed.
if (typeof document$ !== "undefined") {
  document$.subscribe(() => {
    if (window.MathJax && window.MathJax.typesetPromise) {
      window.MathJax.typesetPromise();
    }
  });
}
