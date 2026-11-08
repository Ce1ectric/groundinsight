# docs/snippets/installation_check.py
"""
The self-test of the step "Verify the installation" of the installation page.

``gi.run_reference_cases()`` solves six networks whose answers are known in
closed form and compares; every row has to say ``agrees = True``. It takes
about a second and needs nothing but the package.

Run it as a script: ``python docs/snippets/installation_check.py``.
"""

# --8<-- [start:selftest]
import groundinsight as gi

print("groundinsight", gi.__version__)
table = gi.run_reference_cases()
for case, agrees in table.select("case", "agrees").iter_rows():
    print(f"{case:<24} {'agrees' if agrees else 'DOES NOT AGREE'}")
assert table["agrees"].all(), "a reference case does not agree -- see the table"
# --8<-- [end:selftest]
