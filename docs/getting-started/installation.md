# Installation

`groundinsight` requires **Python 3.12 or newer** and runs on Linux, macOS and
Windows. It is pure Python; every dependency ships prebuilt wheels, so no
compiler is needed. The steps below take you from an empty folder to an
installation that has checked itself.

## Create a virtual environment

A virtual environment keeps the packages of one project apart from those of
the system and of other projects. Create one in your project folder and
activate it:

=== "Linux and macOS"

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```

=== "Windows (PowerShell)"

    ```bash
    py -m venv .venv
    .venv\Scripts\Activate.ps1
    ```

=== "Windows (cmd)"

    ```bash
    py -m venv .venv
    .venv\Scripts\activate.bat
    ```

`python --version` inside the activated environment has to show 3.12 or
newer.

## Install from PyPI

=== "pip"

    ```bash
    pip install groundinsight
    ```

=== "uv"

    ```bash
    uv add groundinsight
    ```

=== "Poetry"

    ```bash
    poetry add groundinsight
    ```

This pulls in the runtime dependencies: `pydantic`, `numpy`, `scipy`, `sympy`,
`sqlalchemy`, `polars`, `matplotlib` and `pillow` (the image library
matplotlib uses; declared directly so that no release with a known security
advisory is installed alongside groundinsight).

## Optional extras

Runtime-optional features are exposed as extras. They keep the core install
lean and let you opt into the heavier dependencies on demand:

| Extra        | Purpose                                            | Pulls in       |
|--------------|----------------------------------------------------|----------------|
| pandapower   | `gi.from_pandapower` / `gi.preview_pandapower_import` to import existing distribution-network models, and `gi.read_shortcircuit_results` for their short-circuit results | `pandapower` |

```bash
pip install "groundinsight[pandapower]"
```

## Work in Jupyter or VS Code

The examples of this documentation are Jupyter notebooks, and a notebook is
the most convenient place for a study. groundinsight does not install a
notebook front end; add one to the same environment:

=== "JupyterLab"

    ```bash
    pip install jupyterlab
    jupyter lab
    ```

=== "VS Code"

    Install the *Python* and *Jupyter* extensions, open the project folder,
    open or create a `.ipynb` file and choose the interpreter of `.venv` with
    **Select Kernel** in the top right corner of the notebook. VS Code asks to
    install `ipykernel` into the environment the first time; accept.

## Verify the installation

`gi.run_reference_cases()` solves six networks whose answers are known in
closed form — a cable line with ideal and with finite earthing, the EN 50522
chain, the input impedance and the potential decay of a long ladder of bonded
stations, and the parallel decomposition at a fault — and compares. It takes
about a second:

```python
--8<-- "docs/snippets/installation_check.py:selftest"
```

Expected output (the version is the one you installed):

```text
groundinsight 0.6.0
line_ideal_bonding       agrees
line_finite_earthing     agrees
en50522_chain            agrees
ladder_input_impedance   agrees
ladder_potential_decay   agrees
parallel_decomposition   agrees
```

If a case does not agree, the installation computes wrong numbers, and
`table` says by how much (`rel_deviation` against `tolerance`).
[Verification and validation](../explanation/verification.md) explains what
each case checks. For a bug report, `gi.show_versions()` prints the versions
of groundinsight, Python and every dependency.

Continue with [Your first fault calculation](first-calculation.md).

## Install from source

Clone the repository and install it in editable mode with Poetry:

```bash
git clone https://github.com/Ce1ectric/groundinsight.git
cd groundinsight
poetry install
```

The default `poetry install` picks up the `[tool.poetry.dependencies]` group
plus the `dev` group (test tools and notebook kernel). The documentation tools
live in the optional `docs` group and have to be enabled explicitly:

```bash
poetry install --with docs
```

## Optional dependency groups

The project declares the following Poetry groups:

| Group  | Purpose                                     | Includes                                              |
|--------|---------------------------------------------|-------------------------------------------------------|
| dev    | Tests and notebook work                     | `pytest`, `pytest-cov`, `hypothesis`, `ipykernel`, `pandapower`, ... |
| docs   | Build the MkDocs site locally               | `mkdocs`, `mkdocs-material`, `mkdocstrings`, `mkdocs-jupyter`, `mkdocs-redirects`, ... |

Install a specific group with:

```bash
poetry install --with docs,dev
```

`CONTRIBUTING.md` in the repository describes how to run the tests and build
this documentation.

## The graphical front end

`groundinsight-gui` is a desktop and browser front end on top of the library:
network tables, background runs, the key figures of a fault and the
diagnostics, in German and English. It is a separate distribution in the
`gui/` folder of the repository, released together with the library and with
the same version. It is at an early stage (no login, a working skeleton of the
screens); see its README in the repository for installing and starting it.
