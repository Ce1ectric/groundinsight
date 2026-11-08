# scripts/check_api_inventory.py

"""
Check that every public name of ``groundinsight`` is rendered on the API pages.

Why the built inventory
    A name in ``groundinsight.__all__`` that no API page renders is invisible
    on the site: a user who meets a ``DCLimitWarning`` or wants to call
    ``gi.migrate_database`` finds nothing; audit pass 19 found such names
    (DOC4). Parsing the ``:::`` directives of the Markdown pages
    cannot settle the question on its own, because mkdocstrings leaves out
    members it considers undocumented (a constant without a docstring) or
    private. The inventory the build writes, ``site/objects.inv``, lists what
    was actually rendered, so this script runs after ``mkdocs build`` and
    reads it. ``tests/test_audit_pass19_docs_api.py`` keeps a static check of
    the directives in the test suite; the two are independent methods for the
    same statement.

What counts as rendered
    A public name ``N`` of the package ``P`` (``groundinsight`` or
    ``groundinsight.api``) counts when the inventory holds ``P.N`` or the
    canonical path of the object -- ``module.qualname`` of a class or
    function, the module of a module, or for a plain value (a constant
    without ``__module__``) the module of the package that defines ``N``.

Usage::

    mkdocs build --strict
    python scripts/check_api_inventory.py site/objects.inv

The exit status is 1 when a name is missing, 2 when the inventory cannot be
read, 0 otherwise.
"""

import argparse
import importlib
import logging
import sys
import types
import zlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("groundinsight.api_inventory")

#: The packages whose ``__all__`` must be rendered completely.
PUBLIC_PACKAGES: Tuple[str, ...] = ("groundinsight", "groundinsight.api")


def read_inventory(path: Path) -> Set[str]:
    """
    The object names of a Sphinx/mkdocstrings inventory (version 2).

    Parameters
    ----------
    path : Path
        ``objects.inv`` of a built site.

    Returns
    -------
    set of str
        Every object name the inventory lists.

    Raises
    ------
    ValueError
        If the file is not a version-2 inventory.
    """
    raw = path.read_bytes()
    parts = raw.split(b"\n", 4)
    if len(parts) < 5 or not parts[0].startswith(b"# Sphinx inventory version 2"):
        raise ValueError(f"{path} is not a version-2 object inventory")
    body = zlib.decompress(parts[4]).decode("utf-8")
    names = set()
    for line in body.splitlines():
        fields = line.split(" ", 4)
        if len(fields) >= 4:
            names.add(fields[0])
    return names


def _package_modules(package: types.ModuleType) -> List[types.ModuleType]:
    """
    The imported modules of a package, the package first.

    Parameters
    ----------
    package : module
        A package such as ``groundinsight``.

    Returns
    -------
    list of module
        The package and every submodule found in ``sys.modules``.
    """
    prefix = package.__name__ + "."
    found = [package]
    found.extend(
        module for name, module in sorted(sys.modules.items())
        if name.startswith(prefix) and isinstance(module, types.ModuleType)
    )
    return found


def accepted_names(package_name: str, name: str) -> Set[str]:
    """
    Inventory names under which a public name counts as rendered.

    Parameters
    ----------
    package_name : str
        ``"groundinsight"`` or ``"groundinsight.api"``.
    name : str
        A name of the package's ``__all__``.

    Returns
    -------
    set of str
        ``package.name`` plus the canonical path(s) of the object.
    """
    package = importlib.import_module(package_name)
    obj = getattr(package, name)
    accepted = {f"{package_name}.{name}"}
    if isinstance(obj, types.ModuleType):
        accepted.add(obj.__name__)
        return accepted
    module = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        # A class or a function: the module that defines it.
        accepted.add(f"{module}.{qualname}")
        return accepted
    # A plain value: the modules that hold this very object under this name.
    root = importlib.import_module(package_name.split(".")[0])
    for candidate in _package_modules(root):
        if getattr(candidate, name, None) is obj:
            accepted.add(f"{candidate.__name__}.{name}")
    return accepted


def missing_names(inventory: Set[str], packages: Iterable[str] = PUBLIC_PACKAGES) -> Dict[str, List[str]]:
    """
    Public names that the inventory does not list.

    Parameters
    ----------
    inventory : set of str
        Names from :func:`read_inventory`.
    packages : iterable of str, optional
        Packages whose ``__all__`` is checked.

    Returns
    -------
    dict of str to list of str
        Package -> missing names, sorted; every checked package is a key.
    """
    result: Dict[str, List[str]] = {}
    for package_name in packages:
        package = importlib.import_module(package_name)
        absent = [
            name for name in package.__all__
            if not (accepted_names(package_name, name) & inventory)
        ]
        result[package_name] = sorted(absent)
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Command-line entry point.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        0 when every public name is rendered, 1 when names are missing, 2
        when the inventory cannot be read.
    """
    parser = argparse.ArgumentParser(
        description="Check that every public name of groundinsight is on an API page."
    )
    parser.add_argument("inventory", type=Path, help="objects.inv of the built site")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        inventory = read_inventory(args.inventory)
    except (OSError, ValueError, zlib.error) as exc:
        logger.error("cannot read %s: %s", args.inventory, exc)
        return 2
    missing = missing_names(inventory)
    status = 0
    for package_name, names in missing.items():
        total = len(importlib.import_module(package_name).__all__)
        if names:
            status = 1
            logger.error(
                "%s: %d of %d public names are not rendered on any API page: %s",
                package_name, len(names), total, ", ".join(names),
            )
        else:
            logger.info("%s: all %d public names are rendered", package_name, total)
    return status


if __name__ == "__main__":
    sys.exit(main())
