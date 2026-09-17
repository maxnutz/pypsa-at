# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Scale regional biomass potentials to probe biomass availability sensitivities.

Applied to ``biomass_potentials_s_{clusters}_{planning_horizons}.csv`` right after
``build_biomass_potentials`` and before ``prepare_sector_network`` turns the table
into biomass ``Generator`` objects. Because that one file is the single source of
truth for biomass availability, scaling it keeps generator ``p_nom``, ``e_sum_min``
and ``e_sum_max`` consistent across all planning horizons for free.

The public entry point is :func:`scale_biomass_potentials`.

Notes
-----
Four of the six potential columns back *must-run* generators in
``prepare_sector_network.add_biomass`` (``e_sum_min == e_sum_max == potential``):
municipal solid waste and the three unsustainable carriers. Scaling those changes
forced consumption, not just availability — see :data:`MUST_RUN_CARRIERS`.
"""

from logging import getLogger

import pandas as pd

logger = getLogger(__name__)

#: Potential columns of ``biomass_potentials_s_*.csv`` that may be scaled.
#: The remaining column, ``not included``, is never read by
#: ``prepare_sector_network.add_biomass`` and is therefore left alone.
POTENTIAL_CARRIERS = (
    "solid biomass",
    "biogas",
    "municipal solid waste",
    "unsustainable solid biomass",
    "unsustainable biogas",
    "unsustainable bioliquids",
)

#: Carriers whose generators are added with ``e_sum_min == e_sum_max``, so that
#: scaling their potential scales forced consumption rather than an upper bound.
MUST_RUN_CARRIERS = frozenset(
    {
        "municipal solid waste",
        "unsustainable solid biomass",
        "unsustainable biogas",
        "unsustainable bioliquids",
    }
)

#: ``factors`` key applied to every country without an explicit entry.
DEFAULT_FACTOR_KEY = "default"


def resolve_factors(countries: pd.Index, factors: dict) -> dict[str, float]:
    """
    Resolve the scaling factor of every country in the potentials table.

    Countries without an explicit entry fall back to the ``default`` key, which
    itself defaults to ``1.0``. Configured country codes that do not occur in the
    table are reported as a warning, because they are almost always typos or
    countries missing from ``countries``.

    Parameters
    ----------
    countries
        Two-letter country codes occurring in the potentials table.
    factors
        Mapping of country code to multiplier, optionally carrying a ``default``
        key.

    Returns
    -------
    :
        Multiplier for each country in ``countries``.
    """
    default = float(factors.get(DEFAULT_FACTOR_KEY, 1.0))

    unknown = sorted(set(factors) - {DEFAULT_FACTOR_KEY} - set(countries))
    if unknown:
        logger.warning(
            f"Ignoring biomass scaling factors for countries absent from the "
            f"potentials table: {', '.join(unknown)}."
        )

    return {c: float(factors.get(c, default)) for c in countries}


def scale_biomass_potentials(potentials: pd.DataFrame, params: dict) -> pd.DataFrame:
    """
    Scale the configured biomass potential columns per country.

    Gated behind ``mods.biomass_potential_scaling.enable``. The table is indexed by
    model region (``AT111``, ``DE1``, ``AL``, ...); the country of a region is its
    first two characters, so the same factors apply to every clustering mode.

    Parameters
    ----------
    potentials
        Biomass potentials in MWh/a, indexed by model region, as written by
        ``build_biomass_potentials``.
    params
        The ``mods.biomass_potential_scaling`` config section with the keys
        ``enable``, ``carriers`` and ``factors``.

    Returns
    -------
    :
        A scaled copy of ``potentials``. The input is never modified in place.
    """
    if not params.get("enable", False):
        logger.info("Biomass potential scaling disabled, passing potentials through.")
        return potentials

    carriers = list(params.get("carriers") or [])
    columns = [c for c in carriers if c in potentials.columns]

    missing = [c for c in carriers if c not in potentials.columns]
    if missing:
        logger.warning(
            f"Configured biomass carriers not found in the potentials table and "
            f"therefore not scaled: {', '.join(missing)}."
        )
    if not columns:
        logger.warning("No biomass carriers to scale, passing potentials through.")
        return potentials

    countries = pd.Index(potentials.index.str[:2].unique())
    factors = resolve_factors(countries, params.get("factors") or {})

    scaled = {c: f for c, f in factors.items() if f != 1.0}
    if not scaled:
        logger.info("All biomass scaling factors are 1.0, potentials unchanged.")
        return potentials

    must_run = sorted(set(columns) & MUST_RUN_CARRIERS)
    if must_run and max(scaled.values()) > 1.0:
        logger.warning(
            f"Scaling up the must-run biomass carriers {', '.join(must_run)}: these "
            "back generators with e_sum_min == e_sum_max, so their potential is "
            "consumed in full rather than offered to the optimiser."
        )

    result = potentials.copy()
    region_country = result.index.str[:2]
    for country, factor in sorted(scaled.items()):
        result.loc[region_country == country, columns] *= factor

    uniform = set(scaled.values())
    if len(uniform) == 1 and len(scaled) == len(factors):
        applied = f"a factor of {uniform.pop()} in all countries"
    else:
        applied = ", ".join(f"{c}={f}" for c, f in sorted(scaled.items()))

    before = potentials[columns].sum().sum() / 1e6
    after = result[columns].sum().sum() / 1e6
    logger.info(
        f"Scaled biomass carriers {', '.join(columns)} by {applied}: "
        f"{before:.2f} -> {after:.2f} TWh/a in total."
    )

    return result
