# groundinsight_gui/__main__.py
"""
``python -m groundinsight_gui`` -- start the GUI.

Browser mode is the default: NiceGUI serves the app on ``127.0.0.1`` and opens
it in the default browser, so it runs wherever Python runs (also on a machine
without a desktop toolkit). ``--native`` opens a desktop window through
pywebview instead (``pip install "groundinsight-gui[native]"``). Listening on
another interface (``--host 0.0.0.0``) is possible but deliberately not the
default: the phase-0 app has no login, and every visitor gets a full session.

The options are parsed into a :class:`LaunchOptions` first, so the command
line is testable without starting a server.
"""

import argparse
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from groundinsight.api import EXAMPLES

from .app import AppOptions, run_app
from .i18n import LANGUAGES

logger = logging.getLogger(__name__)

__all__ = ["LaunchOptions", "main", "parse_options"]


@dataclass(frozen=True)
class LaunchOptions:
    """
    The parsed command line.

    Attributes
    ----------
    native : bool
        Desktop window instead of a browser.
    host : str
        Interface to listen on.
    port : int, optional
        Port; chosen automatically when omitted.
    open_browser : bool
        Open the default browser (browser mode only).
    app : AppOptions
        What the app starts with.
    """

    native: bool = False
    host: str = "127.0.0.1"
    port: Optional[int] = None
    open_browser: bool = True
    app: AppOptions = field(default_factory=AppOptions)


def _parser() -> argparse.ArgumentParser:
    """The argument parser."""
    parser = argparse.ArgumentParser(
        prog="groundinsight-gui",
        description="Start the groundinsight GUI (browser mode unless --native).",
    )
    parser.add_argument("--native", action="store_true",
                        help="open a desktop window (needs groundinsight-gui[native])")
    parser.add_argument("--host", default="127.0.0.1",
                        help="interface to listen on (default 127.0.0.1: this computer only)")
    parser.add_argument("--port", type=int, default=None, help="port (default 8080, a free one with --native)")
    parser.add_argument("--no-browser", dest="open_browser", action="store_false",
                        help="do not open a browser (browser mode)")
    parser.add_argument("--lang", choices=LANGUAGES, default=LANGUAGES[0], help="interface language")
    parser.add_argument("--example", choices=sorted(EXAMPLES), default=None,
                        help="start with this template instead of the start page")
    parser.add_argument("--project", default=None, help="start with this project file")
    parser.add_argument("--backend", choices=("thread", "process"), default="thread",
                        help="where calculations run (default: a worker thread)")
    parser.add_argument("--max-job-runtime", dest="max_job_runtime_s", type=float, default=None,
                        help="runtime limit of a calculation in seconds")
    return parser


def parse_options(argv: Optional[List[str]] = None) -> LaunchOptions:
    """
    Parse the command line.

    Parameters
    ----------
    argv : list of str, optional
        Arguments without the program name; default ``sys.argv[1:]``.

    Returns
    -------
    LaunchOptions
        The options.

    Raises
    ------
    SystemExit
        For invalid arguments (argparse prints the usage), or a non-positive
        runtime limit.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.max_job_runtime_s is not None and not args.max_job_runtime_s > 0:
        parser.error("--max-job-runtime must be positive")
    if args.example and args.project:
        parser.error("--example and --project exclude each other")
    return LaunchOptions(
        native=args.native,
        host=args.host,
        port=args.port,
        open_browser=args.open_browser,
        app=AppOptions(
            language=args.lang,
            backend=args.backend,
            max_job_runtime_s=args.max_job_runtime_s,
            start_example=args.example,
            start_file=args.project,
        ),
    )


def main(argv: Optional[List[str]] = None) -> None:
    """
    Start the GUI and serve it until the window or the server is closed.

    Parameters
    ----------
    argv : list of str, optional
        Arguments without the program name; default ``sys.argv[1:]``.
    """
    options = parse_options(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logger.info("starting groundinsight GUI (%s mode)", "native" if options.native else "browser")
    run_app(
        options.app,
        native=options.native,
        host=options.host,
        port=options.port,
        open_browser=options.open_browser,
    )


# NiceGUI's native mode starts helper processes with the "spawn" method; they
# import this module as ``__mp_main__`` and must not start a second server.
if __name__ == "__main__":
    main()
