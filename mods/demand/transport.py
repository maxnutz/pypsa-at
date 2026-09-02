# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Rescale Austrian road-transport Loads and BEV chargers to NetZero2040 shares."""

import pandas as pd
import pypsa
from snakemake.script import Snakemake

from scripts._helpers import get

LOAD_CARRIER_TO_ENGINE = {
    "land transport EV": "electric",
    "land transport fuel cell": "fuel_cell",
    "land transport oil": "ice",
}


def apply_transport_technology_shares(n: pypsa.Network, snakemake: Snakemake) -> None:
    """
    Rescale Austrian land-transport loads and BEV charger/V2G capacity to
    match NetZero2040 technology shares.

    Note
    ----
    Mirrors PyPSA-DE's ``modify_mobility_demand`` (energy Loads rescaled by a
    share ratio; BEV chargers/V2G sized from an absolute EV count), retargeted
    from ``"DE"`` to ``"AT"`` nodes and from the Ariadne database to the
    NetZero2040 Zenodo scenario.

    Parameters
    ----------
    n
        The pre-network to be modified in place.
    snakemake
        The Snakemake workflow object providing inputs, params, and
        wildcards.

    Returns
    -------
    :
        Updates the network in place.
    """
    if not snakemake.params.netzero_technology_shares_enable:
        return

    stock = pd.read_csv(snakemake.input.transport_technology_shares, index_col=0)
    investment_year = int(snakemake.wildcards.planning_horizons)
    year_col = str(investment_year)

    _rescale_loads(n, stock, year_col, investment_year, snakemake.params.sector)
    _rescale_bev_chargers(n, stock, year_col, snakemake.params.bev_charge_rate)


def _rescale_loads(n, stock, year_col, investment_year, sector_params):
    """
    Rescale AT land-transport Loads by ``new_share / old_share`` per engine.

    ``old_share`` is the generic config share the network was already built
    with (via ``add_land_transport``) for this investment year;
    ``new_share`` is the NetZero2040-prescribed share. Because the NetZero2040
    shares are expressed against the same 2023 baseline as the absolute car
    counts, this ratio simultaneously captures technology-mix shift and
    overall demand growth without needing to separately re-derive total
    driven-km demand.

    Parameters
    ----------
    n
        The pre-network to be modified in place.
    stock
        The DataFrame containing the NetZero2040 technology shares.
    year_col
        The column in the stock DataFrame corresponding to the investment year.
    investment_year
        The year for which to rescale the loads.
    sector_params
        The sector parameters containing the old share information.

    Returns
    -------
    :
        Updates the network in place.

    Raises
    ------
    ValueError
        If ``old_share`` is 0 for an engine that the NetZero2040 scenario
        wants nonzero -- there are then no matching Loads in the network to
        rescale.
    """
    new_shares = {
        engine: stock.loc[f"{engine}_share", year_col]
        for engine in LOAD_CARRIER_TO_ENGINE.values()
    }
    old_shares = {
        engine: get(sector_params[f"land_transport_{engine}_share"], investment_year)
        for engine in LOAD_CARRIER_TO_ENGINE.values()
    }

    scale_factors = {}
    for engine, old_share in old_shares.items():
        if old_share == 0:
            if new_shares[engine] != 0:
                raise ValueError(
                    f"land_transport_{engine}_share is 0 for {investment_year}, "
                    f"but the NetZero2040 scenario prescribes a nonzero share "
                    f"({new_shares[engine]}). No '{engine}' loads exist in the "
                    "network to rescale. Add a nonzero floor to "
                    f"sector.land_transport_{engine}_share in config.at.yaml."
                )
            scale_factors[engine] = 1.0  # placeholder for scaling nothing.
        else:
            scale_factors[engine] = new_shares[engine] / old_share

    for carrier, engine in LOAD_CARRIER_TO_ENGINE.items():
        loads_i = n.loads.index[
            n.loads.carrier.eq(carrier) & n.loads.index.str.startswith("AT")
        ]
        n.loads_t.p_set.loc[:, loads_i] *= scale_factors[engine]


def _rescale_bev_chargers(n, stock, year_col, bev_charge_rate):
    """
    Size AT BEV chargers/V2G from the absolute electric-car stock target.

    Mirrors PyPSA-DE's ``modify_mobility_demand``: ``p_nom_target =
    number_of_EVs * bev_charge_rate``, distributed proportionally across the
    existing chargers' current capacity.

    Parameters
    ----------
    n
        The pre-network to be modified in place.
    stock
        The DataFrame containing the NetZero2040 technology shares.
    year_col
        The column in the stock DataFrame corresponding to the investment year.
    bev_charge_rate
        The charging rate for BEV vehicles.

    Returns
    -------
    :
        Updates the network in place.

    Raises
    ------
    ValueError
        If no AT BEV charger capacity exists to scale from, but the
        NetZero2040 scenario prescribes a nonzero electric-car stock.
    """
    # The NetZero2040 source file reports Stock|Cars|Passenger|* in units of
    # 1000 vehicles, so the raw stock value must be scaled up to an actual
    # vehicle count here
    number_of_evs = stock.loc["electric", year_col] * 1000
    p_nom_target = number_of_evs * bev_charge_rate

    chargers_i = n.links.index[
        n.links.carrier.eq("BEV charger") & n.links.bus0.str.startswith("AT")
    ]
    current_p_nom_total = n.links.loc[chargers_i, "p_nom"].sum()
    if current_p_nom_total == 0:
        if p_nom_target != 0:
            raise ValueError(
                "No AT 'BEV charger' capacity exists in the network to size "
                f"from, but the NetZero2040 scenario prescribes {number_of_evs} "
                "electric cars. Ensure sector.land_transport_electric_share "
                "is nonzero for this investment year."
            )
        return

    charger_scale_factor = p_nom_target / current_p_nom_total
    for carrier in ("BEV charger", "V2G"):
        links_i = n.links.index[
            n.links.carrier.eq(carrier) & n.links.bus0.str.startswith("AT")
        ]
        n.links.loc[links_i, "p_nom"] *= charger_scale_factor
