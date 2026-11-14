# api/commands.py
"""
Edit commands: every change of a project, atomic and undoable.

A front end never assigns to a model field itself. It builds a command --
``UpdateBus(name="b1", fields={"specific_earth_resistance": 250.0})`` -- and
hands it to :meth:`ProjectSession.apply
<groundinsight.api.ProjectSession.apply>`. Three properties hold for every
command:

**It goes through the library.** A command calls the library's own editing
API -- ``Network.add_*``, ``update_bus``, ``update_branch``, ``remove_*``,
assignment of ``Network.frequencies`` and of attached elements' fields --
which validates the input, re-derives the impedances through the same code
path as ``create_bus`` and keeps the network's invariants (no dangling bus
reference, no rename of an attached element). The command adds no physics and
no second validation that could disagree with the library's.

**It is atomic.** A rejected command raises :class:`CommandError` and leaves
the network, the session's type catalogue, the revision and the history
exactly as they were. The library methods are atomic themselves; where a
command needs several of them (a type edit re-derives every element of that
type), it rolls back the steps already done before it re-raises.

**It is undoable.** Undo runs inverse operations through the same library
API -- ``remove_bus`` undoes ``add_bus`` and vice versa -- rather than
restoring a snapshot of the whole network, which would cost a deep copy of
every impedance table per keystroke. What a library call drops as a side
effect is put back explicitly: the stored result and the active state of a
removed fault, the position of a re-added element in its dictionary, and
derived values the user had overridden by assignment. The session tests check
the outcome the strict way: after undo, the serialised network is identical
to the one before the command, so a result computed before the command is
current again (the library compares input digests by content).

**Types.** ``BusType`` and ``BranchType`` are read-only in the library: a type
is evaluated into every element that uses it, so a changed type is a new
type, swapped into its users with ``update_bus``/``update_branch``. Elements
refer to types by *name* in the tables a front end edits, so the session keeps
a catalogue of types by name -- the types in use plus types added for later
use -- and the type commands edit it. All of that lives in
:class:`_TypeCommand`: should the maintainer decide that editing a type
re-derives its users in place, only that class changes.

The commands are pydantic models with a discriminator field ``op``, so they
are also the request bodies of a future REST endpoint; :func:`parse_command`
builds one from its JSON form.
"""

import logging
from typing import Annotated, Any, ClassVar, Dict, List, Literal, Optional, Tuple, Union

from pydantic import Field, PrivateAttr, TypeAdapter, ValidationError, field_validator

import groundinsight as gi

from .schemas import BaseDTO, ComplexDTO, Phasor, SessionLimitsDTO

logger = logging.getLogger(__name__)

__all__ = [
    "COMMAND_OPS",
    "AddBranch",
    "AddBranchType",
    "AddBus",
    "AddBusType",
    "AddFault",
    "AddSource",
    "Command",
    "CommandContext",
    "CommandError",
    "RemoveBranch",
    "RemoveBranchType",
    "RemoveBus",
    "RemoveBusType",
    "RemoveFault",
    "RemoveSource",
    "SetFrequencies",
    "UpdateBranch",
    "UpdateBranchType",
    "UpdateBus",
    "UpdateBusType",
    "UpdateFault",
    "UpdateSource",
    "parse_command",
]


class CommandError(ValueError):
    """A command was rejected; the session is unchanged.

    The message names the command and the library's reason.
    """


class CommandContext:
    """
    What a command acts on: the network, the type catalogue and the limits.

    Parameters
    ----------
    network : Network
        The session's network.
    bus_types, branch_types : dict of str to BusType / BranchType
        The session's type catalogue by name. Commands edit these
        dictionaries in place.
    limits : SessionLimitsDTO
        The session's resource limits.
    """

    def __init__(
        self,
        network: gi.Network,
        bus_types: Dict[str, gi.BusType],
        branch_types: Dict[str, gi.BranchType],
        limits: SessionLimitsDTO,
    ) -> None:
        self.network = network
        self.bus_types = bus_types
        self.branch_types = branch_types
        self.limits = limits

    def catalogue(self, kind: str) -> Dict[str, Any]:
        """
        The catalogue of one element kind.

        Parameters
        ----------
        kind : {"bus", "branch"}
            The element kind.

        Returns
        -------
        dict
            :attr:`bus_types` or :attr:`branch_types`.
        """
        return self.bus_types if kind == "bus" else self.branch_types

    def type_named(self, kind: str, name: Any):
        """
        Look up a type of the catalogue by name.

        Parameters
        ----------
        kind : {"bus", "branch"}
            The element kind.
        name : str
            The type name.

        Returns
        -------
        BusType or BranchType
            The catalogue's type object.

        Raises
        ------
        CommandError
            If ``name`` is not a string or names no type of the catalogue.
        """
        catalogue = self.catalogue(kind)
        if not isinstance(name, str):
            raise CommandError(
                f"a {kind} type is referred to by its name, got {type(name).__name__}"
            )
        if name not in catalogue:
            raise CommandError(
                f"{kind} type '{name}' is not in the catalogue "
                f"(known: {', '.join(sorted(catalogue)) or 'none'})"
            )
        return catalogue[name]

    def check_room(self, collection: str) -> None:
        """
        Refuse one more element beyond the session's size limit.

        Parameters
        ----------
        collection : {"buses", "branches"}
            The element collection that is about to grow by one.

        Raises
        ------
        CommandError
            If the collection already holds ``max_buses``/``max_branches``
            elements.
        """
        limit_name = {"buses": "max_buses", "branches": "max_branches"}[collection]
        limit = getattr(self.limits, limit_name)
        size = len(getattr(self.network, collection))
        if limit is not None and size >= limit:
            raise CommandError(
                f"the network already has {size} {collection}; this session "
                f"allows at most {limit} ({limit_name})"
            )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _field_text(fields: Dict[str, Any]) -> str:
    """Render ``fields`` for a command label, in insertion order."""
    return ", ".join(f"{key}={value!r}" for key, value in fields.items())


