# _logging.py

"""
The package's logging default and the console switch :func:`set_log_level`.

A library must not print anything unless the application asks for it.
Importing this module attaches a single :class:`logging.NullHandler` to the
``groundinsight`` package logger, below which every module of the package
logs (``logging.getLogger(__name__)``); applications and notebooks opt in to
log output by configuring :mod:`logging` themselves or by calling
:func:`set_log_level`.

This code lived in the package ``__init__`` until audit pass 19 (ARC3). The
``__init__`` now only imports and re-exports; it imports this module first,
so the handler is in place before any other module of the package can log.
Unlike the old in-``__init__`` setup, re-importing the package (for example
with :func:`importlib.reload`) does not stack a second ``NullHandler``.
"""

import logging
from typing import Union

#: Name of the package logger; every module of the package logs below it.
PACKAGE_LOGGER = "groundinsight"

# Sentinel attribute on a StreamHandler so we can recognise the one
# installed by :func:`set_log_level` and dedupe accidental duplicates.
_GI_HANDLER_MARK = "_groundinsight_console_handler"


def _install_null_handler() -> logging.Logger:
    """
    Attach the library's ``NullHandler`` to the package logger, once.

    Returns
    -------
    logging.Logger
        The ``groundinsight`` package logger.
    """
    package_logger = logging.getLogger(PACKAGE_LOGGER)
    if not any(isinstance(h, logging.NullHandler) for h in package_logger.handlers):
        package_logger.addHandler(logging.NullHandler())
    return package_logger


_install_null_handler()


def set_log_level(level: Union[int, str] = "INFO") -> logging.Logger:
    """
    Enable console logging for the ``groundinsight`` package.

    Convenience helper for interactive use (notebooks, scripts). Attaches a
    single :class:`logging.StreamHandler` with a simple formatter to the
    ``groundinsight`` package logger and sets the requested level.
    Subsequent calls only adjust the level on the *same* handler instance,
    so it is safe to call this helper repeatedly without accumulating
    duplicate console handlers — even across mixed
    ``set_log_level("DEBUG") -> set_log_level("INFO")`` toggles, and even
    when ``logging.basicConfig(...)`` has been called at the root logger.

    Parameters
    ----------
    level : int or str, optional
        Log level either as a numeric constant (e.g. ``logging.INFO``) or
        as a string name (``"DEBUG"``, ``"INFO"``, ``"WARNING"``,
        ``"ERROR"``, ``"CRITICAL"``). Defaults to ``"INFO"``.

    Returns
    -------
    logging.Logger
        The configured ``groundinsight`` package logger, for further
        adjustments by the caller if desired.

    Notes
    -----
    If you have also called :func:`logging.basicConfig` at the root logger
    level, every record will be emitted twice: once through the package
    handler installed here and once through the root handler installed by
    ``basicConfig``. To suppress the duplicate either call
    ``logging.getLogger("groundinsight").propagate = False`` explicitly,
    or rely solely on ``basicConfig`` and do not call
    :func:`set_log_level`.

    Examples
    --------
    >>> import groundinsight as gi
    >>> gi.set_log_level("INFO")  # doctest: +ELLIPSIS
    <Logger groundinsight (INFO)>
    """
    pkg_logger = logging.getLogger(PACKAGE_LOGGER)
    pkg_logger.setLevel(level)

    # Collect every console-style handler installed previously, keep the
    # marked one, drop any stragglers that may have been attached by an
    # earlier (pre-0.5) version of this helper or by user code.
    marked = [
        h for h in pkg_logger.handlers if getattr(h, _GI_HANDLER_MARK, False)
    ]
    for h in list(pkg_logger.handlers):
        if (
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.NullHandler)
            and not getattr(h, _GI_HANDLER_MARK, False)
        ):
            pkg_logger.removeHandler(h)

    if marked:
        # Re-use the existing marked handler — just align its level so
        # filtering happens at both the logger and the handler level.
        handler = marked[0]
        # Defensive: if for some reason multiple marked handlers are
        # present (e.g. from a previous buggy release running in the same
        # interpreter), keep only the first.
        for extra in marked[1:]:
            pkg_logger.removeHandler(extra)
        handler.setLevel(level)
    else:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(levelname)s [%(name)s] %(message)s")
        )
        handler.setLevel(level)
        setattr(handler, _GI_HANDLER_MARK, True)
        pkg_logger.addHandler(handler)

    return pkg_logger
