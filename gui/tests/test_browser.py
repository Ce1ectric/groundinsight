# gui/tests/test_browser.py
"""
The network canvas in a real browser: drawing with the mouse.

The app runs as ``python -m groundinsight_gui`` in a subprocess; Playwright
drives Chromium against it. What the simulated ``User`` of
``tests/test_plan.py`` cannot see is checked here: that Vue Flow renders the
diagram from the vendored bundle, that a click, a drag and a double-click
reach the server as the right events, that the server's answer is drawn, and
that the browser reports no error.

Screenshots go to ``tests/screens/`` for review. Where reference images for
this platform exist (``tests/screens/reference/<platform>/``), each screenshot
is compared with its reference: the mean difference per pixel must stay
below :data:`MAX_MEAN_DIFFERENCE` (font rendering and anti-aliasing differ
between machines, a broken canvas differs by far more). Without a reference
the comparison is skipped; ``GI_UPDATE_SCREENS=1`` writes the references.

The tests are skipped when Playwright or its Chromium is not installed
(``python -m playwright install chromium``).
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.browser

GUI_ROOT = Path(__file__).resolve().parents[1]
SCREENS = Path(__file__).resolve().parent / "screens"
REFERENCES = SCREENS / "reference" / sys.platform

#: Mean absolute difference per pixel and channel (0..255) a screenshot may
#: have from its reference.
MAX_MEAN_DIFFERENCE = 6.0

VIEWPORT = {"width": 1440, "height": 900}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """The app on a free port; each page load opens a fresh session."""
    port = _free_port()
    # The app's output goes to a file: an unread pipe fills up and blocks it.
    log = (tmp_path_factory.mktemp("app") / "app.log").open("w", encoding="utf-8")
    # NiceGUI starts in its screen-test mode when it sees pytest's variables.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "NICEGUI_"))}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(GUI_ROOT), env.get("PYTHONPATH")]))
    process = subprocess.Popen(
        [sys.executable, "-m", "groundinsight_gui", "--example", "minimal", "--port", str(port),
         "--no-browser"],
        env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(300):
            try:
                urllib.request.urlopen(url, timeout=1)
                break
            except OSError:
                if process.poll() is not None:
                    log.close()
                    pytest.fail(f"the app exited: {Path(log.name).read_text(encoding='utf-8')}")
                time.sleep(0.1)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 -- any launch failure means no usable browser
            pytest.skip(f"Chromium is not available: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def page(browser, server):
    context = browser.new_context(viewport=VIEWPORT, locale="de-DE")
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
    page.goto(server)
    page.locator(".vue-flow__node").first.wait_for()
    yield page
    context.close()


def _node(page, name):
    return page.locator(f".vue-flow__node[data-id='{name}']")


def _centre(locator):
    box = locator.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


TOOLS = {"Auswahl": "select", "Bus": "bus", "Quell-Bus": "source_bus", "Zweig": "branch", "Fehler": "fault"}


def _tool(page, label):
    """Pick a palette tool and wait until the canvas has it (a round trip to the server)."""
    page.get_by_role("button", name=label, exact=True).first.click()
    page.locator(f".gi-canvas.gi-tool-{TOOLS[label]}").wait_for()


def _screenshot(page, name: str) -> None:
    """Save a screenshot and compare it with this platform's reference."""
    SCREENS.mkdir(exist_ok=True)
    path = SCREENS / f"{name}.png"
    page.wait_for_timeout(400)
    page.screenshot(path=str(path))
    reference = REFERENCES / f"{name}.png"
    if os.environ.get("GI_UPDATE_SCREENS"):
        REFERENCES.mkdir(parents=True, exist_ok=True)
        reference.write_bytes(path.read_bytes())
        return
    if not reference.exists():
        return
    from PIL import Image, ImageChops, ImageStat

    shot, ref = Image.open(path).convert("RGB"), Image.open(reference).convert("RGB")
    assert shot.size == ref.size, (name, shot.size, ref.size)
    mean = sum(ImageStat.Stat(ImageChops.difference(shot, ref)).mean) / 3
    assert mean < MAX_MEAN_DIFFERENCE, f"{name}: mean difference {mean:.2f} from the reference"