def rejection_reason(exc: BaseException) -> str:
    """
    The library's reason for a rejection, as a front end shows it.

    Internal API: the session words its project errors with it, too; not
    exported, may change in any release.

    A pydantic ``ValidationError`` renders as a multi-line report (error
    count, model name, input echo, a link to pydantic's documentation) around
    the one sentence the library wrote. Front ends show the reason verbatim,
    so only the messages are kept, each with its field when the message does
    not name it already.

    Parameters
    ----------
    exc : BaseException
        The exception the library raised.

    Returns
    -------
    str
        One line per message, joined with ``"; "``.
    """
    if not isinstance(exc, ValidationError):
        return str(exc)
    parts = []
    for error in exc.errors(include_url=False):
        message = str(error.get("msg", "")).removeprefix("Value error, ")
        location = ".".join(str(part) for part in error.get("loc", ()))
        parts.append(f"{location}: {message}" if location and location not in message else message)
    return "; ".join(parts) or str(exc)


def _derived(element) -> Dict[str, Any]:
    """
    The derived fields of a bus or branch (impedance tables, RLC values).

    Parameters
    ----------
    element : Bus or Branch
        The element.

    Returns
    -------
    dict
        Field name to the current value object (the library replaces these
        dictionaries on re-derivation, it does not edit them in place).
    """
    return {field: getattr(element, field) for field in type(element).DERIVED_FIELDS}


def _restore_derived(element, derived: Dict[str, Any]) -> None:
    """
    Put back derived values that a re-derivation did not reproduce.

    A re-derivation from the same inputs reproduces the derived values bit for
    bit, so normally nothing is written. A value differs only when it had
    been overridden by assignment (the library's documented way to impose an
    impedance for a study); assigning it again restores the override.

    Parameters
    ----------
    element : Bus or Branch
        The element after its inputs were restored.
    derived : dict
        The output of :func:`_derived` before the command.
    """
    for field, value in derived.items():
        if getattr(element, field) != value:
            setattr(element, field, value)


def _restore_order(network: gi.Network, collection: str, order: List[str]) -> None:
    """
    Put a re-inserted element back at its former position.

    ``Network.add_*`` appends; the serialised network (and a table view)
    follows dictionary order, so an undone removal restores the order through
    an assignment of the whole dictionary, which the library re-attaches.

    Parameters
    ----------
    network : Network
        The network.
    collection : str
        ``"buses"``, ``"branches"``, ``"sources"``, ``"faults"`` or
        ``"results"``.
    order : list of str
        The former key order.
    """
    current = getattr(network, collection)
    if list(current) == order:
        return
    ordered = {name: current[name] for name in order if name in current}
    ordered.update({name: value for name, value in current.items() if name not in ordered})
    setattr(network, collection, ordered)


def _phasor_value(value: Any) -> Any:
    """
    One phasor in its JSON form: a number, or ``{"real", "imag"}``.

    Parameters
    ----------
    value : Any
        A number, a :class:`ComplexDTO`, a ``complex`` or a mapping.

    Returns
    -------
    Any
        A JSON-representable value the library's ``ComplexNumber`` accepts.
    """
    if isinstance(value, ComplexDTO):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k in ("real", "imag")}
    return value


def _frequency_map(mapping: Any, phasors: bool) -> Any:
    """
    Normalise a frequency-keyed mapping: float keys, JSON-form values.

    JSON object keys are strings; normalising them to ``float`` here makes a
    command equal to itself after a trip through JSON.

    Parameters
    ----------
    mapping : Any
        A mapping of frequency to value, or ``None``.
    phasors : bool
        Whether the values are phasors (see :func:`_phasor_value`).

    Returns
    -------
    Any
        The normalised mapping, or the input when it is not a mapping (the
        library then reports the type error).
    """
    if not isinstance(mapping, dict):
        return mapping
    out = {}
    for key, value in mapping.items():
        try:
            frequency = float(key)
        except (TypeError, ValueError):
            frequency = key
        out[frequency] = _phasor_value(value) if phasors else value
    return out


