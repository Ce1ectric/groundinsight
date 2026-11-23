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


def _solve_in_the_results_tab(page) -> None:
    """Results tab, Run, wait for the result (the plan has a Run button of its own)."""
    page.get_by_role("tab", name="Ergebnisse").click()
    page.get_by_role("button", name="Alle Fehlerorte").wait_for()  # the results tab is in front
    page.locator(".q-btn:not(.gi-plan-run)", has_text="Rechnen").click()
    page.get_by_text("Revision", exact=False).first.wait_for(timeout=60000)


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


def test_the_dialog_takes_the_keyboard_and_reports_a_wrong_entry_at_its_field(page):
    _node(page, "bus2").dblclick()
    dialog = page.locator(".q-dialog")
    dialog.wait_for()
    page.wait_for_timeout(400)
    # The name has the focus: typing replaces nothing else.
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Name"
    rho = dialog.get_by_label("ρ / Ω·m")
    rho.fill("abc")
    rho.press("Enter")
    dialog.get_by_text("Keine Zahl: abc").wait_for()  # under the field, not in a toast
    assert page.locator(".q-notification").count() == 0
    _screenshot(page, "dialog_field_error")
    rho.fill("412")
    rho.press("Enter")
    dialog.wait_for(state="hidden")
    page.get_by_text("412", exact=True).first.wait_for()  # the properties panel
    assert page.errors == []


def test_delete_removes_a_connected_bus(page):
    _node(page, "bus3").click()
    page.keyboard.press("Delete")  # at once: the canvas knows what was clicked
    _node(page, "bus3").wait_for(state="detached")
    assert page.locator("[data-branch]").count() == 1
    assert page.errors == []


@pytest.mark.parametrize("size", [(1440, 900), (1280, 800)])
def test_the_plan_fills_the_window_below_a_flat_header(browser, server, tmp_path, size):
    """The plan is the main view: it starts high up and ends at the bottom of the window, without scrolling."""
    import shutil

    # A project file deep in the file system: its path must not push the toolbar into two lines.
    folder = tmp_path / "ein" / "sehr" / "langer" / "pfad" / "zu" / "einem" / "projekt" / "im" / "dateisystem"
    folder.mkdir(parents=True)
    import groundinsight as gi
    from groundinsight.api import EXAMPLES

    gi.save_network_to_json(EXAMPLES["minimal"](), folder / "strang.json")
    context = browser.new_context(viewport={"width": size[0], "height": size[1]}, locale="de-DE")
    page = context.new_page()
    try:
        page.goto(server)
        page.locator(".vue-flow__node").first.wait_for()
        page.get_by_role("button", name="Öffnen").click()
        page.get_by_label("Dateipfad (.json)").fill(str(folder / "strang.json"))
        page.locator(".q-dialog").get_by_role("button", name="Öffnen").click()
        page.get_by_text("strang.json").wait_for()
        page.locator(".vue-flow__node").first.wait_for()
        page.wait_for_timeout(500)
        canvas = page.locator(".gi-canvas").bounding_box()
        toolbar = page.locator(".gi-toolbar").bounding_box()
        assert toolbar["height"] <= 56  # one line, whatever the path
        assert canvas["y"] <= 150  # was 264 of 900
        assert size[1] - 16 <= canvas["y"] + canvas["height"] <= size[1]
        assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")
        # The title names the file; the whole path is its tooltip.
        assert page.locator(".gi-toolbar").get_by_text(str(folder)).count() == 0
        page.get_by_text("strang.json").hover()
        page.get_by_text(str(folder / "strang.json")).wait_for()
    finally:
        context.close()
    shutil.rmtree(tmp_path / "ein")


def test_the_browser_asks_before_a_page_with_unsaved_changes_is_left(page):
    asked = []
    page.on("dialog", lambda dialog: (asked.append(dialog.type), dialog.accept()))
    page.reload()  # nothing changed: no question
    page.locator(".vue-flow__node").first.wait_for()
    assert asked == []
    _tool(page, "Bus")
    pane = page.locator(".vue-flow__pane").bounding_box()
    page.mouse.click(pane["x"] + pane["width"] * 0.5, pane["y"] + pane["height"] * 0.8)
    _node(page, "B1").wait_for()
    page.locator(".gi-dirty:not(.hidden)").wait_for()
    page.reload()
    page.locator(".vue-flow__node").first.wait_for()
    assert asked == ["beforeunload"]


def test_an_element_drawn_or_clicked_in_the_plan_is_the_selected_row_of_its_table(page):
    page.get_by_role("tab", name="Netz", exact=True).click()  # the tables exist in the browser
    page.locator(".ag-row").first.wait_for()
    page.get_by_role("tab", name="Netzplan").click()
    _tool(page, "Zweig")
    x0, y0 = _centre(_node(page, "bus1"))
    x1, y1 = _centre(_node(page, "bus3"))
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=15)
    page.mouse.up()
    page.locator("[data-branch='L1']").wait_for()  # the new branch is selected in the plan ...
    page.get_by_role("tab", name="Netz", exact=True).click()
    page.locator(".ag-row-selected", has_text="L1").wait_for()  # ... and is the selected row of the branches
    page.get_by_role("tab", name="Netzplan").click()
    _tool(page, "Auswahl")
    _node(page, "bus2").click()
    page.get_by_text("Bus bus2", exact=True).wait_for()
    page.get_by_role("tab", name="Netz", exact=True).click()
    page.locator(".ag-row-selected", has_text="bus2").first.wait_for()
    assert page.errors == []  # no row asked for before the grid had it


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


