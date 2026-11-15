# gui/tests/test_characterize.py
"""
The earthing characteristic computed with groundfield from the bus dialog.

Without groundfield the button is disabled and says why. With it (tests
marked ``groundfield``, skipped otherwise), the dialog's job computes the
characteristic and the page attaches it with one undoable command; the
attached table is compared with ``characterize`` called directly.
"""

import asyncio
import importlib.util

import pytest
from nicegui.testing import User

from groundinsight_gui.app import AppOptions, build_app

pytestmark = pytest.mark.anyio

HAS_GROUNDFIELD = importlib.util.find_spec("groundfield") is not None


async def _bus_dialog(user: User):
    pages = build_app(AppOptions(start_example="minimal"))
    await user.open("/")
    user.find(marker="canvas").trigger("open", {"kind": "bus", "name": "bus2"})
    await user.should_see(marker="char-compute")
    return pages[0]


@pytest.mark.skipif(HAS_GROUNDFIELD, reason="groundfield is installed here")
async def test_the_compute_button_needs_groundfield(user: User):
    await _bus_dialog(user)
    button = next(iter(user.find(marker="char-compute").elements))
    assert not button.enabled


@pytest.mark.groundfield
@pytest.mark.skipif(not HAS_GROUNDFIELD, reason="needs groundfield")
async def test_a_computed_characteristic_is_attached_to_the_bus(user: User):
    import groundfield as gf

    from groundinsight.integrations import groundfield as gfi

    page = await _bus_dialog(user)
    user.find(marker="char-compute").click()
    await user.should_see(marker="study-dialog")
    for key, value in {"factors": "1; 2", "frequencies": "50", "rho_1": "100", "rho_2": "500", "h_1": "2"}.items():
        next(iter(user.find(marker=f"study-{key}").elements)).set_value(value)
    user.find(marker="study-run").click()
    bus = None
    for _ in range(600):
        bus = page.session.network.buses["bus2"]
        if bus.characteristic is not None:
            break
        await asyncio.sleep(0.05)
    assert bus is not None and bus.characteristic is not None
    direct = gfi.characterize(
        gfi.electrode_world("ring"), gfi.scaled_soils(gf.TwoLayerSoil(rho_1=100.0, rho_2=500.0, h_1=2.0), [1.0, 2.0]),
        electrode="E", engine=gf.create_engine(frequencies=[50.0], segment_length=0.5), rho_preset="station")
    assert bus.characteristic.impedance_Ohm == direct.impedance_Ohm
    assert bus.characteristic.k_touch == direct.k_touch
    assert page.session.history()[-1] == "set characteristic of bus 'bus2'"
    page.session.undo()
    assert page.session.network.buses["bus2"].characteristic is None
