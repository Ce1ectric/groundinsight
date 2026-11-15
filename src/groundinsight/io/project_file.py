# io/project_file.py

"""
The JSON project file: one network per file, UTF-8, written atomically.

A project file is what :func:`save_project` (and :func:`save_network_to_json`)
writes and :func:`load_project` (and :func:`load_network_from_json`) reads:
two header keys and three sections::

    {
        "schema_version": 2,
        "groundinsight_version": "2026.12.0",
        "network": { ... the network's model_dump_json ... },
        "diagram": { ... the Diagram, or null ... },
        "views": [ ... the evaluation tabs of a front end ... ]
    }

``network`` is the physical model, ``diagram`` where its elements are drawn
(:class:`~groundinsight.Diagram`, kept apart so a moved bus does not touch
the network), ``views`` the evaluation tabs a front end defines -- a list of
JSON objects the library stores and returns unchanged.

Three properties are guaranteed, because a desktop application and a server
open and save these files on every platform:

Encoding
    The file is UTF-8 on every platform. Without an explicit encoding the
    locale decides: on Windows (cp1252) a name containing ``Ω`` or ``ρ``
    raises ``UnicodeEncodeError`` on save, and a file with ``"Süd"`` written
    on macOS comes back as mojibake. Reading
    accepts a UTF-8 byte-order mark, which Windows editors add when a file is
    edited by hand. A file that is *not* UTF-8 can only have been written by
    0.6.0 or earlier with a non-UTF-8 locale; it is decoded with the
    platform's preferred encoding -- exactly what 0.6.0 did on that machine --
    and an :class:`~groundinsight.InputWarning` with the code
    ``PROJECT_FILE_LEGACY_ENCODING`` says so. Saving it again writes UTF-8.

Atomicity
    The network is serialised completely *before* anything touches the disk,
    then written to a temporary file in the target directory, flushed and
    ``fsync``-ed, and moved over the target with :func:`os.replace`. A failure
    at any point -- an exception in the serialiser, a full disk, a crash --
    leaves an existing file byte-identical and removes the temporary file.
    The 0.6.0 writer truncated the target first, so the same failures left an
    empty or half-written project. The replaced file keeps the permission
    bits of the old one (a new file gets the usual ``0o666 & ~umask``), a
    read-only target is refused like ``open("w")`` refuses it, and a
    symbolic link is followed so the link keeps pointing at the saved file.

Schema version
    ``schema_version`` (an ``int``) is the version of the file layout;
    ``groundinsight_version`` (a ``str``) names the release that wrote it.
    A reader refuses a ``schema_version`` newer than :data:`SCHEMA_VERSION`
    with a :class:`ValueError` that names both numbers, instead of silently
    dropping whatever the newer release added. Every older layout is read:
    version 1 (2026.11.x) and files without the keys (0.6.0 and earlier) hold
    the network's fields at the top level, next to the header keys, and load
    as a project without diagram and views.

Why version 2 is an envelope
----------------------------
Version 1 kept the network's fields at the top level, next to the header
keys, so that ``Network.model_validate_json`` -- and a 0.6.0 reader, which
knows no schema version -- could still open a project file. Version 2 holds
what those readers must not open silently: an earthing characteristic
changes the bus impedances, and a reader that drops it computes other
numbers without a word. In the envelope a 0.6.0 reader and
``Network.model_validate_json`` fail on the missing network fields instead,
and 2026.11.x refuses the newer schema version (maintainer decision). A
script that read project files with ``Network.model_validate_json`` uses
:func:`load_network_from_json` instead.

When to increment :data:`SCHEMA_VERSION`
----------------------------------------
Whenever a release writes something an earlier release would ignore or
misread -- a new field that changes a result, a changed meaning of an
existing one, a different shape of a stored value. The older release then
refuses the file instead of computing with a silently reduced model. A change
that older releases read correctly (a new optional field they may safely
ignore) does not need a new version.

The validation itself is the model's: after the metadata check the parsed
object goes through ``Network.model_validate``, the same validators and
formula guard as every other way of building a network. (Validating the
parsed dictionary gives the same model as ``Network.model_validate_json`` on
the text -- the tests compare both on every value shape that serialises
specially -- and is faster, because the file is parsed once.)
"""

