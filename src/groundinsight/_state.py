# _state.py

"""
Exact snapshot and restore of the network-level state a solve writes.

The studies mutate the network they are given -- they have to: a sweep writes
a bus impedance, an outage flips an ``active`` flag, a search scales ``rho``,
and every step ends in a ``run_fault`` that replaces the stored result, the
active fault, possibly the path set, and the assembled electrical network.
Each study already undoes its *element* edits in its own ``finally`` block.
What this module adds is the other half, for the two cases that promise an
untouched network: a cancelled study, and the studies that
document "the network is left exactly as found".

:class:`NetworkState` records the network-level state *generically* rather
than field by field:

* every field value of the network, with the content of the mappings and
  lists among them (``results``, ``paths``, ``faults``, ...), so that a dict
  that was rebound is put back and a dict that was edited in place is
  refilled -- in its original order, which the serialised form depends on;
* every private attribute of the network (the assembled system, the path
  fingerprint, and whatever a later release adds), with the content of the
  containers among them;
* the private attributes of every fault (the ``active`` flag lives there);
* the pydantic bookkeeping of which fields were set explicitly.

The restore writes the original *objects* back, bypassing validation on
assignment: they are the validated originals, a validated copy would change
their identity (a caller holding ``net.results`` would hold a dict the
network no longer uses), and re-running the model validators could recompute
derived private state. Element objects (buses, branches, sources) are not
recorded: their edits are the studies' own to undo, and recording them would
cost ``elements x frequencies`` copies on every study call.
"""

import contextlib
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .models.core_models import Network
from .progress import StudyCancelled

__all__ = ["NetworkState", "restore_always", "restore_on_cancel"]


def _content(value: Any) -> Optional[Tuple[str, Any]]:
    """
    The content of a container, for an in-place refill later.

    Parameters
    ----------
    value : object
        A field or private-attribute value.

    Returns
    -------
    tuple or None
        ``("dict", items)``, ``("list", items)`` or ``("set", items)`` for
        the three mutable containers the models use, ``None`` otherwise.
    """
    if isinstance(value, dict):
        return ("dict", list(value.items()))
    if isinstance(value, list):
        return ("list", list(value))
    if isinstance(value, set):
        return ("set", set(value))
    return None


def _refill(value: Any, content: Optional[Tuple[str, Any]]) -> None:
    """
    Put a container's recorded content back, in place.

    Parameters
    ----------
    value : object
        The container object that was recorded.
    content : tuple or None
        What :func:`_content` returned for it.
    """
    if content is None:
        return
    kind, items = content
    if kind == "dict":
        value.clear()
        value.update(items)
    elif kind == "list":
        value[:] = items
    else:
        value.clear()
        value.update(items)


class NetworkState:
    """
    The network-level state of a :class:`Network`, recorded for an exact restore.

    Parameters
    ----------
    network : Network
        The network to record. Recording is ``O(elements)`` -- references and
        shallow container copies, no element copies.
    """

    def __init__(self, network: Network) -> None:
        self.network = network
        self._fields: List[Tuple[str, Any, Optional[Tuple[str, Any]]]] = [
            (name, value, _content(value)) for name, value in network.__dict__.items()
        ]
        private = network.__pydantic_private__ or {}
        self._private: List[Tuple[str, Any, Optional[Tuple[str, Any]]]] = [
            (name, value, _content(value)) for name, value in private.items()
        ]
        self._fields_set = set(network.__pydantic_fields_set__)
        self._fault_private: List[Tuple[Any, Dict[str, Any]]] = [
            (fault, dict(fault.__pydantic_private__ or {}))
            for fault in network.faults.values()
        ]

    def restore(self) -> None:
        """Write the recorded state back into the network, objects and order exact."""
        network = self.network
        for name, value, content in self._fields:
            _refill(value, content)
            network.__dict__[name] = value
        private = network.__pydantic_private__
        if private is not None:
            for name, value, content in self._private:
                _refill(value, content)
                private[name] = value
        fields_set = network.__pydantic_fields_set__
        fields_set.clear()
        fields_set.update(self._fields_set)
        for fault, saved in self._fault_private:
            if fault.__pydantic_private__ is not None:
                fault.__pydantic_private__.update(saved)


@contextlib.contextmanager
def restore_on_cancel(network: Network) -> Iterator[NetworkState]:
    """
    Restore ``network`` exactly if the block is cancelled.

    Parameters
    ----------
    network : Network
        The network the enclosed study runs on.

    Yields
    ------
    NetworkState
        The recorded state.

    Raises
    ------
    StudyCancelled
        Re-raised after the restore.
    """
    state = NetworkState(network)
    try:
        yield state
    except StudyCancelled:
        state.restore()
        raise


@contextlib.contextmanager
def restore_always(network: Network) -> Iterator[NetworkState]:
    """
    Restore ``network`` exactly when the block ends, however it ends.

    Parameters
    ----------
    network : Network
        The network the enclosed study runs on.

    Yields
    ------
    NetworkState
        The recorded state.
    """
    state = NetworkState(network)
    try:
        yield state
    finally:
        state.restore()