def test_the_plan_renders_the_diagram(page):
    assert page.locator(".vue-flow__node").count() == 3
    assert page.locator("[data-branch]").count() == 2
    assert page.locator(".vue-flow__minimap").count() == 1
    _screenshot(page, "plan_minimal")
    assert page.errors == []


def test_drawing_a_bus_a_branch_and_a_fault_with_the_mouse(page):
    _tool(page, "Bus")
    pane = page.locator(".vue-flow__pane").bounding_box()
    page.mouse.click(pane["x"] + pane["width"] * 0.5, pane["y"] + pane["height"] * 0.8)
    _node(page, "B1").wait_for()
    assert page.locator(".vue-flow__node").count() == 4

    _tool(page, "Zweig")
    x0, y0 = _centre(_node(page, "bus1"))
    x1, y1 = _centre(_node(page, "B1"))
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=15)
    page.mouse.up()
    page.locator("[data-branch='L1']").wait_for()

    _tool(page, "Fehler")
    _node(page, "B1").click()
    _node(page, "B1").locator(".gi-flash").wait_for()
    _screenshot(page, "drawn_bus_branch_fault")
    assert page.errors == []


def test_dragging_a_symbol_moves_it_on_the_raster(page):
    node = _node(page, "bus2")
    before = node.bounding_box()
    x, y = _centre(node)
    page.mouse.move(x, y)
    page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(x + 6 * k, y + 9 * k)
    page.mouse.up()
    page.wait_for_timeout(500)
    after = node.bounding_box()
    assert after["y"] > before["y"] + 20
    page.get_by_text("Netz", exact=True).click()  # the tables show the same session
    page.get_by_role("tab", name="Netzplan").click()
    assert page.errors == []


def test_double_click_opens_the_dialog_and_apply_changes_the_bus(page):
    _node(page, "bus2").dblclick()
    dialog = page.locator(".q-dialog")
    dialog.wait_for()
    rho = dialog.get_by_label("ρ / Ω·m")
    rho.fill("333")
    dialog.get_by_role("button", name="Übernehmen").click()
    dialog.wait_for(state="hidden")
    page.get_by_text("333", exact=True).first.wait_for()  # the properties panel
    _screenshot(page, "after_dialog")
    assert page.errors == []


def test_delete_removes_a_connected_bus(page):
    _node(page, "bus3").click()
    page.keyboard.press("Delete")  # at once: the canvas knows what was clicked
    _node(page, "bus3").wait_for(state="detached")
    assert page.locator("[data-branch]").count() == 1
    assert page.errors == []


def _selected(page):
    return sorted(page.locator(".gi-node.gi-sel").evaluate_all("ns => ns.map(n => n.dataset.bus)"))


def test_shift_click_and_a_frame_select_several_and_the_group_moves_and_goes(page):
    _node(page, "bus1").click()
    _node(page, "bus2").click(modifiers=["Shift"])
    page.get_by_text("2 Elemente ausgewählt").wait_for()
    assert _selected(page) == ["bus1", "bus2"]
    _node(page, "bus1").click(modifiers=["Shift"])  # takes it out again
    page.locator("[data-bus='bus1']:not(.gi-sel)").wait_for()
    assert _selected(page) == ["bus2"]

    # A frame, drawn with Shift held, around bus2 and bus3.
    (x2, y2), (x3, y3) = _centre(_node(page, "bus2")), _centre(_node(page, "bus3"))
    page.keyboard.down("Shift")
    page.mouse.move(min(x2, x3) - 40, min(y2, y3) - 60)
    page.mouse.down()
    page.mouse.move(max(x2, x3) + 40, max(y2, y3) + 60, steps=10)
    page.mouse.up()
    page.keyboard.up("Shift")
    page.get_by_text("3 Elemente ausgewählt").wait_for()  # two buses and the branch between them
    assert _selected(page) == ["bus2", "bus3"]

    # Dragging one of them moves both, by the same amount.
    before = [_centre(_node(page, n)) for n in ("bus1", "bus2", "bus3")]
    page.mouse.move(x2, y2)
    page.mouse.down()
    for k in range(1, 11):
        page.mouse.move(x2, y2 + 8 * k)
    page.mouse.up()
    page.wait_for_timeout(600)
    after = [_centre(_node(page, n)) for n in ("bus1", "bus2", "bus3")]
    assert after[0] == before[0]
    assert after[1][1] - before[1][1] == pytest.approx(after[2][1] - before[2][1], abs=1) and after[1][1] > before[1][1] + 40
    assert _selected(page) == ["bus2", "bus3"]

    page.keyboard.press("Delete")
    _node(page, "bus3").wait_for(state="detached")
    assert page.locator(".vue-flow__node").count() == 1 and page.locator("[data-branch]").count() == 0
    assert page.errors == []