import codecs
import contextlib
import errno
import json
import locale
import logging
import os
import secrets
import stat
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from ..diagnostics import InputWarning, emit
from ..diagram import Diagram
from ..models.core_models import Network

logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 2
"""Version of the project-file layout this release writes, and the newest one
it reads. See the module docstring for when it is incremented."""

METADATA_KEYS: Tuple[str, ...] = ("schema_version", "groundinsight_version")
"""The top-level keys of a project file that are file metadata, in the order
they are written."""

_PathLike = Union[str, "os.PathLike[str]"]

SECTIONS: Tuple[str, ...] = ("network", "diagram", "views")
"""The sections of a version-2 project file, in the order they are written."""


class Project(BaseModel):
    """
    The content of a project file: a network, its diagram and its views.

    Attributes
    ----------
    network : Network
        The physical model, with any stored results.
    diagram : Diagram, optional
        Where the elements are drawn; ``None`` for a network that has not
        been drawn (a front end places it automatically).
    views : list of dict
        The evaluation tabs of a front end, as JSON objects; stored and
        returned unchanged.
    schema_version : int, optional
        The layout version of the file it was read from; ``None`` for a file
        without one (0.6.0 or earlier) and for a project built in memory.
    groundinsight_version : str, optional
        The release that wrote the file, as stored.

    Examples
    --------
    >>> import groundinsight as gi
    >>> project = gi.Project(network=gi.create_network(name="n", frequencies=[50.0]))
    >>> project.diagram is None, project.views
    (True, [])
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    network: Network
    diagram: Optional[Diagram] = None
    views: List[Dict[str, Any]] = Field(default_factory=list)
    schema_version: Optional[int] = None
    groundinsight_version: Optional[Any] = None


def save_project(
    path: _PathLike,
    network: Network,
    diagram: Optional[Diagram] = None,
    views: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Save a network with its diagram and views, atomically and as UTF-8.

    Parameters
    ----------
    path : str or os.PathLike
        Target file. An existing file is replaced as a whole or not at all;
        if ``path`` is a symbolic link, the file it points to is replaced.
    network : Network
        The network, saved with its stored results.
    diagram : Diagram, optional
        Its diagram; ``None`` writes ``null``.
    views : list of dict, optional
        The evaluation tabs; JSON objects. ``None`` writes ``[]``.

    Raises
    ------
    OSError
        If the file cannot be written; an existing file is then unchanged.
    TypeError
        If a view is not a JSON object or holds values JSON cannot store.

    Examples
    --------
    >>> import pathlib, tempfile
    >>> import groundinsight as gi
    >>> path = pathlib.Path(tempfile.mkdtemp()) / "sued.json"
    >>> net = gi.create_network(name="Süd", frequencies=[50.0])
    >>> diagram = gi.Diagram(nodes={"B1": gi.DiagramNode(x=0, y=0)})
    >>> gi.save_project(path, net, diagram, views=[{"title": "EPR"}])
    >>> gi.load_project(path).diagram == diagram
    True
    """
    payload = _project_file_text(network, diagram, views).encode("utf-8")
    target = _resolve_target(os.fspath(path))
    _atomic_write(target, payload)
    logger.debug(
        "Saved project '%s' to '%s' (schema version %d).", network.name, target, SCHEMA_VERSION
    )


def load_project(path: _PathLike) -> Project:
    """
    Load a project file of any release.

    Parameters
    ----------
    path : str or os.PathLike
        The project file.

    Returns
    -------
    Project
        The network with its diagram and views. A file of schema version 1
        or without a version holds a network only: ``diagram`` is ``None``
        and ``views`` empty.

    Raises
    ------
    OSError
        If the file cannot be read.
    ValueError
        As for :func:`load_network_from_json`, and for a version-2 file whose
        sections are missing or malformed.

    Warns
    -----
    InputWarning
        Code ``PROJECT_FILE_LEGACY_ENCODING``, as for
        :func:`load_network_from_json`.

    Examples
    --------
    >>> import pathlib, tempfile
    >>> import groundinsight as gi
    >>> path = pathlib.Path(tempfile.mkdtemp()) / "sued.json"
    >>> gi.save_project(path, gi.create_network(name="Süd", frequencies=[50.0]))
    >>> project = gi.load_project(path)
    >>> project.network.name, project.schema_version, project.diagram
    ('Süd', 2, None)
    """
    return _read_project(os.fspath(path))

