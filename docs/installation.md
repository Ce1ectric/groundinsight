# Installation

`groundinsight` requires **Python 3.12 or newer**. It is published on PyPI and
can be installed with any standard Python package manager.

## Install from PyPI

=== "pip"

    ```bash
    pip install groundinsight
    ```

=== "Poetry"

    ```bash
    poetry add groundinsight
    ```

=== "uv"

    ```bash
    uv add groundinsight
    ```

This pulls in the runtime dependencies: `pydantic`, `numpy`, `scipy`, `sympy`,
`sqlalchemy`, `polars` and `matplotlib`.

## Install from source

Clone the repository and install it in editable mode using Poetry:

```bash
git clone https://github.com/Ce1ectric/groundinsight.git
cd groundinsight
poetry install
```

The default `poetry install` picks up the `[tool.poetry.dependencies]` group
plus the `dev` group (test and formatting tools). The documentation extras live
in the optional `docs` group and have to be enabled explicitly:

```bash
poetry install --with docs
```

## Optional dependency groups

The project declares the following Poetry groups:

| Group  | Purpose                                     | Includes                                              |
|--------|---------------------------------------------|-------------------------------------------------------|
| dev    | Test, format and notebook work              | `pytest`, `pytest-cov`, `black`, `ipykernel`, ...    |
| docs   | Build the MkDocs site locally               | `mkdocs`, `mkdocs-material`, `mkdocstrings`, ...     |

Install a specific group with:

```bash
poetry install --with docs,dev
```

## Verify the installation

Open a Python shell and import the package:

```python
import groundinsight as gi
print(gi.__version__)
```

If the import succeeds and the version string is printed, `groundinsight` is
ready to use. Continue with the [Quickstart](quickstart.md) for a first
end-to-end example.
