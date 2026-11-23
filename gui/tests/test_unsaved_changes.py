# gui/tests/test_unsaved_changes.py
"""
Unsaved changes are not thrown away without a question.

Starting another project -- from a template, a file or an import -- replaces
the session. With unsaved changes the page asks first: cancel, discard, or
save and go on. A project without changes is replaced at once.
"""

import pytest
from nicegui.testing import User

import groundinsight as gi
from groundinsight.api import EXAMPLES

from groundinsight_gui.app import AppOptions, build_app

pytestmark = pytest.mark.anyio


async def _page(user: User, **options):
    pages = build_app(AppOptions(**({"start_example": "minimal"} | options)))
    await user.open("/")
    return pages[0]


def _change(user: User) -> None:
    user.find(marker="canvas").trigger("place", {"x": 400, "y": 200, "symbol": "bus"})


async def test_a_template_does_not_replace_unsaved_changes_without_a_question(user: User):
    page = await _page(user)
    session = page.session
    _change(user)
    user.find(marker="new-mv_ring").click()
    await user.should_see(marker="discard-dialog")
    assert page.session is session and "B1" in session.network.buses
    user.find(marker="discard-cancel").click()
    await user.should_not_see(marker="discard-dialog")
    assert page.session is session
    user.find(marker="new-mv_ring").click()
    await user.should_see(marker="discard-dialog")
    user.find(marker="discard-confirm").click()
    assert page.session is not session and len(page.session.network.buses) == 20
    assert not page.session.summary().dirty


async def test_a_project_without_changes_is_replaced_at_once(user: User):
    page = await _page(user)
    user.find(marker="new-mv_ring").click()
    assert len(page.session.network.buses) == 20
    await user.should_not_see(marker="discard-dialog")


async def test_open_and_import_ask_before_their_dialog(user: User):
    page = await _page(user)
    _change(user)
    user.find(marker="open").click()
    await user.should_see(marker="discard-dialog")
    await user.should_not_see(marker="file-dialog-open")
    user.find(marker="discard-confirm").click()
    await user.should_see(marker="file-dialog-open")  # now the file manager
    assert "B1" in page.session.network.buses  # nothing is lost before a file is chosen
    # An export replaces nothing and asks nothing.
    user.find(marker="io-export_tables").click()
    await user.should_see(marker="io-dialog")


async def test_save_and_go_on_writes_the_file_first(user: User, tmp_path):
    target = tmp_path / "strang.json"
    gi.save_network_to_json(EXAMPLES["minimal"](), target)
    page = await _page(user, start_example=None, start_file=str(target))
    _change(user)
    user.find(marker="new-mv_ring").click()
    await user.should_see(marker="discard-dialog")
    user.find(marker="discard-save").click()
    await user.should_see("Gespeichert", retries=50)
    assert len(page.session.network.buses) == 20  # the template, after the save
    assert "B1" in gi.load_project(target).network.buses  # and the change is in the file


async def test_the_browser_is_told_to_warn_while_the_dirty_marker_shows(user: User):
    from groundinsight_gui import app as app_module

    page = await _page(user)
    # The page's script asks the browser to confirm leaving while the marker of unsaved changes is visible.
    assert "beforeunload" in app_module._UNSAVED_JS and ".gi-dirty" in app_module._UNSAVED_JS
    assert "gi-dirty" in page.dirty_marker.classes and not page.dirty_marker.visible
    _change(user)
    assert page.dirty_marker.visible
