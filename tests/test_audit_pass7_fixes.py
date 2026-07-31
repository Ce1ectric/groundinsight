# tests/test_audit_pass7_fixes.py

"""
Regression tests for the seventh audit-pass bug-fix batch.

Pins the Pass-7-only findings reported in
``applications/Claude Audits/audit-report-changelogs-2026-05-18-pass7.md``
under the *seventh 2026-05-18 review pass* heading for
``groundinsight``. The remaining Pass-6/Pass-7 bullets are already
covered by ``tests/test_audit_pass6_fixes.py``; this module only
covers the new code-level surface introduced for Pass 7.

Findings covered here
---------------------

1. ``groundinsight._set_session`` is the single source of truth for
   the module-level scoped session globals; both ``gi.session`` and
   the historic alias ``gi.db_session`` are kept in lock-step on every
   transition.
2. ``start_dbsession`` and ``close_dbsession`` route through
   ``_set_session`` (verified end-to-end on a real SQLite engine).
3. ``_set_session(None)`` clears both names symmetrically.
4. ``_set_session`` is reachable from the package surface for the
   planned ``swap_dbsession`` helper.
5. ``gi.show_versions()`` returns at least ``{"groundinsight":
   __version__, "python": ..., "platform": ...}``.
6. ``gi.show_versions()`` reports ``groundinsight`` matching
   ``gi.__version__`` exactly (version-parity test from the Pass-7
   Tests-Backlog).
7. ``gi.show_versions()`` includes the peer packages when they are
   importable, gracefully skips them otherwise.
8. ``show_versions`` is listed in ``__all__`` so ``from groundinsight
   import *`` and type-checkers see it.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Iterator

import pytest

import groundinsight as gi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_session() -> Iterator[None]:
    """Ensure the module-level session globals start and end at ``None``."""
    if gi.session is not None or gi.engine is not None:
        gi.close_dbsession()
    yield
    if gi.session is not None or gi.engine is not None:
        gi.close_dbsession()


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "pass7_test.db")


# ---------------------------------------------------------------------------
# Central setter: ``_set_session``
# ---------------------------------------------------------------------------


def test_set_session_is_importable_from_package() -> None:
    """The central setter must be reachable on the package surface so
    future helpers (``swap_dbsession`` ctx manager, test monkeypatching)
    can route through it."""
    assert hasattr(gi, "_set_session")
    assert callable(gi._set_session)


def test_set_session_assigns_both_names_in_lock_step(clean_session) -> None:
    """``_set_session(obj)`` must update ``session`` and ``db_session``
    in a single observable transition."""
    sentinel = object()
    gi._set_session(sentinel)  # type: ignore[arg-type]
    try:
        assert gi.session is sentinel
        assert gi.db_session is sentinel
        assert gi.session is gi.db_session
    finally:
        gi._set_session(None)
    assert gi.session is None
    assert gi.db_session is None


def test_set_session_none_clears_both_aliases(clean_session) -> None:
    """``_set_session(None)`` is the documented clear-path; both
    aliases must drop to ``None`` symmetrically."""
    sentinel = object()
    gi._set_session(sentinel)  # type: ignore[arg-type]
    gi._set_session(None)
    assert gi.session is None
    assert gi.db_session is None


# ---------------------------------------------------------------------------
# ``start_dbsession`` / ``close_dbsession`` route through the central setter
# ---------------------------------------------------------------------------


def test_start_dbsession_binds_both_aliases(
    clean_session, tmp_db_path: str
) -> None:
    """A successful ``start_dbsession`` must leave ``session`` and
    ``db_session`` pointing at the *same* scoped-session object."""
    gi.start_dbsession(tmp_db_path)
    assert gi.session is not None
    assert gi.db_session is not None
    assert gi.session is gi.db_session


def test_close_dbsession_clears_both_aliases(
    clean_session, tmp_db_path: str
) -> None:
    """``close_dbsession`` must reset both aliases to ``None`` without
    leaving an asymmetric state."""
    gi.start_dbsession(tmp_db_path)
    gi.close_dbsession()
    assert gi.session is None
    assert gi.db_session is None


def test_force_rebind_keeps_aliases_in_sync(
    clean_session, tmp_path: Path
) -> None:
    """``start_dbsession(..., force=True)`` reuses the central setter,
    so the alias must follow the new session object — never lag behind
    on the previous one (the exact failure mode flagged in Pass 7)."""
    first = str(tmp_path / "first.db")
    second = str(tmp_path / "second.db")
    gi.start_dbsession(first)
    first_session = gi.session
    gi.start_dbsession(second, force=True)
    assert gi.session is gi.db_session
    assert gi.session is not first_session  # was rebound
    assert gi.session is not None


# ---------------------------------------------------------------------------
# Cross-repo convention: ``show_versions``
# ---------------------------------------------------------------------------


def test_show_versions_in_all() -> None:
    """``show_versions`` must be advertised in ``__all__`` so
    ``from groundinsight import *`` and type-checkers see it."""
    assert "show_versions" in gi.__all__


def test_show_versions_returns_dict_with_groundinsight_key() -> None:
    info = gi.show_versions()
    assert isinstance(info, dict)
    assert "groundinsight" in info
    assert "python" in info
    assert "platform" in info


def test_show_versions_version_parity() -> None:
    """The reported ``groundinsight`` entry must equal
    ``gi.__version__`` exactly (Pass-7 version-parity test)."""
    info = gi.show_versions()
    assert info["groundinsight"] == gi.__version__


def test_show_versions_returns_fresh_dict_each_call() -> None:
    """The helper must be safe to mutate — every call returns a fresh
    mapping, not a shared module-level reference."""
    a = gi.show_versions()
    b = gi.show_versions()
    a["groundinsight"] = "tampered"
    assert b["groundinsight"] == gi.__version__


def test_show_versions_skips_missing_peers(monkeypatch) -> None:
    """When ``groundfield`` / ``groundmeas`` are NOT importable, the
    helper must omit them rather than raise."""

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"groundfield", "groundmeas"}:
            raise ImportError(f"simulated absence of {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    info = gi.show_versions()
    assert "groundfield" not in info
    assert "groundmeas" not in info
    # core entries are still present
    assert info["groundinsight"] == gi.__version__


def test_show_versions_reports_peer_when_present(monkeypatch) -> None:
    """When a peer module is importable and carries a ``__version__``
    attribute, ``show_versions`` must surface it under the package
    name."""
    import types

    fake = types.ModuleType("groundfield")
    fake.__version__ = "9.9.9"
    monkeypatch.setitem(sys.modules, "groundfield", fake)
    info = gi.show_versions()
    assert info.get("groundfield") == "9.9.9"


# ---------------------------------------------------------------------------
# Sanity: the lock-step property survives a fresh import
# ---------------------------------------------------------------------------


def test_fresh_import_starts_with_aliases_aligned() -> None:
    """A clean import must start with ``session`` and ``db_session``
    both bound to ``None`` (the documented quiescent state)."""
    mod = importlib.reload(gi)
    try:
        assert mod.session is None
        assert mod.db_session is None
    finally:
        # leave the live module untouched for the rest of the suite
        if mod.session is not None or mod.engine is not None:
            mod.close_dbsession()
