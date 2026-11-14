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

Audit pass 19 (ARC2) replaced the module globals by ``gi.Database``:
``session`` and ``db_session`` are deprecated aliases that one accessor
resolves from the default database, so findings 1-4 hold by construction and
``_set_session`` is gone. The tests below pin the same lock-step contract
through the aliases; their deprecation itself is tested in
``tests/test_audit_pass19_persistence.py``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Iterator

import pytest

import groundinsight as gi



def _alias(name: str, module=gi):
    """Read a deprecated session alias and assert its ``DeprecationWarning``.

    The aliases are what these tests are about, so every access checks the
    warning instead of silencing it.
    """
    with pytest.deprecated_call(match=rf"groundinsight\.{name} is deprecated"):
        return getattr(module, name)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_session() -> Iterator[None]:
    """Ensure no default database is open before and after the test."""
    gi.close_dbsession()
    yield
    gi.close_dbsession()


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "pass7_test.db")


# ---------------------------------------------------------------------------
# One accessor for both aliases (formerly the central setter ``_set_session``)
# ---------------------------------------------------------------------------


def test_the_central_setter_is_gone_with_the_globals() -> None:
    """Nothing is assigned any more, so there is nothing to keep in step:
    both names are looked up from the default database on access."""
    assert not hasattr(gi, "_set_session")
    assert "session" not in vars(gi)
    assert "db_session" not in vars(gi)


def test_both_aliases_are_none_without_a_session(clean_session) -> None:
    assert _alias("session") is None
    assert _alias("db_session") is None


def test_both_aliases_are_the_same_object_while_a_session_is_active(
    clean_session, tmp_db_path: str
) -> None:
    gi.start_dbsession(tmp_db_path)
    first = _alias("session")
    assert first is not None
    assert _alias("db_session") is first
    assert _alias("session") is first  # stable, not a new object per access


# ---------------------------------------------------------------------------
# ``start_dbsession`` / ``close_dbsession`` keep both aliases in step
# ---------------------------------------------------------------------------


def test_start_dbsession_binds_both_aliases(
    clean_session, tmp_db_path: str
) -> None:
    """A successful ``start_dbsession`` must leave ``session`` and
    ``db_session`` pointing at the *same* scoped-session object."""
    gi.start_dbsession(tmp_db_path)
    assert _alias("session") is not None
    assert _alias("db_session") is not None
    assert _alias("session") is _alias("db_session")


def test_close_dbsession_clears_both_aliases(
    clean_session, tmp_db_path: str
) -> None:
    """``close_dbsession`` must reset both aliases to ``None`` without
    leaving an asymmetric state."""
    gi.start_dbsession(tmp_db_path)
    gi.close_dbsession()
    assert _alias("session") is None
    assert _alias("db_session") is None


def test_force_rebind_keeps_aliases_in_sync(
    clean_session, tmp_path: Path
) -> None:
    """``start_dbsession(..., force=True)`` reuses the central setter,
    so the alias must follow the new session object — never lag behind
    on the previous one (the exact failure mode flagged in Pass 7)."""
    first = str(tmp_path / "first.db")
    second = str(tmp_path / "second.db")
    gi.start_dbsession(first)
    first_session = _alias("session")
    gi.start_dbsession(second, force=True)
    assert _alias("session") is _alias("db_session")
    assert _alias("session") is not first_session  # was rebound
    assert _alias("session") is not None


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


def test_fresh_import_starts_with_aliases_aligned(clean_session) -> None:
    """A clean import must start with ``session`` and ``db_session``
    both bound to ``None`` (the documented quiescent state)."""
    mod = importlib.reload(gi)
    assert _alias("session", mod) is None
    assert _alias("db_session", mod) is None
