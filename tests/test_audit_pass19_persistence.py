# tests/test_audit_pass19_persistence.py

"""
Audit pass 19 -- the JSON project file (GUI1).

GUI1: ``save_network_to_json`` / ``load_network_from_json`` opened the file
without an encoding, so the platform default decided how a name such as
``"Umspannwerk Süd Ω"`` reached the disk -- ``UnicodeEncodeError`` on a
Windows machine (cp1252), mojibake when a file written on macOS was read
there. The write truncated the target first and serialised second, so any
failure in between destroyed the previous project file. And the file carried
no schema version, so a reader could not tell a file of a newer release from
a damaged one.

The tests pin the repaired contract:

* the file is UTF-8 on every platform, independent of the locale;
* a failure while serialising or writing leaves an existing file
  byte-identical and no temporary file behind;
* the file carries ``schema_version`` and ``groundinsight_version`` as
  top-level keys next to the model fields, so ``Network.model_validate_json``
  still reads it, and a file of a newer schema is refused with a clear
  ``ValueError``;
* a file written by 0.6.0 (no metadata, and on Windows not even UTF-8) loads
  unchanged.

Every round trip is checked twice: against the model's own dump, and by
solving the reloaded network again (a second, independent route to the same
numbers).
"""

import io
import json
import locale
import os
import pathlib
import subprocess
import sys
import warnings

import pytest

import groundinsight as gi

DATA = pathlib.Path(__file__).parent / "data"
V060_FILE = DATA / "network_written_by_0.6.0.json"

FREQUENCIES = [50.0, 250.0]
FAULT = "F Straße"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_version() -> int:
    """The schema version this release writes (imported lazily on purpose).

    A module-level import would turn every test of this file into a
    collection error on a checkout without the module; imported here, each
    test fails on its own assertion instead.
    """
    from groundinsight.io.project_file import SCHEMA_VERSION

    return SCHEMA_VERSION


def _network(name: str = "Umspannwerk Süd Ω") -> gi.Network:
    """
    A three-bus radial network whose names need more than ASCII and cp1252.

    ``Ω`` and ``ρ`` are not in cp1252, ``ü``/``ß``/``µ`` are; together they
    cover both failure modes of a locale-dependent encoding.

    Parameters
    ----------
    name : str
        Network name.

    Returns
    -------
    groundinsight.Network
        An unsolved network with one source and the fault ``FAULT``.
    """
    bus_type = gi.BusType(
        name="Erder ρ-abhängig",
        system_type="Substation",
        voltage_level=110.0,
        impedance_formula="rho*0.01 + 1 + I*f*0.002",
    )
    branch_type = gi.BranchType(
        name="Kabel Ω",
        grounding_conductor=True,
        self_impedance_formula="(0.25 + I*f*0.012)*l",
        mutual_impedance_formula="(0.05 + I*f*0.010)*l",
        phase_impedance_formula="(0.10 + I*f*0.004)*l",
    )
    net = gi.create_network(
        name=name, frequencies=FREQUENCIES, description="Prüfnetz – Süd, µ-Test"
    )
    for bus in ("UW Süd Ω", "Station Öl", "Ortsnetz Straße"):
        gi.create_bus(
            name=bus, type=bus_type, specific_earth_resistance=100.0, network=net
        )
    gi.create_branch(
        name="Kabel 1 ρ",
        type=branch_type,
        from_bus="UW Süd Ω",
        to_bus="Station Öl",
        length=1.0,
        network=net,
    )
    gi.create_branch(
        name="Kabel 2",
        type=branch_type,
        from_bus="Station Öl",
        to_bus="Ortsnetz Straße",
        length=0.5,
        network=net,
    )
    gi.create_source(
        name="Einspeisung µ",
        bus="UW Süd Ω",
        values={50.0: 1000.0 + 0.0j, 250.0: 100.0 + 0.0j},
        network=net,
    )
    gi.create_fault(
        name=FAULT,
        bus="Ortsnetz Straße",
        scalings={50.0: 1.0, 250.0: 1.0},
        network=net,
    )
    return net