def test_escape_undo_redo_and_select_all_from_the_keyboard(page):
    _tool(page, "Bus")
    pane = page.locator(".vue-flow__pane").bounding_box()
    page.mouse.click(pane["x"] + pane["width"] * 0.5, pane["y"] + pane["height"] * 0.8)
    _node(page, "B1").wait_for()
    page.keyboard.press("Escape")
    page.locator(".gi-canvas.gi-tool-select").wait_for()
    page.locator("[data-bus='B1']:not(.gi-sel)").wait_for()  # and nothing is selected
    page.keyboard.press("ControlOrMeta+z")
    _node(page, "B1").wait_for(state="detached")
    page.keyboard.press("ControlOrMeta+Shift+z")
    _node(page, "B1").wait_for()
    page.mouse.click(pane["x"] + 30, pane["y"] + 30)  # the canvas has the keys again
    page.keyboard.press("ControlOrMeta+a")
    page.get_by_text("6 Elemente ausgewählt").wait_for()  # four buses, two branches
    assert page.errors == []


def test_elk_lays_the_network_out_in_the_browser(page):
    before = [_centre(_node(page, n)) for n in ("bus1", "bus2", "bus3")]
    page.get_by_role("button", name="Auto-Layout").click()
    page.get_by_text("Anordnen mit ELK (geschichtet)").click()
    page.wait_for_timeout(1500)
    after = [_centre(_node(page, n)) for n in ("bus1", "bus2", "bus3")]
    assert after != before
    assert page.errors == []


def test_a_solved_ring_coloured_by_its_epr(page):
    page.get_by_role("button", name="Neu").click()
    page.get_by_text("MS-Ring", exact=True).click()
    page.locator(".vue-flow__node[data-id='b20']").wait_for()
    page.get_by_role("tab", name="Ergebnisse").click()
    page.get_by_role("button", name="Rechnen").click()
    page.get_by_text("Revision", exact=False).first.wait_for(timeout=60000)
    page.get_by_role("tab", name="Netzplan").click()
    page.get_by_role("button", name="Einfärben").click()
    page.get_by_text("Ergebnis: EPR (RMS)").click()
    page.get_by_text("Ergebnis: EPR (RMS)").first.wait_for()
    # Second way: the ends of the scale sit at the buses with the smallest and
    # the largest RMS EPR of the library's own solve.
    import warnings

    import groundinsight as gi
    from groundinsight.api import EXAMPLES

    twin = EXAMPLES["mv_ring"]()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        gi.run_fault(twin, "F1")
    frame = twin.res_buses("F1")
    rms = frame.filter(frame["frequency_Hz"].is_null())
    epr = dict(zip(rms["bus_name"], rms["EPR_V"], strict=True))

    def fill(bus):
        return page.locator(f"[data-bus='{bus}']").evaluate("e => getComputedStyle(e).backgroundColor")

    assert fill(min(epr, key=epr.get)) == "rgb(68, 1, 84)"  # first stop of the scale
    assert fill(max(epr, key=epr.get)) == "rgb(253, 231, 37)"  # last stop
    _screenshot(page, "ring_epr")
    assert page.errors == []
