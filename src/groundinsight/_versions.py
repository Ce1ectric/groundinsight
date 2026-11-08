# _versions.py

"""
Version report of the earthing-platform stack: :func:`show_versions`.

A bug report about a calculation needs the exact versions of the packages
that took part in it -- ``groundinsight`` itself, the Python interpreter, the
platform, and the sister packages ``groundfield`` and ``groundmeas`` when they
are installed. :func:`show_versions` collects them in one call, following the
cross-repository convention the three packages share.

The function lived in the package ``__init__`` until audit pass 19 (ARC3). It
reads ``groundinsight.__version__`` at call time instead of keeping a copy,
because the release script rewrites that one literal and nothing else.
"""

from typing import Dict


def show_versions() -> Dict[str, str]:
    """Return version information for ``groundinsight`` and its peers.

    Cross-repo convention helper introduced for the *seventh
    2026-05-18 review pass* roadmap entry "ADR-0013 — Cross-repo
    `show_versions` convention". The returned mapping always contains
    a ``"groundinsight"`` key with the value of :data:`__version__`;
    optional peers ``"groundfield"`` and ``"groundmeas"`` are added
    when the respective package is installed in the active
    interpreter. Python and platform information are reported under
    the ``"python"`` and ``"platform"`` keys so a bug report can
    include the full earthing-platform stack in a single call.

    Returns
    -------
    dict of str to str
        A mapping ``{"groundinsight": <__version__>, "python": "3.14.6",
        "platform": "Linux-...", ...}``. The first value is always
        :data:`__version__`; no version literal is repeated here so the
        docstring cannot drift away from the release. The dict is freshly
        built on every call and safe to mutate.

    Examples
    --------
    >>> import groundinsight as gi
    >>> info = gi.show_versions()
    >>> info["groundinsight"] == gi.__version__
    True
    """
    import platform
    import sys

    # The package ``__init__`` imports this module before it defines
    # ``__version__``; read it at call time.
    from . import __version__

    info: Dict[str, str] = {
        "groundinsight": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    for peer in ("groundfield", "groundmeas"):
        try:
            mod = __import__(peer)
        except ImportError:
            continue
        peer_version = getattr(mod, "__version__", None)
        if peer_version is not None:
            info[peer] = str(peer_version)
    return info