def _solved_network() -> gi.Network:
    """``_network()`` after ``run_fault``, so the file carries a result."""
    net = _network()
    gi.run_fault(net, FAULT)
    return net


def _epr(network: gi.Network, fault: str) -> dict:
    """
    EPR per bus and frequency, recomputed from the network's inputs.

    Parameters
    ----------
    network : groundinsight.Network
        Network to solve (it is mutated by ``run_fault`` as usual).
    fault : str
        Fault to solve.

    Returns
    -------
    dict
        ``{(bus, frequency): complex}``.
    """
    result = gi.run_fault(network, fault)
    return {
        (bus.name, freq): complex(value.real, value.imag)
        for bus in result.buses
        for freq, value in bus.uepr_freq.items()
    }


def _simulate_cp1252_platform(monkeypatch) -> None:
    """
    Make every implicit text encoding cp1252, as on Windows before Python 3.15.

    ``locale.getpreferredencoding`` is what a reader falls back to for files
    of 0.6.0; ``io.text_encoding`` is what ``pathlib.Path.open`` consults when
    it is not given an encoding, which is how 0.6.0 opened the file.
    """
    monkeypatch.setattr(
        locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252"
    )
    monkeypatch.setattr(
        io,
        "text_encoding",
        lambda encoding, stacklevel=2: "cp1252" if encoding is None else encoding,
    )


