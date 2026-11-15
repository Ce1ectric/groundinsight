# gui/tests/test_rules.py
"""
Architecture rules of the GUI distribution (GUI phase 0).

* R1 -- the GUI imports ``groundinsight`` and ``groundinsight.api`` only: a
  callback that reaches into ``groundinsight.models`` or the solver is the
  first step towards physics in the front end.
* R8 -- the service layer, which lives in the library, imports no UI toolkit
  (also enforced in the library suite; repeated here because this is the job
  that installs one).
* AG Grid Community only: the Enterprise modules need a commercial licence,
  and NiceGUI makes them one keyword away.
* Every UI string exists in German and English.
* ``python -m groundinsight_gui --help`` works and names ``--native``.
"""

import ast
import os
import pathlib
import subprocess
import sys

GUI_ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = GUI_ROOT / "groundinsight_gui"
API = GUI_ROOT.parent / "src" / "groundinsight" / "api"

ALLOWED = {"groundinsight", "groundinsight.api"}
UI_TOOLKITS = ("nicegui", "webview", "fastapi", "starlette", "PySide6", "flet", "panel")


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


def test_the_gui_imports_only_the_service_layer_and_the_public_namespace():
    files = sorted(PACKAGE.rglob("*.py"))
    assert {p.name for p in files} >= {"__init__.py", "__main__.py", "app.py", "i18n.py", "formatting.py"}
    offenders = [
        f"{path.name}: {name}"
        for path in files
        for name in _imports(path)
        if name.split(".")[0] == "groundinsight" and name not in ALLOWED
    ]
    assert offenders == []


def test_the_service_layer_imports_no_ui_toolkit():
    offenders = [
        f"{path.name}: {name}"
        for path in sorted(API.glob("*.py"))
        for name in _imports(path)
        if name.split(".")[0] in UI_TOOLKITS
    ]
    assert API.is_dir() and offenders == []


def test_only_the_community_grid_is_used():
    for path in sorted(PACKAGE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "enterprise" not in source.lower(), path.name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "aggrid":
                assert all(k.arg != "modules" for k in node.keywords), path.name


def test_every_ui_string_exists_in_both_languages():
    from groundinsight_gui.i18n import LANGUAGES, missing_keys

    assert LANGUAGES == ("de", "en")
    assert missing_keys() == []


def test_the_module_starts_with_help():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(GUI_ROOT), env.get("PYTHONPATH")]))
    completed = subprocess.run(
        [sys.executable, "-m", "groundinsight_gui", "--help"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--native" in completed.stdout and "--lang" in completed.stdout


def test_the_command_line_options_are_parsed():
    from groundinsight_gui.__main__ import parse_options

    options = parse_options(["--native", "--port", "8123", "--lang", "en", "--example", "mv_ring"])
    assert options.native and options.port == 8123
    assert options.app.language == "en" and options.app.start_example == "mv_ring"
    defaults = parse_options([])
    assert not defaults.native and defaults.host == "127.0.0.1" and defaults.app.language == "de"


def _shown_columns():
    """Every column name the GUI shows as a header or a property label."""
    import warnings

    from groundinsight.api import KeyFigureRowDTO, ProjectSession
    from groundinsight.io import TABLE_SCHEMAS

    names = {column for schema in TABLE_SCHEMAS.values() for column in schema}
    session = ProjectSession.from_example("mv_ring")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = session.solve("F1")
    names |= {c.name for c in result.buses.columns} | {c.name for c in result.branches.columns}
    names |= set(KeyFigureRowDTO.model_fields) - {"dto_schema_version"}
    names |= {"rho_Ohm_m", "frequency_Hz", "Z_Ohm", "Z_degree", "R_Ohm", "X_Ohm", "k_touch", "k_transfer"}
    session.close()
    return names


def test_people_read_no_underscores():
    """Identifiers with ``_`` are for code; headers, labels and texts get words."""
    from groundinsight.api import EXAMPLES

    from groundinsight_gui.i18n import COLUMN_LABELS, LANGUAGES, STRINGS, column_label, template_label

    for language in LANGUAGES:
        unlabelled = sorted(c for c in _shown_columns() if c not in COLUMN_LABELS[language])
        assert unlabelled == [], (language, unlabelled)
        headers = {c: column_label(language, c) for c in _shown_columns()}
        assert [c for c, h in headers.items() if "_" in h] == []
        assert [k for k, v in STRINGS[language].items() if "_" in v] == []
        assert ["_" in template_label(language, name) for name in EXAMPLES] == [False] * len(EXAMPLES)
    assert column_label("de", "some_new_column") == "some new column"
