# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Scale historic JRC-IDEES/Eurostat industrial production for selected countries.

Applied to the per-country production table (kton/a per subsector) right
after it is assembled and before ammonia/HVC/chlorine/methanol are split out
of "Basic chemicals", so a country's scale factor applies uniformly across
all its historic subsector output.

The public entry point is :func:`apply_historic_industrial_demand_scaling`.
"""

from logging import getLogger

import pandas as pd
from snakemake.script import Snakemake

logger = getLogger(__name__)


def apply_historic_industrial_demand_scaling(
    demand: pd.DataFrame, snakemake: Snakemake
) -> pd.DataFrame:
    """
    Scale historic industrial production per country.

    Gated behind ``industry.manipulate_output_historical``. A country is
    scaled if it has an entry in
    ``industry.manipulate_output_historical_scale_factor``.

    Parameters
    ----------
    demand
        Per-country industrial production (kton/a), indexed by country code.
    snakemake
        The Snakemake workflow object providing config.

    Returns
    -------
    :
        The scaled ``demand`` DataFrame (also modified in place).
    """
    cfg = snakemake.config.get("industry", {})
    if not cfg.get("manipulate_output_historical", False):
        return demand

    scale_factors = cfg.get("manipulate_output_historical_scale_factor", {})
    for country, factor in scale_factors.items():
        if country not in demand.index:
            logger.warning(
                f"Country {country} in manipulate_output_historical_scale_factor "
                "not found in industrial production data; skipping."
            )
            continue
        demand.loc[country] *= factor
        logger.info(
            f"Scaled historic industrial production for {country} by factor {factor}."
        )

    return demand
