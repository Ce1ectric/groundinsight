# io/project_file.py

"""
The JSON project file: one network per file, UTF-8, written atomically.

A project file is what :func:`save_network_to_json` writes and
:func:`load_network_from_json` reads -- the network's own
``model_dump_json(indent=4)`` with two header keys in front::

    {
        "schema_version": 1,
        "groundinsight_version": "2026.11.0",
        "name": "...",
        ...
    }

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
    dropping whatever the newer release added. Files without the keys --
    plain ``model_dump_json`` dumps, the format of groundinsight 0.6.0 and
    earlier -- load as a network without metadata.

Where the metadata lives, and why not in an envelope
----------------------------------------------------
The two keys sit *next to* the model fields rather than in an envelope such
as ``{"format": ..., "network": {...}}``. ``Network`` does not forbid extra
keys (its ``model_config`` leaves ``extra`` at Pydantic's default
``"ignore"``), so ``Network.model_validate_json`` -- the documented way to
read a dump, used in the docs and by every script written against 0.6.0 --
still accepts a project file, and a 0.6.0 reader opens a new file too. An
envelope would break both. The reader here does not rely on ``extra`` being
``"ignore"``: it removes the two keys before validation, so a later
``extra="forbid"`` on ``Network`` cannot lock out project files. The names are
therefore reserved: ``Network`` must never get a field called
``schema_version`` or ``groundinsight_version``.

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
from typing import Any, Dict, Optional, Tuple, Union

from ..diagnostics import InputWarning, emit
from ..models.core_models import Network

logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 1
"""Version of the project-file layout this release writes, and the newest one
it reads. See the module docstring for when it is incremented."""

METADATA_KEYS: Tuple[str, ...] = ("schema_version", "groundinsight_version")
"""The top-level keys of a project file that are file metadata, not fields of
:class:`~groundinsight.Network`, in the order they are written."""

_PathLike = Union[str, "os.PathLike[str]"]


def save_network_to_json(network: Network, path: _PathLike) -> None:
    """
    Save a network to a JSON project file, atomically and as UTF-8.

    The file is the network's ``model_dump_json(indent=4)`` preceded by the
    header keys ``schema_version`` (:data:`SCHEMA_VERSION`) and
    ``groundinsight_version``. Stored results are included.

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
    payload = _project_file_text(network).encode("utf-8")
    target = _resolve_target(os.fspath(path))
    _atomic_write(target, payload)
    logger.debug(
        "Saved network '%s' to '%s' (schema version %d).",
        network.name,
        target,
        SCHEMA_VERSION,
    )


def load_network_from_json(path: _PathLike) -> Network:
    """
    Load a network from a JSON project file.

    Reads files of every release: files with the current or an older
    ``schema_version``, and files without one (written by 0.6.0 or earlier).

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
    source = os.fspath(path)
    with open(source, "rb") as handle:
        raw = handle.read()
    text, legacy_encoding = _decode(raw, source)
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
    network = Network.model_validate(payload)
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
    return network


# ---------------------------------------------------------------------------
# Serialisation and metadata
# ---------------------------------------------------------------------------


def _project_file_text(network: Network) -> str:
    """
    The text of a project file: two header keys, then the model's own dump.

    Parameters
    ----------
    network : Network
        The network to serialise.

    Returns
    -------
    str
        A JSON object whose first keys are :data:`METADATA_KEYS`.

    Raises
    ------
    RuntimeError
        If the model dump is not a JSON object (cannot happen for a
        ``Network``, which always has fields; checked so that a future change
        of the serialiser fails loudly instead of writing invalid JSON).

    Notes
    -----
    The header is spliced in front of ``model_dump_json(indent=4)`` instead of
    re-serialising a merged dictionary. That keeps Pydantic's own output byte
    for byte -- a project file minus its first two keys *is* the model dump --
    and avoids parsing and writing the whole network a second time.
    """
    # Imported here: the package ``__init__`` imports this module before it
    # defines ``__version__``.
    from .. import __version__

    body = network.model_dump_json(indent=4)
    if not body.startswith("{") or body[1:].lstrip().startswith("}"):
        raise RuntimeError(
            "Network.model_dump_json() did not produce a non-empty JSON "
            "object; refusing to write a project file from it."
        )
    header = (
        "{\n"
        f'    "{METADATA_KEYS[0]}": {SCHEMA_VERSION},\n'
        f'    "{METADATA_KEYS[1]}": {json.dumps(__version__)},'
    )
    return header + body[1:]


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
