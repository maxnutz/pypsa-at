# SPDX-FileCopyrightText: 2023-2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Relocate existing onshore wind capacities to their actual plant locations.

Upstream ``add_existing_baseyear`` takes national onshore wind totals per
build year from IRENA statistics and spreads them over all regions of a country
in proportion to the regional technical potential (``p_nom_max``). For Austria
this places most existing turbines in alpine regions with near-zero capacity
factors, while the real fleet is concentrated in Burgenland and Lower Austria.

:func:`relocate_existing_onwind` keeps the IRENA national totals and build-year
cohorts, but redistributes each cohort across regions according to the regional
capacity shares of the onshore wind plants in powerplantmatching. It only acts
in the base year; later myopic horizons inherit the relocated capacities via
``add_brownfield``.
"""

from logging import getLogger

import pandas as pd
import pypsa
from snakemake.script import Snakemake

logger = getLogger(__name__)

CARRIER = "onwind"


def regional_shares_from_powerplants(
    powerplants: pd.DataFrame, country: str
) -> pd.Series:
    """
    Compute regional onshore wind capacity shares for one country.

    Parameters
    ----------
    powerplants
        Clustered powerplantmatching table with ``Fueltype``, ``Technology``,
        ``Country``, ``Capacity`` and ``bus`` columns.
    country
        Two-letter country code.

    Returns
    -------
    :
        Capacity share per bus, summing to 1.

    Raises
    ------
    ValueError
        If powerplantmatching contains no onshore wind plants for *country*.
    """
    onwind = powerplants.query(
        "Fueltype == 'Wind' and Technology == 'Onshore' and Country == @country"
    )
    capacity = onwind.groupby("bus")["Capacity"].sum()
    capacity = capacity[capacity > 0]
    if capacity.empty:
        raise ValueError(f"No onshore wind plants found in powerplants for {country}.")
    return capacity / capacity.sum()


def redistribute_cohort(cohort: pd.Series, buses: pd.Series, shares: pd.Series):
    """
    Redistribute the capacity of one build-year cohort according to bus shares.

    Within a bus, capacity is split among its generators (resource classes) in
    proportion to their current capacity, or evenly if all are zero.

    Parameters
    ----------
    cohort
        Current capacity per generator of one country and build year.
    buses
        Bus per generator, same index as *cohort*.
    shares
        Target capacity share per bus, summing to 1.

    Returns
    -------
    :
        New capacity per generator; the cohort total is preserved.

    Raises
    ------
    ValueError
        If a bus with a positive share hosts no generator of the cohort.
    """
    missing = shares.index.difference(buses.unique())
    if not missing.empty:
        raise ValueError(
            f"Buses {missing.tolist()} hold existing {CARRIER} plants in "
            f"powerplantmatching, but have no {CARRIER} generator for this cohort."
        )

    target = buses.map(shares).fillna(0.0) * cohort.sum()
    bus_total = cohort.groupby(buses).transform("sum")
    bus_count = cohort.groupby(buses).transform("count")
    within_bus = (cohort / bus_total).where(bus_total > 0, 1 / bus_count)
    return target * within_bus


def add_missing_cohort_generators(
    n: pypsa.Network, cohort: pd.Index, buses: pd.Index, base_year: int
) -> pd.Index:
    """
    Add empty generators for a build-year cohort on buses that lack one.

    Upstream drops cohort slices below ``existing_capacities.threshold_capacity``
    per region, so small cohorts exist only on some buses. New generators copy
    the static attributes of an existing cohort member and take ``p_max_pu``
    from the base-year generator of the target bus, one per resource class.

    Parameters
    ----------
    n
        Network to extend in place.
    cohort
        Names of the existing generators of one country and build year.
    buses
        Buses that must host a generator of the cohort.
    base_year
        First planning horizon; base-year generators provide the profiles.

    Returns
    -------
    :
        Names of the added generators.
    """
    gens = n.generators
    template = gens.loc[cohort[0]]
    missing = buses.difference(gens.loc[cohort, "bus"].unique())
    sources = gens.index[
        (gens.carrier == CARRIER)
        & gens.p_nom_extendable
        & (gens.build_year == base_year)
        & gens.bus.isin(missing)
    ]
    suffix = f"-{base_year}"
    names = pd.Index(
        [s.removesuffix(suffix) + f"-{template.build_year}" for s in sources]
    )
    if names.empty:
        return names

    attrs = template.drop(
        ["bus", "p_nom", "p_nom_min", "p_nom_max", "p_max_pu"]
    ).to_dict()
    n.add(
        "Generator",
        names,
        bus=gens.loc[sources, "bus"].values,
        p_nom=0.0,
        p_max_pu=n.generators_t.p_max_pu[sources].set_axis(names, axis=1),
        **attrs,
    )
    return names


def relocate_existing_onwind(n: pypsa.Network, snakemake: Snakemake) -> None:
    """
    Move base-year onshore wind brownfield to powerplantmatching plant locations.

    For every configured country and build year, the summed existing capacity
    is redistributed across regions by :func:`regional_shares_from_powerplants`.
    Existing cohorts are the non-extendable generators (``p_nom``) plus the
    base-year cohort, which upstream stores as ``p_nom`` / ``p_nom_min`` of the
    extendable generator. Missing cohort generators on target buses are added
    by :func:`add_missing_cohort_generators`. ``p_nom_max`` is raised where it
    falls below the relocated ``p_nom_min``.

    Parameters
    ----------
    n
        The pre-network to be modified in place.
    snakemake
        Snakemake workflow object. Uses ``params.relocate_existing_onwind``,
        ``params.planning_horizons``, ``input.powerplants_at`` and
        ``wildcards.planning_horizons``.

    Returns
    -------
    :
        Updates ``n.generators`` in place.
    """
    cfg = snakemake.params.relocate_existing_onwind
    if not cfg["enable"]:
        logger.info("Relocation of existing onwind capacities disabled.")
        return

    base_year = min(int(y) for y in snakemake.params.planning_horizons)
    if int(snakemake.wildcards.planning_horizons) != base_year:
        return

    powerplants = pd.read_csv(snakemake.input.powerplants_at, index_col=0)
    existing = (n.generators.carrier == CARRIER) & (
        ~n.generators.p_nom_extendable | (n.generators.build_year == base_year)
    )
    country = n.generators.bus.map(n.buses.country)

    for ct in cfg["countries"]:
        shares = regional_shares_from_powerplants(powerplants, ct)
        cohorts = n.generators[existing & (country == ct)].groupby("build_year")
        for build_year, names in cohorts.groups.items():
            added = add_missing_cohort_generators(n, names, shares.index, base_year)
            cohort = n.generators.loc[names.append(added)]
            new = redistribute_cohort(cohort.p_nom, cohort.bus, shares)
            n.generators.loc[new.index, "p_nom"] = new
            ext = new.index[cohort.p_nom_extendable]
            n.generators.loc[ext, "p_nom_min"] = new[ext]
            logger.info(
                f"Relocated {new.sum():.1f} MW existing {CARRIER} in {ct} "
                f"(build year {build_year}) to powerplantmatching locations."
            )

    gens = n.generators
    too_small = gens.index[
        (gens.carrier == CARRIER) & (gens.p_nom_min > gens.p_nom_max)
    ]
    gens.loc[too_small, "p_nom_max"] = gens.loc[too_small, "p_nom_min"]