def _phasor_map(mapping: Optional[Dict[float, Phasor]]) -> Optional[Dict[float, complex]]:
    """
    A request's phasor map as the ``complex`` values a ``Source`` takes.

    Parameters
    ----------
    mapping : dict of float to Phasor, optional
        Frequency to number or :class:`ComplexDTO`.

    Returns
    -------
    dict of float to complex, optional
        ``None`` stays ``None``.
    """
    if mapping is None:
        return None
    return {
        float(f): (v.to_complex() if isinstance(v, ComplexDTO) else complex(v))
        for f, v in mapping.items()
    }


# ---------------------------------------------------------------------------
# base
# ---------------------------------------------------------------------------


class Command(BaseDTO):
    """
    Base of every edit command.

    Subclasses define their fields, a :attr:`label` and the two methods
    ``_apply``/``_revert``; this class turns the library's ``ValueError``,
    ``TypeError``, ``KeyError`` and ``AttributeError`` into
    :class:`CommandError`. ``_apply`` must leave everything unchanged when it
    raises; ``_revert`` undoes a successful ``_apply`` and must not fail.

    Attributes
    ----------
    op : str
        The operation, the discriminator of the JSON form.
    """

    op: str
    _undo: Any = PrivateAttr(default=None)

    @property
    def label(self) -> str:
        """
        Human-readable description for the undo history.

        Returns
        -------
        str
            The operation name; a concrete command names its element as well.
        """
        return self.op

    def apply(self, context: CommandContext) -> None:
        """
        Apply the command.

        Parameters
        ----------
        context : CommandContext
            Network, type catalogue and limits of the session.

        Raises
        ------
        CommandError
            If the library rejects the change; nothing has changed then.
        """
        try:
            self._apply(context)
        except CommandError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise CommandError(f"{self.label}: {rejection_reason(exc)}") from exc

    def revert(self, context: CommandContext) -> None:
        """
        Undo a successful :meth:`apply`.

        Parameters
        ----------
        context : CommandContext
            The same context the command was applied to.

        Raises
        ------
        RuntimeError
            If the command was not applied.
        """
        if self._undo is None:
            raise RuntimeError(f"cannot revert '{self.label}': it was not applied")
        self._revert(context)
        self._undo = None

    def _apply(self, context: CommandContext) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _revert(self, context: CommandContext) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class _UpdateFields(Command):
    """
    Base of the commands that change several fields of one element.

    Attributes
    ----------
    name : str
        The element.
    fields : dict
        Field name to new value, as the library names the fields.
    """

    name: str
    fields: Dict[str, Any]

    #: How the element is named in messages.
    _KIND: ClassVar[str] = "element"

    @property
    def label(self) -> str:
        """``update <kind> '<name>': field=value, ...``."""
        return f"update {self._KIND} '{self.name}': {_field_text(self.fields)}"

    def _require_fields(self) -> None:
        """Refuse an empty update, which would only create a revision."""
        if not self.fields:
            raise CommandError(f"{self.label}: no fields to update")

    def _refuse_rename(self) -> None:
        """Refuse a rename: elements are kept, and referred to, by name."""
        if "name" in self.fields and self.fields["name"] != self.name:
            raise CommandError(
                f"{self.label}: an element cannot be renamed in place (it is "
                f"referred to by name); remove it and add a renamed copy"
            )


# ---------------------------------------------------------------------------
# buses and branches
# ---------------------------------------------------------------------------


class AddBus(Command):
    """
    Add a bus of a catalogue type (``Network.add_bus``).

    Attributes
    ----------
    name : str
        Name of the new bus.
    type : str
        Name of its bus type in the session's catalogue.
    specific_earth_resistance : float
        ``rho`` in Ohm*m; default 100.
    active : bool
        Whether the bus takes part in the solve.
    description : str, optional
        Free text.
    """

    op: Literal["add_bus"] = "add_bus"
    name: str
    type: str
    specific_earth_resistance: float = 100.0
    active: bool = True
    description: Optional[str] = None

    @property
    def label(self) -> str:
        """
        ``add bus '<name>'``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"add bus '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        context.check_room("buses")
        bus = gi.Bus.model_validate(
            {
                "name": self.name,
                "type": context.type_named("bus", self.type),
                "impedance": {},
                "specific_earth_resistance": self.specific_earth_resistance,
                "active": self.active,
                "description": self.description,
            }
        )
        context.network.add_bus(bus)
        self._undo = True

    def _revert(self, context: CommandContext) -> None:
        context.network.remove_bus(self.name)


class UpdateBus(_UpdateFields):
    """
    Change fields of a bus, all or nothing (``Network.update_bus``).

    ``fields`` may hold ``type`` (a catalogue type name),
    ``specific_earth_resistance``, ``active`` and ``description``; the
    impedances are re-derived by the library.
    """

    op: Literal["update_bus"] = "update_bus"
    _KIND: ClassVar[str] = "bus"

    def _apply(self, context: CommandContext) -> None:
        _update_element(self, context, "bus")

    def _revert(self, context: CommandContext) -> None:
        _revert_update(self, context, "bus")


class RemoveBus(Command):
    """
    Remove a bus that nothing refers to (``Network.remove_bus``).

    Attributes
    ----------
    name : str
        The bus.
    """

    op: Literal["remove_bus"] = "remove_bus"
    name: str

    @property
    def label(self) -> str:
        """
        ``remove bus '<name>'``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"remove bus '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        _remove_element(self, context, "buses")

    def _revert(self, context: CommandContext) -> None:
        _readd_element(self, context, "buses")


