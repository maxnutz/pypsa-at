# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Apply the configured biomass availability factors to the clustered potentials."""

import pandas as pd
from snakemake.script import Snakemake

from mods import scale_biomass_potentials


def main(snakemake: Snakemake) -> None:
    """
    Read, scale and write the clustered biomass potentials.

    Parameters
    ----------
    snakemake
        The Snakemake workflow object providing inputs, outputs and params.

    Returns
    -------
    :
        Exports the result.
    """
    potentials = pd.read_csv(snakemake.input.biomass_potentials_raw, index_col=0)
    result = scale_biomass_potentials(
        potentials, snakemake.params.biomass_potential_scaling
    )
    result.to_csv(snakemake.output.biomass_potentials)


if __name__ == "__main__":
    from scripts._helpers import configure_logging, mock_snakemake, set_scenario_config

    if "snakemake" not in globals():
        snakemake = mock_snakemake(
            "scale_biomass_potentials_at",
            clusters="adm",
            planning_horizons="2040",
            run="AT_KN2040",
        )
    configure_logging(snakemake)
    set_scenario_config(snakemake)
    main(snakemake)
