# integrations/__init__.py

"""
Couplings to other programs.

Each submodule couples one program and imports it lazily, so groundinsight
installs and runs without it:

- :mod:`groundinsight.integrations.groundfield` -- earthing characteristics
  from the field solver groundfield; its BusType JSON document is read
  without groundfield installed.
"""