class AddBranch(Command):
    """
    Add a branch of a catalogue type (``Network.add_branch``).

    Attributes
    ----------
    name : str
        Name of the new branch.
    type : str
        Name of its branch type in the session's catalogue.
    from_bus, to_bus : str
        The buses it connects.
    length : float
        Length in km.
    specific_earth_resistance : float
        ``rho`` in Ohm*m; default 100.
    parallel_coefficient : float, optional
        For ``phase_current_mode="paths"``; default 1.0.
    active : bool
        Whether the branch takes part in the solve.
    description : str, optional
        Free text.
    """

    op: Literal["add_branch"] = "add_branch"
    name: str
    type: str
    from_bus: str
    to_bus: str
    length: float
    specific_earth_resistance: float = 100.0
    parallel_coefficient: Optional[float] = 1.0
    active: bool = True
    description: Optional[str] = None

    @property
    def label(self) -> str:
        """
        ``add branch '<name>'``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"add branch '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        context.check_room("branches")
        branch = gi.Branch.model_validate(
            {
                "name": self.name,
                "type": context.type_named("branch", self.type),
                "from_bus": self.from_bus,
                "to_bus": self.to_bus,
                "length": self.length,
                "specific_earth_resistance": self.specific_earth_resistance,
                "parallel_coefficient": self.parallel_coefficient,
                "active": self.active,
                "description": self.description,
                "self_impedance": {},
                "mutual_impedance": {},
            }
        )
        context.network.add_branch(branch)
        self._undo = True

    def _revert(self, context: CommandContext) -> None:
        context.network.remove_branch(self.name)


class UpdateBranch(_UpdateFields):
    """
    Change fields of a branch, all or nothing (``Network.update_branch``).

    ``fields`` may hold ``type`` (a catalogue type name), ``length``,
    ``specific_earth_resistance``, ``from_bus``, ``to_bus``,
    ``parallel_coefficient``, ``active`` and ``description``.
    """

    op: Literal["update_branch"] = "update_branch"
    _KIND: ClassVar[str] = "branch"

    def _apply(self, context: CommandContext) -> None:
        _update_element(self, context, "branch")

    def _revert(self, context: CommandContext) -> None:
        _revert_update(self, context, "branch")


class RemoveBranch(Command):
    """
    Remove a branch (``Network.remove_branch``).

    Attributes
    ----------
    name : str
        The branch.
    """

    op: Literal["remove_branch"] = "remove_branch"
    name: str

    @property
    def label(self) -> str:
        """
        ``remove branch '<name>'``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"remove branch '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        _remove_element(self, context, "branches")

    def _revert(self, context: CommandContext) -> None:
        _readd_element(self, context, "branches")


#: Per element kind: the network collection and the library's update method.
_ELEMENTS = {
    "bus": ("buses", "update_bus"),
    "branch": ("branches", "update_branch"),
}


def _update_element(command: _UpdateFields, context: CommandContext, kind: str) -> None:
    """
    Apply an ``UpdateBus``/``UpdateBranch`` through the library.

    Parameters
    ----------
    command : UpdateBus or UpdateBranch
        The command.
    context : CommandContext
        The session context.
    kind : {"bus", "branch"}
        The element kind.

    Raises
    ------
    CommandError, ValueError
        If the element does not exist, ``fields`` is empty, a type name is
        unknown, or the library rejects the change (unknown, derived or
        invalid field, unknown bus, a formula that cannot be evaluated).
    """
    collection, method = _ELEMENTS[kind]
    element = getattr(context.network, collection).get(command.name)
    if element is None:
        raise CommandError(f"{command.label}: {kind} '{command.name}' does not exist")
    command._require_fields()
    resolved = dict(command.fields)
    if "type" in resolved:
        resolved["type"] = context.type_named(kind, resolved["type"])
    model_fields = type(element).model_fields
    previous = {key: getattr(element, key) for key in resolved if key in model_fields}
    derived = _derived(element)
    getattr(context.network, method)(command.name, **resolved)
    command._undo = (previous, derived)


def _revert_update(command: _UpdateFields, context: CommandContext, kind: str) -> None:
    """
    Undo an ``UpdateBus``/``UpdateBranch``: the former values, through the library.

    Parameters
    ----------
    command : UpdateBus or UpdateBranch
        The applied command.
    context : CommandContext
        The session context.
    kind : {"bus", "branch"}
        The element kind.
    """
    collection, method = _ELEMENTS[kind]
    previous, derived = command._undo
    getattr(context.network, method)(command.name, **previous)
    _restore_derived(getattr(context.network, collection)[command.name], derived)