def _python(code: str, *args: str, flags=()) -> subprocess.CompletedProcess:
    """
    Run ``code`` in a fresh interpreter that imports this very checkout.

    Parameters
    ----------
    code : str
        Source passed to ``python -c``.
    *args : str
        Arguments available as ``sys.argv[1:]``.
    flags : sequence of str
        Interpreter options placed before ``-c``.

    Returns
    -------
    subprocess.CompletedProcess
        The finished process with captured text output.
    """
    env = dict(os.environ)
    src = str(pathlib.Path(gi.__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (src, env.get("PYTHONPATH", "")) if part
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["MPLBACKEND"] = "Agg"
    return subprocess.run(
        [sys.executable, *flags, "-c", code, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=300,
    )


# ---------------------------------------------------------------------------
# GUI1 -- encoding
# ---------------------------------------------------------------------------


def test_non_ascii_names_round_trip_on_a_cp1252_platform(tmp_path, monkeypatch):
    """The Windows case: ``Ω``/``ρ`` used to raise, ``ü`` became mojibake."""
    net = _solved_network()
    reference_dump = net.model_dump_json()
    _simulate_cp1252_platform(monkeypatch)

    path = tmp_path / "projekt.json"
    gi.save_network_to_json(net, str(path))

    raw = path.read_bytes()
    for name in ("Umspannwerk Süd Ω", "UW Süd Ω", "Erder ρ-abhängig", "Einspeisung µ"):
        assert json.dumps(name, ensure_ascii=False).encode("utf-8") in raw
    raw.decode("utf-8")  # strict: the whole file is UTF-8

    loaded = gi.load_network_from_json(str(path))
    assert loaded.model_dump_json() == reference_dump
    # Second route: the reloaded inputs solve to the stored numbers.
    assert _epr(loaded, FAULT) == pytest.approx(_epr(_network(), FAULT), rel=1e-12)


def test_save_and_load_never_use_an_implicit_encoding(tmp_path):
    """PQ-GUI1: ``-X warn_default_encoding`` with the warning as an error."""
    path = tmp_path / "quelle.json"
    gi.save_network_to_json(_solved_network(), str(path))
    copy = tmp_path / "kopie.json"

    code = (
        "import sys, warnings\n"
        "import groundinsight as gi\n"
        "warnings.simplefilter('error', EncodingWarning)\n"
        "net = gi.load_network_from_json(sys.argv[1])\n"
        "gi.save_network_to_json(net, sys.argv[2])\n"
        "again = gi.load_network_from_json(sys.argv[2])\n"
        "print(again.model_dump_json() == net.model_dump_json())\n"
    )
    proc = _python(code, str(path), str(copy), flags=("-X", "warn_default_encoding"))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip() == "True"
    assert "Süd Ω".encode("utf-8") in copy.read_bytes()


def test_a_cp1252_file_written_by_0_6_0_on_windows_still_loads(tmp_path, monkeypatch):
    """0.6.0 wrote the locale encoding; such a file must keep opening there."""
    text = V060_FILE.read_text(encoding="utf-8")
    path = tmp_path / "windows.json"
    path.write_bytes(text.encode("cp1252"))
    monkeypatch.setattr(
        locale, "getpreferredencoding", lambda do_setlocale=True: "cp1252"
    )

    with pytest.warns(gi.InputWarning) as record:
        loaded = gi.load_network_from_json(str(path))

    codes = [getattr(w.message, "code", None) for w in record]
    assert "PROJECT_FILE_LEGACY_ENCODING" in codes
    assert list(loaded.buses) == ["UW Süd", "Station Öl", "Ortsnetz Straße"]
    reference = gi.Network.model_validate_json(text)
    assert loaded.model_dump_json() == reference.model_dump_json()


def test_a_file_that_is_neither_utf8_nor_the_locale_encoding_is_refused(
    tmp_path, monkeypatch
):
    """No silent mojibake on a UTF-8 platform: a clear error naming the file."""
    path = tmp_path / "kaputt.json"
    path.write_bytes(b'{"name": "S\xfcd", "frequencies": [50.0]}')
    monkeypatch.setattr(
        locale, "getpreferredencoding", lambda do_setlocale=True: "UTF-8"
    )
    with pytest.raises(ValueError) as excinfo:
        gi.load_network_from_json(str(path))
    assert "kaputt.json" in str(excinfo.value)
    assert "UTF-8" in str(excinfo.value)


def test_a_utf8_byte_order_mark_is_accepted(tmp_path):
    """Windows editors may prepend a BOM when a user edits the file by hand."""
    path = tmp_path / "bom.json"
    gi.save_network_to_json(_network(), str(path))
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    loaded = gi.load_network_from_json(str(path))
    assert loaded.model_dump_json() == _network().model_dump_json()


# ---------------------------------------------------------------------------
# GUI1 -- atomic write
# ---------------------------------------------------------------------------


def _existing_project(tmp_path) -> pathlib.Path:
    """A directory holding one previously saved project file."""
    path = tmp_path / "projekt.json"
    gi.save_network_to_json(_network(name="vorher"), str(path))
    return path


def test_a_failing_serialisation_leaves_the_existing_file_byte_identical(
    tmp_path, monkeypatch
):
    path = _existing_project(tmp_path)
    before = path.read_bytes()
    listing = sorted(os.listdir(tmp_path))

    def boom(self, *args, **kwargs):
        raise RuntimeError("serialisation failed")

    monkeypatch.setattr(gi.Network, "model_dump_json", boom)
    with pytest.raises(RuntimeError, match="serialisation failed"):
        gi.save_network_to_json(_network(name="nachher"), str(path))

    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == listing


@pytest.mark.parametrize("failing", ["fsync", "replace"])
def test_a_failing_write_leaves_the_existing_file_byte_identical(
    tmp_path, monkeypatch, failing
):
    path = _existing_project(tmp_path)
    before = path.read_bytes()
    listing = sorted(os.listdir(tmp_path))

    def boom(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, failing, boom)
    with pytest.raises(OSError, match="No space left"):
        gi.save_network_to_json(_network(name="nachher"), str(path))
    monkeypatch.undo()

    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == listing
    assert gi.load_network_from_json(str(path)).name == "vorher"


def test_a_successful_save_replaces_the_file_and_leaves_nothing_else(tmp_path):
    path = _existing_project(tmp_path)
    gi.save_network_to_json(_network(name="nachher"), path)  # pathlib.Path works
    assert os.listdir(tmp_path) == ["projekt.json"]
    assert gi.load_network_from_json(path).name == "nachher"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_permissions_are_those_of_a_plain_write(tmp_path):
    """A temporary file must not leave the project readable by its owner only."""
    path = tmp_path / "neu.json"
    gi.save_network_to_json(_network(), str(path))
    umask = os.umask(0)
    os.umask(umask)
    assert (path.stat().st_mode & 0o777) == (0o666 & ~umask)

    os.chmod(path, 0o640)
    gi.save_network_to_json(_network(name="nachher"), str(path))
    assert (path.stat().st_mode & 0o777) == 0o640


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_a_read_only_project_file_is_not_replaced(tmp_path):
    """``open("w")`` refused a read-only file; replacing it must refuse too."""
    if os.geteuid() == 0:
        pytest.skip("root may write read-only files")
    path = _existing_project(tmp_path)
    before = path.read_bytes()
    os.chmod(path, 0o444)
    try:
        with pytest.raises(PermissionError):
            gi.save_network_to_json(_network(name="nachher"), str(path))
        assert path.read_bytes() == before
    finally:
        os.chmod(path, 0o644)


def test_an_unwritable_target_is_refused_before_anything_is_written(
    tmp_path, monkeypatch
):
    """Same check without relying on the account (root ignores mode bits)."""
    path = _existing_project(tmp_path)
    before = path.read_bytes()
    listing = sorted(os.listdir(tmp_path))
    real_access = os.access

    def access(target, mode, *args, **kwargs):
        if os.fspath(target) == str(path) and mode == os.W_OK:
            return False
        return real_access(target, mode, *args, **kwargs)

    monkeypatch.setattr(os, "access", access)
    with pytest.raises(PermissionError):
        gi.save_network_to_json(_network(name="nachher"), str(path))
    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == listing


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_saving_through_a_symlink_updates_the_linked_file(tmp_path):
    real = _existing_project(tmp_path)
    link = tmp_path / "link.json"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    gi.save_network_to_json(_network(name="nachher"), str(link))
    assert link.is_symlink()
    assert gi.load_network_from_json(str(real)).name == "nachher"


# ---------------------------------------------------------------------------
# GUI1 -- schema version
# ---------------------------------------------------------------------------


def test_the_file_carries_schema_and_package_version_first(tmp_path):
    path = tmp_path / "projekt.json"
    gi.save_network_to_json(_network(), str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload)[:2] == ["schema_version", "groundinsight_version"]
    assert payload["schema_version"] == _schema_version() == 1
    assert isinstance(payload["schema_version"], int)
    assert payload["groundinsight_version"] == gi.__version__


def test_the_rest_of_the_file_is_the_models_own_dump(tmp_path):
    """Two header keys in front of ``model_dump_json(indent=4)`` -- nothing else."""
    net = _solved_network()
    path = tmp_path / "projekt.json"
    gi.save_network_to_json(net, str(path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["schema_version"], payload["groundinsight_version"]
    assert payload == json.loads(net.model_dump_json(indent=4))


def test_model_validate_json_still_reads_a_saved_file(tmp_path):
    """The metadata lives next to the fields; the model ignores unknown keys."""
    net = _solved_network()
    path = tmp_path / "projekt.json"
    gi.save_network_to_json(net, str(path))
    direct = gi.Network.model_validate_json(path.read_text(encoding="utf-8"))
    via_loader = gi.load_network_from_json(str(path))
    assert direct.model_dump_json() == via_loader.model_dump_json()
    assert direct.model_dump_json() == net.model_dump_json()


def test_a_file_written_by_0_6_0_loads_unchanged():
    """The genuine 0.6.0 writer's output (no metadata, solved, with paths)."""
    text = V060_FILE.read_text(encoding="utf-8")
    assert "schema_version" not in json.loads(text)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no warning for an ordinary old file
        loaded = gi.load_network_from_json(str(V060_FILE))

    reference = gi.Network.model_validate_json(text)
    assert loaded.model_dump_json() == reference.model_dump_json()
    # The stored result is the one the inputs produce (independent solve).
    stored = {
        (bus.name, freq): complex(value.real, value.imag)
        for bus in loaded.results[FAULT].buses
        for freq, value in bus.uepr_freq.items()
    }
    assert _epr(loaded, FAULT) == pytest.approx(stored, rel=1e-9)


def _write_with_metadata(path: pathlib.Path, **metadata) -> None:
    """Write ``_network()`` with the given top-level metadata values."""
    payload = json.loads(_network().model_dump_json())
    payload = {**metadata, **payload}
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_newer_schema_version_is_refused_with_a_clear_message(tmp_path):
    path = tmp_path / "zukunft.json"
    newer = _schema_version() + 1
    _write_with_metadata(
        path, schema_version=newer, groundinsight_version="2099.1.0"
    )
    with pytest.raises(ValueError) as excinfo:
        gi.load_network_from_json(str(path))
    message = str(excinfo.value)
    assert f"schema version {newer}" in message
    assert "2099.1.0" in message
    assert "zukunft.json" in message
    assert "update" in message.lower()


@pytest.mark.parametrize("bad", ["1", True, 0, -1, 1.5, [1]])
def test_an_invalid_schema_version_is_refused(tmp_path, bad):
    path = tmp_path / "kaputt.json"
    _write_with_metadata(path, schema_version=bad, groundinsight_version="x")
    with pytest.raises(ValueError, match="schema_version"):
        gi.load_network_from_json(str(path))


def test_the_current_schema_version_loads(tmp_path):
    path = tmp_path / "aktuell.json"
    _write_with_metadata(
        path,
        schema_version=_schema_version(),
        groundinsight_version="0.0.0-anything",
    )
    assert gi.load_network_from_json(str(path)).name == "Umspannwerk Süd Ω"


@pytest.mark.parametrize(
    "content, expected",
    [
        ("{not json", "not a valid JSON file"),
        ("[1, 2, 3]", "top-level JSON value"),
    ],
)
def test_malformed_files_are_reported_with_their_path(tmp_path, content, expected):
    path = tmp_path / "defekt.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as excinfo:
        gi.load_network_from_json(str(path))
    assert expected in str(excinfo.value)
    assert "defekt.json" in str(excinfo.value)


@pytest.mark.parametrize(
    "builder",
    ["solved", "open_end", "thevenin", "rlc"],
)
def test_loader_and_model_validate_json_agree(tmp_path, builder):
    """The loader validates the parsed dict; JSON mode must give the same model.

    Covers the value shapes that serialise specially: ``inf`` impedances of an
    open end (written as strings), a Thévenin source, lumped RLC fields.
    """
    net = _network()
    if builder == "open_end":
        open_type = gi.BusType(
            name="offen",
            system_type="Substation",
            voltage_level=20.0,
            impedance_formula="nan",
        )
        gi.create_bus(name="Ende", type=open_type, network=net)
    elif builder == "thevenin":
        gi.create_voltage_source(
            name="Thévenin",
            bus="Station Öl",
            voltage={50.0: 1000.0 + 0.0j, 250.0: 10.0 + 0.0j},
            source_impedance={50.0: 0.5 + 0.1j, 250.0: 0.5 + 0.5j},
            network=net,
        )
    elif builder == "rlc":
        rlc_type = gi.BusType(
            name="RLC",
            system_type="Substation",
            voltage_level=20.0,
            impedance_formula="rho*0 + 5",
            R_formula="rho*0 + 5",
            L_formula="rho*0 + 1e-6",
            C_formula="rho*0 + 1e-9",
        )
        gi.create_bus(name="RLC-Bus", type=rlc_type, network=net)
    if builder == "solved":
        gi.run_fault(net, FAULT)

    path = tmp_path / f"{builder}.json"
    gi.save_network_to_json(net, str(path))
    text = path.read_text(encoding="utf-8")
    via_loader = gi.load_network_from_json(str(path))
    via_json_mode = gi.Network.model_validate_json(text)
    assert via_loader.model_dump_json() == via_json_mode.model_dump_json()
    assert via_loader.model_dump_json() == net.model_dump_json()