def save_network_to_json(network: Network, path: _PathLike) -> None:
    """
    Save a network to a JSON project file, atomically and as UTF-8.

    Writes a project file (see the module docstring) whose ``network``
    section is the network with its stored results, with no diagram and no
    views; :func:`save_project` writes those as well.

    Parameters
    ----------
    network : Network
        The network to save.
    path : str or os.PathLike
        Target file. An existing file is replaced as a whole or not at all;
        if ``path`` is a symbolic link, the file it points to is replaced.

    Raises
    ------
    OSError
        If the file cannot be written (directory missing, disk full,
        read-only target, ...). An existing file is then left byte-identical
        and no temporary file remains.
    Exception
        Whatever the serialiser raises; the disk is not touched in that case.

    Notes
    -----
    The new content is written to a temporary file next to the target,
    flushed and ``fsync``-ed, and then moved over the target with
    :func:`os.replace`, which is atomic on POSIX and Windows. The directory
    entry is ``fsync``-ed afterwards where the platform supports it.

    Examples
    --------
    >>> import groundinsight as gi
    >>> net = gi.create_network(name="Süd", frequencies=[50.0])
    >>> gi.save_network_to_json(net, "sued.json")  # doctest: +SKIP
    >>> gi.load_network_from_json("sued.json").name  # doctest: +SKIP
    'Süd'
    """
    # Serialise first: a failure here must not have touched the disk.
    save_project(path, network)


def load_network_from_json(path: _PathLike) -> Network:
    """
    Load a network from a JSON project file.

    Reads files of every release: files with the current or an older
    ``schema_version``, and files without one (written by 0.6.0 or earlier).
    The diagram and views of a version-2 file are not returned; use
    :func:`load_project` for those.

    Parameters
    ----------
    path : str or os.PathLike
        The project file.

    Returns
    -------
    Network
        The validated network, including any stored results.

    Raises
    ------
    OSError
        If the file cannot be read.
    ValueError
        If the file is not valid JSON, is not a JSON object, is neither UTF-8
        nor in the platform's preferred encoding, carries an invalid or a
        newer ``schema_version`` than :data:`SCHEMA_VERSION`, or does not
        describe a valid network (``pydantic.ValidationError`` is a
        ``ValueError``).

    Warns
    -----
    InputWarning
        Code ``PROJECT_FILE_LEGACY_ENCODING``: the file is not UTF-8 and was
        decoded with the platform's preferred encoding, as 0.6.0 wrote it on
        that machine.

    Examples
    --------
    >>> import groundinsight as gi
    >>> net = gi.load_network_from_json("sued.json")  # doctest: +SKIP
    """
    return _read_project(os.fspath(path)).network


