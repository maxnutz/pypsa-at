# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Unit tests for the NetZero2040 transport technology share rescale.

Tests operate on a small synthetic network rather than a solved ``nc``
network: the Load rescale multiplies existing p_set by a ratio, and upstream
``add_land_transport`` applies per-engine efficiency and temperature
corrections when it first builds those Loads, so the pre-rescale energy
split is not recoverable from a solved network's final state alone. Testing
``_rescale_loads``/``_rescale_bev_chargers`` directly keeps the assertions
exact.
"""

import pandas as pd
import pypsa
import pytest

from mods.demand.transport import _rescale_bev_chargers, _rescale_loads


def _make_network_with_transport_loads() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=3, freq="h"))
    n.add("Bus", ["AT0 0", "DE0 0"])
    n.add(
        "Load",
        [
            "AT0 0 land transport EV",
            "AT0 0 land transport oil",
            "AT0 0 land transport fuel cell",
            "DE0 0 land transport EV",
        ],
        bus=["AT0 0", "AT0 0", "AT0 0", "DE0 0"],
        carrier=[
            "land transport EV",
            "land transport oil",
            "land transport fuel cell",
            "land transport EV",
        ],
    )
    n.loads_t.p_set = pd.DataFrame(
        {
            "AT0 0 land transport EV": [10.0, 10.0, 10.0],
            "AT0 0 land transport oil": [20.0, 20.0, 20.0],
            "AT0 0 land transport fuel cell": [5.0, 5.0, 5.0],
            "DE0 0 land transport EV": [30.0, 30.0, 30.0],
        },
        index=n.snapshots,
    )
    return n


def test_rescale_loads_applies_share_ratio_only_to_at_nodes():
    n = _make_network_with_transport_loads()
    stock = pd.DataFrame(
        {"2030": [0.6, 0.001, 0.399]},
        index=["electric_share", "fuel_cell_share", "ice_share"],
    )
    sector_params = {
        "land_transport_electric_share": {2030: 0.2},
        "land_transport_fuel_cell_share": {2030: 0.001},
        "land_transport_ice_share": {2030: 0.8},
    }

    _rescale_loads(n, stock, "2030", 2030, sector_params)

    # electric: new/old = 0.6 / 0.2 = 3.0
    assert n.loads_t.p_set["AT0 0 land transport EV"].tolist() == pytest.approx(
        [30.0, 30.0, 30.0]
    )
    # fuel_cell: new/old = 0.001 / 0.001 = 1.0 (unchanged)
    assert n.loads_t.p_set["AT0 0 land transport fuel cell"].tolist() == pytest.approx(
        [5.0, 5.0, 5.0]
    )
    # ice: new/old = 0.399 / 0.8 = 0.49875
    assert n.loads_t.p_set["AT0 0 land transport oil"].tolist() == pytest.approx(
        [9.975, 9.975, 9.975]
    )
    # DE Loads must stay untouched.
    assert n.loads_t.p_set["DE0 0 land transport EV"].tolist() == pytest.approx(
        [30.0, 30.0, 30.0]
    )


def test_rescale_loads_raises_when_old_share_zero_but_new_share_nonzero():
    n = _make_network_with_transport_loads()
    stock = pd.DataFrame(
        {"2050": [0.999, 0.001, 0.1]},
        index=["electric_share", "fuel_cell_share", "ice_share"],
    )
    sector_params = {
        "land_transport_electric_share": {2050: 0.999},
        "land_transport_fuel_cell_share": {2050: 0.001},
        # No floor applied for this scenario: old share is exactly 0, but
        # the NetZero2040 scenario wants a nonzero ice share.
        "land_transport_ice_share": {2050: 0},
    }

    with pytest.raises(ValueError, match="land_transport_ice_share is 0"):
        _rescale_loads(n, stock, "2050", 2050, sector_params)


def _make_network_with_bev_chargers() -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", ["AT0 0", "AT0 0 EV battery", "DE0 0", "DE0 0 EV battery"])
    n.add(
        "Link",
        ["AT0 0 BEV charger", "AT0 0 V2G", "DE0 0 BEV charger"],
        bus0=["AT0 0", "AT0 0 EV battery", "DE0 0"],
        bus1=["AT0 0 EV battery", "AT0 0", "DE0 0 EV battery"],
        carrier=["BEV charger", "V2G", "BEV charger"],
        p_nom=[100.0, 50.0, 200.0],
    )
    return n


def test_rescale_bev_chargers_sets_to_absolute_target():
    n = _make_network_with_bev_chargers()
    stock = pd.DataFrame({"2030": [500.0]}, index=["electric"])
    bev_charge_rate = 0.011  # MW per EV

    _rescale_bev_chargers(n, stock, "2030", bev_charge_rate)

    # stock is in units of 1000 vehicles, so the target uses 500 * 1000 EVs.
    expected_total = 500.0 * 1000 * bev_charge_rate
    assert n.links.loc["AT0 0 BEV charger", "p_nom"] == pytest.approx(expected_total)

    charger_scale_factor = expected_total / 100.0
    assert n.links.loc["AT0 0 V2G", "p_nom"] == pytest.approx(
        50.0 * charger_scale_factor
    )
    # DE capacity must stay untouched.
    assert n.links.loc["DE0 0 BEV charger", "p_nom"] == pytest.approx(200.0)


def test_rescale_bev_chargers_raises_when_no_at_capacity_but_nonzero_target():
    n = pypsa.Network()
    n.add("Bus", "AT0 0")
    stock = pd.DataFrame({"2030": [500.0]}, index=["electric"])

    with pytest.raises(ValueError, match="No AT 'BEV charger' capacity"):
        _rescale_bev_chargers(n, stock, "2030", 0.011)


def test_rescale_bev_chargers_noop_when_no_at_capacity_and_no_target():
    n = pypsa.Network()
    n.add("Bus", "AT0 0")
    stock = pd.DataFrame({"2030": [0.0]}, index=["electric"])

    _rescale_bev_chargers(n, stock, "2030", 0.011)  # must not raise
