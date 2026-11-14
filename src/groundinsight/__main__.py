# __main__.py
"""
``python -m groundinsight`` -- the command line without the console script.

See :mod:`groundinsight.cli`. The guard keeps a process that imports this
module (a ``spawn`` child of a job runner re-imports the main module) from
running the command line a second time.
"""

import sys

from groundinsight.cli import main

if __name__ == "__main__":
    sys.exit(main())