def project_to_json(
    network: Network,
    diagram: Optional[Diagram] = None,
    views: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    The text of a project file, without writing it.

    What :func:`save_project` writes, for a store that keeps projects as text
    (a database column, a server's object store) rather than as files.

    Parameters
    ----------
    network : Network
        The network, with its stored results.
    diagram : Diagram, optional
        Its diagram.
    views : list of dict, optional
        The evaluation tabs; JSON objects.

    Returns
    -------
    str
        The JSON text of a project file of :data:`SCHEMA_VERSION`.

    Raises
    ------
    TypeError
        If a view is not a JSON object or holds values JSON cannot store.

    Examples
    --------
    >>> import groundinsight as gi
    >>> text = gi.project_to_json(gi.create_network(name="n", frequencies=[50.0]))
    >>> gi.project_from_json(text).network.name
    'n'
    """
    return _project_file_text(network, diagram, views)


def project_from_json(text: str, source: str = "<text>") -> Project:
    """
    Read the text of a project file of any release.

    Parameters
    ----------
    text : str
        The JSON text, as :func:`project_to_json` or :func:`save_project`
        produce it, or of an older release.
    source : str
        How to name the text in messages.

    Returns
    -------
    Project
        As :func:`load_project` returns it.

    Raises
    ------
    ValueError
        As documented for :func:`load_project`.

    Examples
    --------
    >>> import groundinsight as gi
    >>> project = gi.project_from_json(gi.project_to_json(
    ...     gi.create_network(name="n", frequencies=[50.0]), gi.Diagram()))
    >>> project.schema_version, project.diagram == gi.Diagram()
    (2, True)
    """
    return _parse_project(text, None, source)


def _read_project(source: str) -> Project:
    """
    Read and validate a project file.

    Parameters
    ----------
    source : str
        The file.

    Returns
    -------
    Project
        Its content.

    Raises
    ------
    OSError
        If the file cannot be read.
    ValueError
        As documented for :func:`load_project`.
    """
    with open(source, "rb") as handle:
        raw = handle.read()
    text, legacy_encoding = _decode(raw, source)
    return _parse_project(text, legacy_encoding, source)


def _parse_project(text: str, legacy_encoding: Optional[str], source: str) -> Project:
    """
    Validate the decoded text of a project file.

    Parameters
    ----------
    text : str
        The JSON text.
    legacy_encoding : str or None
        The platform encoding the text was decoded with, or ``None`` for
        UTF-8.
    source : str
        The file (or a name of the text) for messages.

    Returns
    -------
    Project
        Its content.

    Raises
    ------
    ValueError
        As documented for :func:`load_project`.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"'{source}' is not a valid JSON file: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(
            f"'{source}' does not contain a groundinsight network: the "
            f"top-level JSON value is a {type(payload).__name__}, not an object."
        )
    schema_version, written_by = _pop_metadata(payload, source)
    if legacy_encoding is not None and schema_version is not None:
        # Every release that writes a schema version writes UTF-8, so a file
        # with one that is not UTF-8 has been re-encoded or damaged since.
        raise ValueError(
            f"'{source}' carries schema_version {schema_version} but is not "
            "valid UTF-8; project files with a schema version are always "
            "written as UTF-8. Re-encode the file as UTF-8."
        )
    network_data, diagram_data, views = _sections(payload, schema_version, source)
    network = Network.model_validate(network_data)
    diagram = None if diagram_data is None else Diagram.model_validate(diagram_data)
    if legacy_encoding is not None:
        emit(
            InputWarning,
            f"'{source}' is not UTF-8 and was decoded with the platform's "
            f"preferred encoding '{legacy_encoding}', the way groundinsight "
            "0.6.0 and earlier wrote project files. Names may be garbled if "
            "the file was written on a computer with a different locale. "
            "Saving the network again writes UTF-8.",
            code="PROJECT_FILE_LEGACY_ENCODING",
            elements=[("network", network.name)],
        )
    logger.debug(
        "Loaded network '%s' from '%s' (schema version %s, written by "
        "groundinsight %s).",
        network.name,
        source,
        schema_version if schema_version is not None else "none",
        written_by if written_by is not None else "0.6.0 or earlier",
    )
    return Project(network=network, diagram=diagram, views=views,
                   schema_version=schema_version, groundinsight_version=written_by)


def _sections(
    payload: Dict[str, Any], schema_version: Optional[int], source: str
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Split a parsed project file (metadata removed) into its sections.

    Parameters
    ----------
    payload : dict
        The top-level object without the metadata keys.
    schema_version : int or None
        The file's layout version.
    source : str
        File name for the error messages.

    Returns
    -------
    tuple
        ``(network data, diagram data or None, views)``. A file before
        version 2 is the network itself.

    Raises
    ------
    ValueError
        If a version-2 file has keys other than :data:`SECTIONS`, no
        ``network`` object, a ``diagram`` that is neither an object nor
        ``null``, or ``views`` that are not a list of objects.
    """
    if schema_version is None or schema_version < 2:
        return payload, None, []
    unknown = sorted(set(payload) - set(SECTIONS))
    if unknown:
        raise ValueError(
            f"'{source}' (schema version {schema_version}) has unknown top-level "
            f"keys {unknown}; expected {list(SECTIONS)}."
        )
    network = payload.get("network")
    if not isinstance(network, dict):
        raise ValueError(f"'{source}' has no 'network' object.")
    diagram = payload.get("diagram")
    if diagram is not None and not isinstance(diagram, dict):
        raise ValueError(f"'{source}': 'diagram' must be an object or null.")
    views = payload.get("views", [])
    if not isinstance(views, list) or not all(isinstance(view, dict) for view in views):
        raise ValueError(f"'{source}': 'views' must be a list of objects.")
    return network, diagram, views


# ---------------------------------------------------------------------------
# Serialisation and metadata
# ---------------------------------------------------------------------------


def _project_file_text(
    network: Network,
    diagram: Optional[Diagram] = None,
    views: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    The text of a project file: the header keys, then the three sections.

    Parameters
    ----------
    network : Network
        The network to serialise.
    diagram : Diagram, optional
        Its diagram.
    views : list of dict, optional
        The evaluation tabs.

    Returns
    -------
    str
        A JSON object whose first keys are :data:`METADATA_KEYS`, followed
        by :data:`SECTIONS`.

    Raises
    ------
    TypeError
        If a view is not a JSON object or cannot be serialised.

    Notes
    -----
    The network's ``model_dump_json(indent=4)`` is spliced in as it is,
    indented by one level, instead of re-serialising a parsed copy: the
    section is Pydantic's own output, and the network is not parsed and
    written a second time. Line-wise indentation is safe because JSON
    strings cannot contain a raw line break.
    """
    # Imported here: the package ``__init__`` imports this module before it
    # defines ``__version__``.
    from .. import __version__

    views = [] if views is None else views
    if not all(isinstance(view, dict) for view in views):
        raise TypeError("every view must be a JSON object (dict)")

    def section(text: str) -> str:
        """Indent a JSON text by one level after its first line.

        Parameters
        ----------
        text : str
            The JSON text.

        Returns
        -------
        str
            The indented text.
        """
        return text.replace("\n", "\n    ")

    network_text = section(network.model_dump_json(indent=4))
    diagram_text = "null" if diagram is None else section(diagram.model_dump_json(indent=4))
    views_text = section(json.dumps(views, indent=4, ensure_ascii=False, allow_nan=False))
    return (
        "{\n"
        f'    "{METADATA_KEYS[0]}": {SCHEMA_VERSION},\n'
        f'    "{METADATA_KEYS[1]}": {json.dumps(__version__)},\n'
        f'    "network": {network_text},\n'
        f'    "diagram": {diagram_text},\n'
        f'    "views": {views_text}\n'
        "}\n"
    )


def _pop_metadata(
    payload: Dict[str, Any], source: str
) -> Tuple[Optional[int], Optional[Any]]:
    """
    Remove and check the metadata keys of a parsed project file.

    Parameters
    ----------
    payload : dict
        The parsed top-level JSON object; the metadata keys are removed from
        it in place, so what remains are the model fields.
    source : str
        File name for the error messages.

    Returns
    -------
    schema_version : int or None
        ``None`` for a file without the key (0.6.0 or earlier).
    groundinsight_version : object or None
        The writer's version as stored; informational only.

    Raises
    ------
    ValueError
        If ``schema_version`` is not a positive integer, or is newer than
        :data:`SCHEMA_VERSION`.
    """
    schema_version = payload.pop(METADATA_KEYS[0], None)
    written_by = payload.pop(METADATA_KEYS[1], None)
    if schema_version is None:
        return None, written_by
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        raise ValueError(
            f"'{source}' has an invalid schema_version {schema_version!r}; "
            "expected a positive integer."
        )
    if schema_version > SCHEMA_VERSION:
        from .. import __version__

        raise ValueError(
            f"'{source}' uses project-file schema version {schema_version} "
            f"(written by groundinsight {written_by or 'unknown'}), but this "
            f"release ({__version__}) reads schema versions up to "
            f"{SCHEMA_VERSION}. Update groundinsight to open it; reading it "
            "here could silently drop what the newer release stored."
        )
    return schema_version, written_by


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


def _decode(raw: bytes, source: str) -> Tuple[str, Optional[str]]:
    """
    Decode a project file: UTF-8 first, then the 0.6.0 locale fallback.

    Parameters
    ----------
    raw : bytes
        File content.
    source : str
        File name for the error message.

    Returns
    -------
    text : str
        The decoded text, without a byte-order mark.
    legacy_encoding : str or None
        ``None`` for UTF-8; otherwise the platform encoding that was used.

    Raises
    ------
    ValueError
        If the content is neither UTF-8 nor valid in the platform's
        preferred encoding.

    Notes
    -----
    ``locale.getpreferredencoding(False)`` is the encoding ``open()`` uses
    when none is given (UTF-8 in Python's UTF-8 mode), i.e. what the 0.6.0
    writer used on this machine.
    """
    try:
        return raw.decode("utf-8-sig"), None
    except UnicodeDecodeError as utf8_error:
        fallback = locale.getpreferredencoding(False)
        hint = (
            "Project files are UTF-8; re-encode the file as UTF-8 (any text "
            "editor can 'save as UTF-8')."
        )
        if codecs.lookup(fallback).name == "utf-8":
            raise ValueError(
                f"'{source}' is not valid UTF-8 ({utf8_error}). {hint}"
            ) from utf8_error
        try:
            return raw.decode(fallback), fallback
        except UnicodeDecodeError:
            raise ValueError(
                f"'{source}' is neither valid UTF-8 ({utf8_error}) nor valid "
                f"in the platform encoding '{fallback}'. {hint}"
            ) from utf8_error


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def _resolve_target(target: str) -> str:
    """
    Follow a symbolic link so the link survives the replace.

    Parameters
    ----------
    target : str
        The path the caller gave.

    Returns
    -------
    str
        The file that is to be replaced. :func:`os.replace` on the link
        itself would swap the link for a regular file.
    """
    if os.path.islink(target):
        return os.path.realpath(target)
    return target


def _atomic_write(target: str, payload: bytes) -> None:
    """
    Write ``payload`` to ``target`` through a temporary sibling file.

    Parameters
    ----------
    target : str
        Final file name.
    payload : bytes
        Complete file content.

    Raises
    ------
    OSError
        From any step; the temporary file is removed and ``target`` is left
        as it was.
    PermissionError
        If ``target`` exists and is not writable -- ``open(target, "w")``
        refuses that, and a replace must not bypass it.
    """
    directory = os.path.dirname(os.path.abspath(target))
    existing_mode: Optional[int] = None
    try:
        existing = os.stat(target)
    except FileNotFoundError:
        pass
    else:
        if not os.access(target, os.W_OK):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), target)
        existing_mode = stat.S_IMODE(existing.st_mode)

    descriptor, temporary = _create_sibling(directory, os.path.basename(target))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
    _fsync_directory(directory)