def test_copy_and_paste_put_a_copy_at_the_pointer(page):
    _node(page, "bus2").click()
    page.get_by_text("Bus bus2", exact=True).wait_for()  # the server knows the selection
    page.keyboard.press("ControlOrMeta+c")
    pane = page.locator(".vue-flow__pane").bounding_box()
    x, y = pane["x"] + pane["width"] * 0.5, pane["y"] + pane["height"] * 0.8
    page.mouse.move(x, y)
    page.keyboard.press("ControlOrMeta+v")
    _node(page, "bus2 Kopie").wait_for()
    cx, cy = _centre(_node(page, "bus2 Kopie"))
    assert abs(cx - x) <= 20 and abs(cy - y) <= 20  # on the raster next to the pointer
    page.locator("[data-bus='bus2 Kopie'].gi-sel").wait_for()
    assert page.errors == []


def test_the_context_menu_acts_on_what_was_right_clicked(page):
    _node(page, "bus2").click(button="right")
    menu = page.locator(".gi-menu")
    menu.wait_for()
    assert menu.locator(".gi-menu-item").all_inner_texts()[0].startswith("Bearbeiten")
    page.locator("[data-bus='bus2'].gi-sel").wait_for()  # the right click selected it
    menu.get_by_text("Fehler hier setzen").click()
    _node(page, "bus2").locator(".gi-flash").wait_for()
    assert page.locator(".gi-menu").count() == 0

    # A closed branch offers to open its switch, an open one to close it.
    page.locator("[data-branch='b12'] .gi-edge-hit").click(button="right", force=True)
    page.locator(".gi-menu").get_by_text("Schalter öffnen").click()
    page.locator("[data-branch='b12'].gi-off").wait_for()
    page.locator("[data-branch='b12'] .gi-edge-hit").click(button="right", force=True)
    assert page.locator(".gi-menu").get_by_text("Schalter schließen").count() == 1
    page.keyboard.press("Escape")
    page.locator(".gi-menu").wait_for(state="detached")

    # On the empty pane: a bus where the pointer is.
    pane = page.locator(".vue-flow__pane").bounding_box()
    x, y = pane["x"] + pane["width"] * 0.5, pane["y"] + pane["height"] * 0.8
    page.mouse.click(x, y, button="right")
    page.locator(".gi-menu").get_by_text("Bus hier setzen").click()
    _node(page, "B1").wait_for()
    cx, cy = _centre(_node(page, "B1"))
    assert abs(cx - x) <= 20 and abs(cy - y) <= 20
    assert page.errors == []


def _text_height(page, selector):
    return page.locator(selector).first.evaluate("e => e.getBoundingClientRect().height")


def test_names_stay_readable_when_the_plan_is_zoomed_out_and_go_when_too_small(page):
    assert _text_height(page, ".gi-label") >= 13  # at the zoom of the fit: the font's own size
    zoom_out = page.locator(".vue-flow__controls-zoomout")
    zoom_out.click()
    zoom_out.click()
    page.wait_for_timeout(500)
    # Zoomed out the symbols shrink, the names do not go below about 11 px on screen.
    assert _text_height(page, ".gi-label") >= 11
    assert _text_height(page, ".gi-edge-label") >= 9
    for _ in range(6):
        zoom_out.click()
    page.locator(".gi-canvas.gi-very-far").wait_for()
    assert not page.locator(".gi-label").first.is_visible()
    assert not page.locator(".gi-edge-label").first.is_visible()
    # What the pointer is on shows its name again.
    _node(page, "bus2").hover()
    assert _node(page, "bus2").locator(".gi-label").is_visible()
    assert page.errors == []


def test_parallel_branches_are_drawn_side_by_side(page):
    _tool(page, "Zweig")
    x0, y0 = _centre(_node(page, "bus1"))
    x1, y1 = _centre(_node(page, "bus2"))
    page.mouse.move(x1, y1)  # drawn the other way round than b12
    page.mouse.down()
    page.mouse.move(x0, y0, steps=15)
    page.mouse.up()
    page.locator("[data-branch='L1']").wait_for()
    boxes = [page.locator(f"[data-branch='{name}'] .gi-edge-line").bounding_box() for name in ("b12", "L1")]
    # Two lines of the same length, 12 px of the diagram apart at right angles to the branch.
    horizontal = boxes[0]["width"] > boxes[0]["height"]
    apart = abs(boxes[0]["y"] - boxes[1]["y"]) if horizontal else abs(boxes[0]["x"] - boxes[1]["x"])
    zoom = page.evaluate("document.querySelector('.vue-flow__transformationpane').getBoundingClientRect().width"
                         " / document.querySelector('.vue-flow__transformationpane').offsetWidth")
    assert apart == pytest.approx(12 * zoom, abs=1.5)
    # Each of them can be clicked.
    _tool(page, "Auswahl")
    page.locator("[data-branch='L1'] .gi-edge-line").click(force=True)
    page.get_by_text("Zweig L1", exact=True).wait_for()
    assert page.errors == []