def _remove_element(command: Command, context: CommandContext, collection: str) -> None:
    """
    Remove a bus or branch through the library and remember how to put it back.

    Parameters
    ----------
    command : RemoveBus or RemoveBranch
        The command.
    context : CommandContext
        The session context.
    collection : {"buses", "branches"}
        The network collection.
    """
    network = context.network
    element = getattr(network, collection).get(command.name)
    if element is None:
        raise CommandError(f"{command.label}: it does not exist")
    order = list(getattr(network, collection))
    derived = _derived(element)
    method = "remove_bus" if collection == "buses" else "remove_branch"
    removed = getattr(network, method)(command.name)
    command._undo = (removed, order, derived)


def _readd_element(command: Command, context: CommandContext, collection: str) -> None:
    """
    Undo a removal: add the same object back, at its former position.

    Parameters
    ----------
    command : RemoveBus or RemoveBranch
        The applied command.
    context : CommandContext
        The session context.
    collection : {"buses", "branches"}
        The network collection.
    """
    removed, order, derived = command._undo
    network = context.network
    if collection == "buses":
        network.add_bus(removed)
    else:
        network.add_branch(removed)
    _restore_derived(removed, derived)
    _restore_order(network, collection, order)


# ---------------------------------------------------------------------------
# sources and faults
# ---------------------------------------------------------------------------

_SOURCE_PHASOR_FIELDS = ("values", "voltage", "source_impedance")


class AddSource(Command):
    """
    Add a source (``Network.add_source``).

    Attributes
    ----------
    name : str
        Name of the new source.
    bus : str
        The bus it feeds.
    source_type : {"current", "voltage"}
        Current source (``values``) or Thevenin source (``voltage`` and
        ``source_impedance``).
    values, voltage, source_impedance : dict of float to Phasor, optional
        RMS phasors per frequency, as plain numbers or ``{"real", "imag"}``.
    i_k_a, r_to_x, kappa : float, optional
        IEC 60909 characteristics (metadata, not solved).
    description : str, optional
        Free text.
    """

    op: Literal["add_source"] = "add_source"
    name: str
    bus: str
    source_type: Literal["current", "voltage"] = "current"
    values: Optional[Dict[float, Phasor]] = None
    voltage: Optional[Dict[float, Phasor]] = None
    source_impedance: Optional[Dict[float, Phasor]] = None
    i_k_a: Optional[float] = None
    r_to_x: Optional[float] = None
    kappa: Optional[float] = None
    description: Optional[str] = None

    @property
    def label(self) -> str:
        """
        ``add source '<name>'``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"add source '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        source = gi.Source.model_validate(
            {
                "name": self.name,
                "bus": self.bus,
                "source_type": self.source_type,
                "values": _phasor_map(self.values),
                "voltage": _phasor_map(self.voltage),
                "source_impedance": _phasor_map(self.source_impedance),
                "i_k_a": self.i_k_a,
                "r_to_x": self.r_to_x,
                "kappa": self.kappa,
                "description": self.description,
            }
        )
        context.network.add_source(source)
        self._undo = True

    def _revert(self, context: CommandContext) -> None:
        context.network.remove_source(self.name)


class UpdateSource(_UpdateFields):
    """
    Change fields of a source, all or nothing.

    The library has no ``update_source``: a source's fields depend on each
    other (``source_type`` decides which of ``values``/``voltage`` must be
    set), so they cannot be assigned one by one. The command validates a
    complete replacement through the model and swaps it in with
    ``Network.add_source(..., overwrite=True)``, which keeps the source's
    position; undo swaps the former object back the same way.
    """

    op: Literal["update_source"] = "update_source"
    _KIND: ClassVar[str] = "source"

    @field_validator("fields")
    @classmethod
    def _normalise(cls, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Frequency keys as ``float``, phasors in JSON form."""
        return {
            key: _frequency_map(value, phasors=True) if key in _SOURCE_PHASOR_FIELDS else value
            for key, value in fields.items()
        }

    def _apply(self, context: CommandContext) -> None:
        network = context.network
        old = network.sources.get(self.name)
        if old is None:
            raise CommandError(f"{self.label}: source '{self.name}' does not exist")
        self._require_fields()
        self._refuse_rename()
        unknown = sorted(set(self.fields) - set(gi.Source.model_fields))
        if unknown:
            raise CommandError(
                f"{self.label}: Source has no field {', '.join(map(repr, unknown))}"
            )
        data = old.model_dump()
        data.update(self.fields)
        replacement = gi.Source.model_validate(data)
        network.add_source(replacement, overwrite=True)
        self._undo = old

    def _revert(self, context: CommandContext) -> None:
        context.network.add_source(self._undo, overwrite=True)


