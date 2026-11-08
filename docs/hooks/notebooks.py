# docs/hooks/notebooks.py
"""
MkDocs build hook: the example notebooks are executed against the documented code.

The example notebooks in ``docs/examples/`` are stored without outputs and
executed by ``mkdocs-jupyter`` while the site is built (``execute: true``,
``allow_errors: false`` in ``mkdocs.yml``), so a published output is always
the output of the code next to it -- stored outputs had drifted from the
code before, with a reduction factor of 0.904 shown where the code gave
0.310. Executing at build time needs three things the plugin does not do by
itself, and this hook provides them:

* **The documented source.** The notebooks run in a separate Jupyter kernel.
  The hook puts the ``src`` directory next to ``mkdocs.yml`` at the front of
  ``PYTHONPATH``, which the kernel inherits, so the notebooks import the same
  ``groundinsight`` that mkdocstrings documents -- not some other installed
  copy.
* **A time limit per cell.** nbclient waits for a cell without limit by
  default, so a hanging cell would hold the build until the CI job is killed.
  ``mkdocs-jupyter`` replaces the ``ExecutePreprocessor`` section of the
  nbconvert configuration with its own, but it loads the configuration files
  first, and a ``NotebookClient`` section applies to the preprocessor as well
  (it is a parent class). The hook adds ``docs/hooks/jupyter`` to
  ``JUPYTER_CONFIG_PATH``; ``jupyter_nbconvert_config.json`` there sets the
  limit.
* **A cache that follows the code.** ``mkdocs-jupyter`` caches a rendered
  notebook under a key made of the notebook file and the plugin options. A
  change to the package does not change that key, so a local rebuild would
  reuse outputs of the old code -- exactly the drift execution at build time
  is meant to end. The hook moves the cache into a directory named after a
  digest of ``src/groundinsight`` and the versions of the numerical
  dependencies. CI starts without a cache and is not affected.

A failure raises, so a strict build stops instead of publishing notebooks
executed against the wrong code. After rendering, the hook also warns when a
notebook page shows a path of the build machine -- the mark of a warning or
traceback a cell did not handle -- which stops a ``--strict`` build too.
"""

import hashlib
import logging
import os
from importlib import metadata
from pathlib import Path
from typing import Dict, Iterable, Optional

logger = logging.getLogger("mkdocs.hooks.notebooks")

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "src"
_JUPYTER_CONFIG = Path(__file__).resolve().parent / "jupyter"

#: Distributions whose version changes what an example prints.
_DEPENDENCIES = ("numpy", "scipy", "sympy", "polars", "pydantic", "matplotlib", "pandapower")

#: Name of the plugin in ``config.plugins``.
_PLUGIN = "mkdocs-jupyter"


def prepend_path(variable: str, directory: Path, environ: Dict[str, str]) -> None:
    """
    Put ``directory`` first on the search path held in ``environ[variable]``.

    Parameters
    ----------
    variable : str
        Name of the environment variable, e.g. ``"PYTHONPATH"``.
    directory : Path
        Directory to put first; an existing entry for it is moved.
    environ : dict
        The environment to change (``os.environ`` in the hook).
    """
    entries = [e for e in environ.get(variable, "").split(os.pathsep) if e]
    entries = [e for e in entries if Path(e).resolve() != directory.resolve()]
    environ[variable] = os.pathsep.join([str(directory)] + entries)


def source_digest(src: Path, dependencies: Iterable[str] = _DEPENDENCIES) -> str:
    """
    A digest of the package source and the versions it computes with.

    Parameters
    ----------
    src : Path
        The ``src`` directory.
    dependencies : iterable of str
        Distribution names whose installed version enters the digest; a
        missing one enters as ``"absent"``.

    Returns
    -------
    str
        The first 16 hex digits of a SHA-256 over every ``*.py`` file under
        ``src/groundinsight`` (path and content) and the versions.
    """
    hasher = hashlib.sha256()
    for path in sorted((src / "groundinsight").rglob("*.py")):
        hasher.update(path.relative_to(src).as_posix().encode())
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
    for name in dependencies:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = "absent"
        hasher.update(f"{name}={version}\0".encode())
    return hasher.hexdigest()[:16]


def cache_directory(default: str, digest: str) -> str:
    """
    The cache directory of ``mkdocs-jupyter`` for one state of the code.

    Parameters
    ----------
    default : str
        The configured ``cache_dir`` of the plugin.
    digest : str
        Result of :func:`source_digest`.

    Returns
    -------
    str
        ``<default>/<digest>``.
    """
    return str(Path(default) / digest)


def on_config(config, **kwargs) -> Optional[dict]:
    """
    Prepare the environment the notebooks are executed in.

    Parameters
    ----------
    config : mkdocs.config.defaults.MkDocsConfig
        The site configuration.
    **kwargs
        Further arguments of the MkDocs event, unused.

    Returns
    -------
    MkDocsConfig
        The configuration, with the plugin's cache directory moved.

    Raises
    ------
    RuntimeError
        If ``src/groundinsight`` or the nbconvert configuration is missing.
    """
    if not (_SRC / "groundinsight" / "__init__.py").is_file():
        raise RuntimeError(f"The documented source tree {_SRC} has no groundinsight package.")
    if not (_JUPYTER_CONFIG / "jupyter_nbconvert_config.json").is_file():
        raise RuntimeError(f"{_JUPYTER_CONFIG} has no jupyter_nbconvert_config.json.")
    prepend_path("PYTHONPATH", _SRC, os.environ)
    prepend_path("JUPYTER_CONFIG_PATH", _JUPYTER_CONFIG, os.environ)
    plugin = config.plugins.get(_PLUGIN)
    if plugin is not None and plugin.config.get("cache"):
        digest = source_digest(_SRC)
        plugin.config["cache_dir"] = cache_directory(plugin.config["cache_dir"], digest)
        logger.info("Notebook cache for this state of the code: %s", plugin.config["cache_dir"])
    return config


def on_post_page(output: str, page, config, **kwargs) -> str:
    """
    Warn when a rendered notebook page shows a path of the build machine.

    A warning that a cell does not handle is printed with the file it was
    raised in, and a traceback lists its frames -- both with the absolute
    path of the checkout. On the published page that is noise at best, and
    it names the machine the site was built on. The notebooks catch their
    warnings and show ``Result.diagnostics`` instead; this check keeps it so.
    Under ``--strict`` the warning stops the build.

    Parameters
    ----------
    output : str
        The rendered page.
    page : mkdocs.structure.pages.Page
        The page.
    config : mkdocs.config.defaults.MkDocsConfig
        The site configuration, unused.
    **kwargs
        Further arguments of the MkDocs event, unused.

    Returns
    -------
    str
        ``output``, unchanged.
    """
    if page.file.src_uri.endswith(".ipynb") and str(_ROOT) in output:
        logger.warning(
            "%s shows a path of the build machine (%s): a warning or traceback "
            "printed by a cell. Catch the warning in the notebook and show "
            "Result.diagnostics instead.",
            page.file.src_uri,
            _ROOT,
        )
    return output