def _create_sibling(directory: str, name: str) -> Tuple[int, str]:
    """
    Create a new, empty temporary file next to the target.

    Parameters
    ----------
    directory : str
        Directory of the target.
    name : str
        Base name of the target, used as a recognisable prefix.

    Returns
    -------
    descriptor : int
        An open, writable file descriptor.
    path : str
        The temporary file's path.

    Raises
    ------
    FileExistsError
        If no unused name was found after 100 attempts.
    OSError
        If the directory is missing or not writable.

    Notes
    -----
    :func:`tempfile.mkstemp` would create the file with mode ``0o600``, and
    the replace would carry that over -- a project shared with colleagues
    would suddenly be readable by its owner only. ``os.open`` with ``0o666``
    gets the permissions a plain ``open(path, "w")`` gets (the umask applies),
    and ``O_EXCL`` keeps the creation race-free.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(100):
        candidate = os.path.join(directory, f".{name}.{secrets.token_hex(6)}.tmp")
        try:
            return os.open(candidate, flags, 0o666), candidate
        except FileExistsError:
            continue
    raise FileExistsError(
        errno.EEXIST, "no unused temporary file name", os.path.join(directory, name)
    )


def _fsync_directory(directory: str) -> None:
    """
    Make the rename durable by syncing the directory entry (POSIX only).

    Parameters
    ----------
    directory : str
        Directory that received the new file.

    Notes
    -----
    Best effort: Windows cannot open a directory for this, and some file
    systems refuse it. The new content is already in place when this runs,
    so a failure here is logged, not raised -- raising would report a failed
    save for a file that has in fact been replaced.
    """
    if os.name != "posix":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError as exc:  # pragma: no cover - platform dependent
        logger.debug("Could not open '%s' to fsync it: %s", directory, exc)
        return
    try:
        os.fsync(descriptor)
    except OSError as exc:  # pragma: no cover - platform dependent
        logger.debug("Could not fsync directory '%s': %s", directory, exc)
    finally:
        os.close(descriptor)