class RemoveSource(Command):
    """
    Remove a source (``Network.remove_source``).

    Attributes
    ----------
    name : str
        The source.
    """

    op: Literal["remove_source"] = "remove_source"
    name: str

    @property
    def label(self) -> str:
        """
        ``remove source '<name>'``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"remove source '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        network = context.network
        if self.name not in network.sources:
            raise CommandError(f"{self.label}: it does not exist")
        order = list(network.sources)
        self._undo = (network.remove_source(self.name), order)

    def _revert(self, context: CommandContext) -> None:
        removed, order = self._undo
        context.network.add_source(removed)
        _restore_order(context.network, "sources", order)


class AddFault(Command):
    """
    Add a fault (``Network.add_fault``).

    Attributes
    ----------
    name : str
        Name of the new fault.
    bus : str
        The faulted bus.
    scalings : dict of float to float, optional
        Factor per frequency; a missing frequency is scaled with 1.0.
    t_k_s : float, optional
        IEC 60909 short-circuit duration in s.
    n_factor : float
        IEC 60909 AC heat factor.
    description : str, optional
        Free text.
    """

    op: Literal["add_fault"] = "add_fault"
    name: str
    bus: str
    scalings: Optional[Dict[float, float]] = None
    t_k_s: Optional[float] = None
    n_factor: float = 1.0
    description: Optional[str] = None

    @property
    def label(self) -> str:
        """
        ``add fault '<name>'``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"add fault '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        fault = gi.Fault.model_validate(
            {
                "name": self.name,
                "bus": self.bus,
                "scalings": dict(self.scalings or {}),
                "t_k_s": self.t_k_s,
                "n_factor": self.n_factor,
                "description": self.description,
            }
        )
        context.network.add_fault(fault)
        self._undo = True

    def _revert(self, context: CommandContext) -> None:
        context.network.remove_fault(self.name)