def test_the_result_is_written_at_the_buses_and_the_tooltip_names_the_element(page):
    _solve_in_the_results_tab(page)
    tile = page.locator(".gi-tile").first.inner_text().split("\n")[1]  # EPR at the fault location, bus3
    page.get_by_role("tab", name="Netzplan").click()
    page.get_by_role("button", name="Einfärben").click()
    page.get_by_text("Ergebnis: EPR (RMS)").click()
    value = _node(page, "bus3").locator(".gi-value")
    value.wait_for()
    assert value.inner_text() == f"{tile} V"
    assert _node(page, "bus3").locator(".gi-flash-name").inner_text() == "fault"
    tip = page.locator("[data-bus='bus3']").get_attribute("title")
    assert tip.startswith("bus3 · ") and f"EPR {tile} V" in tip and tip.endswith("Fehler fault")
    _screenshot(page, "minimal_epr_values")
    assert page.errors == []


def test_elk_lays_the_network_out_in_the_browser(page):
    before = [_centre(_node(page, n)) for n in ("bus1", "bus2", "bus3")]
    page.get_by_role("button", name="Auto-Layout").click()
    page.get_by_text("Anordnen mit ELK (geschichtet)").click()
    page.wait_for_timeout(1500)
    after = [_centre(_node(page, n)) for n in ("bus1", "bus2", "bus3")]
    assert after != before
    assert page.errors == []


def _in_the_middle(page, name):
    """Whether a bus is in the middle of the canvas (within 60 px)."""
    canvas = page.locator(".gi-canvas").bounding_box()
    x, y = _centre(_node(page, name))
    return (abs(x - canvas["x"] - canvas["width"] / 2) < 60 and abs(y - canvas["y"] - canvas["height"] / 2) < 60)


def test_finding_an_element_from_the_table_and_by_its_name(page):
    page.get_by_role("button", name="Neu").click()
    page.get_by_text("MS-Ring", exact=True).click()
    page.locator(".vue-flow__node[data-id='b20']").wait_for()
    assert not _in_the_middle(page, "b17")
    page.get_by_role("tab", name="Netz", exact=True).click()
    grid = page.locator(".ag-root-wrapper").first
    grid.locator(".ag-row").first.wait_for()
    page.get_by_label("In den Tabellen suchen").fill("b17")
    # Of twenty buses the one that is asked for (and the two branches that end at it, in their table).
    page.wait_for_function("document.querySelector('.ag-root-wrapper').querySelectorAll('.ag-row').length === 1")
    grid.get_by_role("gridcell", name="b17").first.click()
    page.get_by_role("button", name="Im Plan zeigen").first.click()
    # The plan tab, bus b17 selected and in the middle.
    page.locator("[data-bus='b17'].gi-sel").wait_for()
    page.wait_for_function("document.querySelector('.gi-canvas').clientWidth > 0")
    page.wait_for_timeout(400)
    assert _in_the_middle(page, "b17")
    # The search of the plan: a name, Enter, and that element is in the middle.
    search = page.get_by_label("Element suchen")
    search.fill("b03")
    page.get_by_role("option", name="b03", exact=True).click()
    page.locator("[data-bus='b03'].gi-sel").wait_for()
    page.wait_for_timeout(400)
    assert _in_the_middle(page, "b03") and not _in_the_middle(page, "b17")
    _screenshot(page, "ring_found_by_name")
    assert page.errors == []


def test_solving_from_the_plan_shows_the_result_in_the_plan(page):
    page.get_by_role("button", name="Rechnen").click()  # the plan's own
    value = _node(page, "bus3").locator(".gi-value")
    value.wait_for(timeout=60000)
    # Still the plan, now coloured by the EPR; the tiles of the results tab show the same number.
    assert page.get_by_role("tab", name="Netzplan").get_attribute("aria-selected") == "true"
    page.get_by_text("Ergebnis: EPR (RMS)").first.wait_for()
    shown = value.inner_text()
    page.get_by_role("tab", name="Ergebnisse").click()
    page.locator(".gi-tile").first.wait_for()
    assert shown == page.locator(".gi-tile").first.inner_text().split("\n")[1] + " V"
    assert page.errors == []


def test_a_solved_ring_coloured_by_its_epr(page):
    page.get_by_role("button", name="Neu").click()
    page.get_by_text("MS-Ring", exact=True).click()
    page.locator(".vue-flow__node[data-id='b20']").wait_for()
    _solve_in_the_results_tab(page)
    page.get_by_role("tab", name="Netzplan").click()
    page.get_by_role("button", name="Einfärben").click()
    page.get_by_text("Ergebnis: EPR (RMS)").click()
    page.locator(".gi-value").first.wait_for()  # the colouring has reached the canvas
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
