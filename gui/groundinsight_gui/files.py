# groundinsight_gui/files.py
"""
A file manager inside the app: browse folders, pick a project file, name one.

Opening and saving used to take a typed path. The dialog here lists the
folders and project files (``*.json``) of a directory, walks into a folder
on a click and up with the breadcrumb or the *up* button, and either opens
the file clicked (*open*) or saves under the name typed into the folder shown
(*save*). It runs in the browser as well as in the native window, because it
lists the files on the machine the app runs on -- the browser of a local app
cannot hand out paths, and a native file dialog exists only in the native
mode. The path field stays for a path pasted from elsewhere.

The listing is a pure function of the directory (:func:`list_directory`), so
it is tested without a browser; hidden entries are left out, folders come
first, both sorted case-insensitively.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from nicegui import ui

logger = logging.getLogger(__name__)

__all__ = ["FileDialog", "breadcrumbs", "list_directory", "start_directory"]

#: The suffix of project files.
SUFFIX = ".json"


def list_directory(directory: Path, suffix: str = SUFFIX) -> Tuple[List[Path], List[Path]]:
    """
    The folders and the files with ``suffix`` of a directory.

    Parameters
    ----------
    directory : Path
        The directory.
    suffix : str, optional
        File suffix shown (case-insensitive); default ``.json``.

    Returns
    -------
    tuple of (list of Path, list of Path)
        Folders and files, each sorted by name case-insensitively; hidden
        entries (a leading dot) are left out, and so is anything that cannot
        be read.

    Examples
    --------
    >>> import tempfile
    >>> root = Path(tempfile.mkdtemp())
    >>> (root / "b.json").write_text("{}"); (root / "A").mkdir(); (root / "x.txt").write_text("")
    2
    0
    >>> folders, files = list_directory(root)
    >>> [p.name for p in folders], [p.name for p in files]
    (['A'], ['b.json'])
    """
    folders: List[Path] = []
    files: List[Path] = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return folders, files
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                folders.append(Path(entry.path))
            elif entry.is_file() and entry.name.lower().endswith(suffix):
                files.append(Path(entry.path))
        except OSError:
            continue
    key = lambda p: p.name.lower()  # noqa: E731 -- a sort key
    return sorted(folders, key=key), sorted(files, key=key)


def breadcrumbs(directory: Path) -> List[Path]:
    """
    The directory and its parents, from the root down.

    Parameters
    ----------
    directory : Path
        An absolute directory.

    Returns
    -------
    list of Path
        ``[/, /Users, /Users/name, ...]``.

    Examples
    --------
    >>> [str(p) for p in breadcrumbs(Path("/a/b"))]
    ['/', '/a', '/a/b']
    """
    return [*reversed(directory.parents), directory]


def start_directory(location: Optional[str], last: Optional[Path]) -> Path:
    """
    Where the dialog opens: the project's folder, the last one used, or home.

    Parameters
    ----------
    location : str, optional
        The project's file path, if it has one.
    last : Path, optional
        The folder of the last file opened or saved.

    Returns
    -------
    Path
        An existing directory.
    """
    for candidate in (Path(location).expanduser().parent if location else None, last):
        if candidate is not None and candidate.is_dir():
            return candidate.resolve()
    return Path.home()


class FileDialog:
    """
    The file manager dialog of the app.

    Parameters
    ----------
    t : callable
        The app's translation function (text key to text).
    mode : {"open", "save"}
        Open the file clicked, or save under the name typed.
    directory : Path
        The folder shown first.
    on_choose : callable
        Called with the chosen path (a ``str``); may be a coroutine function.
    name : str, optional
        The file name proposed when saving.
    """

    def __init__(self, t: Callable[..., str], mode: str, directory: Path,
                 on_choose: Callable[[str], Any], name: str = "") -> None:
        self.t = t
        self.mode = mode
        self.directory = directory
        self.on_choose = on_choose
        self.name = name
        self.dialog: Any = None
        self.name_input: Any = None

    def open(self) -> None:
        """Build and open the dialog."""
        with ui.dialog() as self.dialog, ui.card().classes("w-[40rem] gap-2").mark(f"file-dialog-{self.mode}"):
            ui.label(self.t("open_project") if self.mode == "open" else self.t("save_as_title")).classes(
                "text-base font-medium")
            self.listing()
            if self.mode == "save":
                self.name_input = ui.input(self.t("file_name"), value=self.name,
                                           on_change=lambda _: self.listing.refresh()).classes("w-full").mark(
                    "file-name")
                self.listing.refresh()  # the listing was drawn before the name existed
            with ui.row().classes("w-full items-end gap-2 no-wrap"):
                path = ui.input(self.t("path"), placeholder=self.t("path_example")).classes("grow").mark(
                    "file-path")
                ui.button(self.t("open") if self.mode == "open" else self.t("save"),
                          on_click=lambda: self._confirm(path.value)).props("no-caps").mark("file-confirm")
                ui.button(self.t("cancel_dialog"), on_click=self.dialog.close).props("flat no-caps")
        self.dialog.on_value_change(lambda e: None if e.value else self.dialog.delete())
        self.dialog.open()

    @ui.refreshable_method
    def listing(self) -> None:
        """Breadcrumb, *up*, folders and project files of the current folder."""
        with ui.row().classes("items-center gap-1 flex-wrap").mark("file-breadcrumbs"):
            ui.button(icon="arrow_upward", on_click=lambda: self.go(self.directory.parent)).props(
                f'flat dense round aria-label="{self.t("folder_up")}"').mark("file-up")
            for crumb in breadcrumbs(self.directory):
                ui.button(crumb.name or str(crumb), on_click=lambda _, c=crumb: self.go(c)).props(
                    "flat dense no-caps").classes("text-xs").mark(f"file-crumb-{crumb}")
        folders, files = list_directory(self.directory)
        with ui.scroll_area().classes("w-full h-64 border rounded"):
            for folder in folders:
                with ui.item(on_click=lambda _, f=folder: self.go(f)).mark(f"file-folder-{folder.name}"):
                    with ui.item_section().props("avatar"):
                        ui.icon("folder", color="amber-8")
                    ui.item_section(folder.name)
            for file in files:
                with ui.item(on_click=lambda _, f=file: self.pick(f)).mark(f"file-file-{file.name}"):
                    with ui.item_section().props("avatar"):
                        ui.icon("description", color="blue-grey-6")
                    ui.item_section(file.name)
            if not folders and not files:
                ui.label(self.t("folder_empty")).classes("text-xs text-gray-500 p-2")
        if self.mode == "save" and self.name_input is not None and self._target().exists():
            ui.label(self.t("file_replaced")).classes("text-xs text-amber-800").mark("file-replaced")

    def go(self, directory: Path) -> None:
        """
        Show another folder.

        Parameters
        ----------
        directory : Path
            The folder; ignored if it cannot be listed.
        """
        if directory.is_dir():
            self.directory = directory
            self.listing.refresh()

    async def pick(self, file: Path) -> None:
        """
        A file was clicked: open it, or take its name for saving.

        Parameters
        ----------
        file : Path
            The file.
        """
        if self.mode == "open":
            await self._choose(str(file))
        else:
            self.name_input.set_value(file.name)

    def _target(self) -> Path:
        """The file a save writes: the name typed, in the folder shown, ``.json`` added."""
        name = str(self.name_input.value or "").strip()
        if name and not name.lower().endswith(SUFFIX):
            name += SUFFIX
        return self.directory / name

    async def _confirm(self, typed: Optional[str]) -> None:
        """The confirm button: a typed path wins, else the folder and the name."""
        typed = (typed or "").strip()
        if typed:
            await self._choose(typed)
        elif self.mode == "save" and str(self.name_input.value or "").strip():
            await self._choose(str(self._target()))

    async def _choose(self, path: str) -> None:
        """Hand the path on; the dialog closes when the caller accepted it."""
        outcome = self.on_choose(path)
        if hasattr(outcome, "__await__"):
            outcome = await outcome
        # Opening a project rebuilds the page, and the dialog with it.
        if outcome is not False and not getattr(self.dialog, "is_deleted", False):
            self.dialog.close()