class UpdateFault(_UpdateFields):
    """
    Change fields of a fault, all or nothing.

    The values are validated on a detached copy first (and ``bus`` against
    the network); only then are they assigned to the fault, which the library
    validates again. The fault object stays the same, so it stays the active
    fault if it was.
    """

    op: Literal["update_fault"] = "update_fault"
    _KIND: ClassVar[str] = "fault"

    @field_validator("fields")
    @classmethod
    def _normalise(cls, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Frequency keys of ``scalings`` as ``float``."""
        return {
            key: _frequency_map(value, phasors=False) if key == "scalings" else value
            for key, value in fields.items()
        }

    def _apply(self, context: CommandContext) -> None:
        network = context.network
        fault = network.faults.get(self.name)
        if fault is None:
            raise CommandError(f"{self.label}: fault '{self.name}' does not exist")
        self._require_fields()
        self._refuse_rename()
        scratch = fault.model_copy()  # detached: assignments run no network hooks
        for key, value in self.fields.items():
            setattr(scratch, key, value)
        if "bus" in self.fields and scratch.bus not in network.buses:
            raise CommandError(
                f"{self.label}: bus '{scratch.bus}' is not a bus of network '{network.name}'"
            )
        previous = {key: getattr(fault, key) for key in self.fields}
        written: List[str] = []
        try:
            for key in self.fields:
                setattr(fault, key, getattr(scratch, key))
                written.append(key)
        except Exception:
            for key in reversed(written):
                setattr(fault, key, previous[key])
            raise
        self._undo = previous

    def _revert(self, context: CommandContext) -> None:
        fault = context.network.faults[self.name]
        for key, value in self._undo.items():
            setattr(fault, key, value)


class RemoveFault(Command):
    """
    Remove a fault (``Network.remove_fault``).

    The library drops the fault's stored result with it, and clears the
    active fault if it was this one; undo puts both back, so a result that
    was current before is current again.

    Attributes
    ----------
    name : str
        The fault.
    """

    op: Literal["remove_fault"] = "remove_fault"
    name: str

    @property
    def label(self) -> str:
        """
        ``remove fault '<name>'``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"remove fault '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        network = context.network
        if self.name not in network.faults:
            raise CommandError(f"{self.label}: it does not exist")
        state = (
            list(network.faults),
            network.results.get(self.name),
            list(network.results),
            network.active_fault == self.name,
        )
        removed = network.remove_fault(self.name)
        self._undo = (removed, *state)

    def _revert(self, context: CommandContext) -> None:
        network = context.network
        removed, order, result, result_order, was_active = self._undo
        network.add_fault(removed)
        _restore_order(network, "faults", order)
        if result is not None:
            network.results[self.name] = result
            _restore_order(network, "results", result_order)
        if was_active:
            network.set_active_fault(self.name, keep_results=True)


# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------

#: Per element kind: type model, network collection, library update method.
_TYPE_KINDS = {
    "bus": (gi.BusType, "buses", "update_bus"),
    "branch": (gi.BranchType, "branches", "update_branch"),
}


def _replace_key(mapping: Dict[str, Any], old: str, new: str, value: Any) -> None:
    """
    Replace ``mapping[old]`` by ``mapping[new] = value`` at the same position.

    Parameters
    ----------
    mapping : dict
        The dictionary, edited in place.
    old, new : str
        Former and new key (may be equal).
    value : Any
        The new value.
    """
    items = [(new, value) if key == old else (key, item) for key, item in mapping.items()]
    mapping.clear()
    mapping.update(items)


class _TypeCommand(Command):
    """
    Base of every type command -- the one place that knows how type edits work.

    Types are read-only in the library (see the module docstring): a changed
    type is a new object, swapped into each element of that type with
    ``update_bus``/``update_branch``, which re-derives the element. The swap
    is all or nothing across the users.
    """

    _KIND: ClassVar[str] = "bus"

    def _model(self):
        """The library's type model of this kind."""
        return _TYPE_KINDS[self._KIND][0]

    def _users(self, context: CommandContext, type_name: str) -> List[Any]:
        """The elements whose type is called ``type_name``, in network order."""
        collection = _TYPE_KINDS[self._KIND][1]
        return [e for e in getattr(context.network, collection).values() if e.type.name == type_name]

    def _swap(self, context: CommandContext, users: List[Any], new_type: Any) -> List[Tuple[str, Any, Dict[str, Any]]]:
        """
        Give every user ``new_type``; on a failure, give the done ones back theirs.

        Parameters
        ----------
        context : CommandContext
            The session context.
        users : list of Bus or Branch
            The elements to change.
        new_type : BusType or BranchType
            The replacement type.

        Returns
        -------
        list of tuple
            ``(name, former type object, former derived values)`` per user,
            for :meth:`_unswap`.

        Raises
        ------
        ValueError
            If one user cannot be derived from ``new_type``; every user has
            its former type again then.
        """
        update = getattr(context.network, _TYPE_KINDS[self._KIND][2])
        previous = [(e.name, e.type, _derived(e)) for e in users]
        done: List[Tuple[str, Any, Dict[str, Any]]] = []
        try:
            for entry in previous:
                update(entry[0], type=new_type)
                done.append(entry)
        except Exception:
            self._unswap(context, done)
            raise
        return previous

    def _unswap(self, context: CommandContext, previous: List[Tuple[str, Any, Dict[str, Any]]]) -> None:
        """
        Give the users back their former type objects (and derived values).

        Parameters
        ----------
        context : CommandContext
            The session context.
        previous : list of tuple
            The output of :meth:`_swap`.
        """
        collection, method = _TYPE_KINDS[self._KIND][1:]
        update = getattr(context.network, method)
        for name, former, derived in reversed(previous):
            update(name, type=former)
            _restore_derived(getattr(context.network, collection)[name], derived)


class _AddType(_TypeCommand):
    """
    Add a type to the session's catalogue.

    Attributes
    ----------
    definition : dict
        The fields of the type, as the library's model takes them; validated
        by the model, formula guard included.
    """

    definition: Dict[str, Any]

    @property
    def label(self) -> str:
        """``add <kind> type '<name>'``."""
        return f"add {self._KIND} type '{self.definition.get('name')}'"

    def _apply(self, context: CommandContext) -> None:
        catalogue = context.catalogue(self._KIND)
        new_type = self._model().model_validate(self.definition)
        if new_type.name in catalogue:
            raise CommandError(f"{self.label}: a {self._KIND} type of that name exists")
        catalogue[new_type.name] = new_type
        self._undo = new_type.name

    def _revert(self, context: CommandContext) -> None:
        del context.catalogue(self._KIND)[self._undo]


class _UpdateType(_TypeCommand):
    """
    Replace a catalogue type by a changed copy and swap it into every user.

    Attributes
    ----------
    name : str
        The type.
    fields : dict
        Fields to change; ``name`` renames the type (its users follow).
    """

    name: str
    fields: Dict[str, Any]

    @property
    def label(self) -> str:
        """``update <kind> type '<name>': field=value, ...``."""
        return f"update {self._KIND} type '{self.name}': {_field_text(self.fields)}"

    def _apply(self, context: CommandContext) -> None:
        catalogue = context.catalogue(self._KIND)
        old = context.type_named(self._KIND, self.name)
        if not self.fields:
            raise CommandError(f"{self.label}: no fields to update")
        model = self._model()
        unknown = sorted(set(self.fields) - set(model.model_fields))
        if unknown:
            raise CommandError(
                f"{self.label}: {model.__name__} has no field {', '.join(map(repr, unknown))}"
            )
        data = old.model_dump()
        data.update(self.fields)
        new_type = model.model_validate(data)
        if new_type.name != self.name and new_type.name in catalogue:
            raise CommandError(f"{self.label}: a {self._KIND} type '{new_type.name}' exists")
        previous = self._swap(context, self._users(context, self.name), new_type)
        order = list(catalogue)
        _replace_key(catalogue, self.name, new_type.name, new_type)
        self._undo = (old, new_type.name, previous, order)

    def _revert(self, context: CommandContext) -> None:
        old, new_name, previous, order = self._undo
        self._unswap(context, previous)
        catalogue = context.catalogue(self._KIND)
        _replace_key(catalogue, new_name, self.name, old)


class _RemoveType(_TypeCommand):
    """
    Remove a type that no element uses from the catalogue.

    Attributes
    ----------
    name : str
        The type.
    """

    name: str

    @property
    def label(self) -> str:
        """``remove <kind> type '<name>'``."""
        return f"remove {self._KIND} type '{self.name}'"

    def _apply(self, context: CommandContext) -> None:
        catalogue = context.catalogue(self._KIND)
        removed = context.type_named(self._KIND, self.name)
        users = [e.name for e in self._users(context, self.name)]
        if users:
            shown = ", ".join(f"'{n}'" for n in users[:10]) + (" ..." if len(users) > 10 else "")
            raise CommandError(
                f"{self.label}: still used by {len(users)} element(s) ({shown}); "
                "give them another type first"
            )
        order = list(catalogue)
        del catalogue[self.name]
        self._undo = (removed, order)

    def _revert(self, context: CommandContext) -> None:
        removed, order = self._undo
        catalogue = context.catalogue(self._KIND)
        catalogue[self.name] = removed
        items = sorted(catalogue.items(), key=lambda item: order.index(item[0]) if item[0] in order else len(order))
        catalogue.clear()
        catalogue.update(items)


class AddBusType(_AddType):
    """Add a bus type to the catalogue; see :class:`_AddType`."""

    op: Literal["add_bus_type"] = "add_bus_type"
    _KIND: ClassVar[str] = "bus"


class UpdateBusType(_UpdateType):
    """Change a bus type and swap it into its buses; see :class:`_UpdateType`."""

    op: Literal["update_bus_type"] = "update_bus_type"
    _KIND: ClassVar[str] = "bus"


class RemoveBusType(_RemoveType):
    """Remove an unused bus type; see :class:`_RemoveType`."""

    op: Literal["remove_bus_type"] = "remove_bus_type"
    _KIND: ClassVar[str] = "bus"


class AddBranchType(_AddType):
    """Add a branch type to the catalogue; see :class:`_AddType`."""

    op: Literal["add_branch_type"] = "add_branch_type"
    _KIND: ClassVar[str] = "branch"


class UpdateBranchType(_UpdateType):
    """Change a branch type and swap it into its branches; see :class:`_UpdateType`."""

    op: Literal["update_branch_type"] = "update_branch_type"
    _KIND: ClassVar[str] = "branch"


class RemoveBranchType(_RemoveType):
    """Remove an unused branch type; see :class:`_RemoveType`."""

    op: Literal["remove_branch_type"] = "remove_branch_type"
    _KIND: ClassVar[str] = "branch"


# ---------------------------------------------------------------------------
# frequencies
# ---------------------------------------------------------------------------


class SetFrequencies(Command):
    """
    Replace the network frequencies (assignment of ``Network.frequencies``).

    The library re-derives every bus and branch at the new frequencies, all
    or nothing, and refuses an empty list, duplicates and non-finite or
    negative values; a list that is not increasing is accepted with the
    ``FREQUENCY_ORDER`` warning, which the command returns.

    Attributes
    ----------
    frequencies : list of float
        The new frequencies in Hz.
    """

    op: Literal["set_frequencies"] = "set_frequencies"
    frequencies: List[float]

    @property
    def label(self) -> str:
        """
        ``set frequencies: [...]``.

        Returns
        -------
        str
            The label in the undo history.
        """
        return f"set frequencies: {self.frequencies!r}"

    def _apply(self, context: CommandContext) -> None:
        network = context.network
        previous = list(network.frequencies)
        derived = [
            (collection, name, _derived(element))
            for collection in ("buses", "branches")
            for name, element in getattr(network, collection).items()
        ]
        network.frequencies = list(self.frequencies)
        self._undo = (previous, derived)

    def _revert(self, context: CommandContext) -> None:
        network = context.network
        previous, derived = self._undo
        network.frequencies = previous
        for collection, name, values in derived:
            _restore_derived(getattr(network, collection)[name], values)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

#: Every command class, in a stable order.
COMMAND_TYPES = (
    AddBus,
    UpdateBus,
    RemoveBus,
    AddBranch,
    UpdateBranch,
    RemoveBranch,
    AddSource,
    UpdateSource,
    RemoveSource,
    AddFault,
    UpdateFault,
    RemoveFault,
    AddBusType,
    UpdateBusType,
    RemoveBusType,
    AddBranchType,
    UpdateBranchType,
    RemoveBranchType,
    SetFrequencies,
)

#: The ``op`` value of every command.
COMMAND_OPS = tuple(cls.model_fields["op"].default for cls in COMMAND_TYPES)

#: Any command, discriminated by ``op`` -- the request schema of an edit.
CommandRequest = Annotated[Union[COMMAND_TYPES], Field(discriminator="op")]  # type: ignore[valid-type]

COMMAND_ADAPTER = TypeAdapter(CommandRequest)


def parse_command(data: Any) -> Command:
    """
    Build a command from its JSON form.

    Parameters
    ----------
    data : dict or Command
        A mapping with an ``op`` key, or a command, which is returned as is.

    Returns
    -------
    Command
        The command object.

    Raises
    ------
    CommandError
        If ``op`` names no command or a field is missing or invalid.

    Examples
    --------
    >>> parse_command({"op": "remove_bus", "name": "b7"}).label
    "remove bus 'b7'"
    """
    if isinstance(data, Command):
        return data
    op = data.get("op") if isinstance(data, dict) else None
    if op not in COMMAND_OPS:
        raise CommandError(
            f"unknown command op {op!r}; known ops: {', '.join(COMMAND_OPS)}"
        )
    try:
        return COMMAND_ADAPTER.validate_python(data)
    except ValidationError as exc:
        raise CommandError(f"invalid '{op}' command: {rejection_reason(exc)}") from exc
