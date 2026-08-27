# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
AT-owned variant of ``build_industrial_production_per_country`` that scales
historic industrial production for selected countries.

Mirrors the functionality of ``build_industrial_production_per_country`` and
applies a scaling factor to the historic industrial production data for selected
countries, specified in config["industry"]["manipulate_output_historcal_scaling_factor"].
"""

import pandas as pd

import scripts.build_industrial_production_per_country as bipc
from mods import apply_historic_industrial_demand_scaling
from scripts._helpers import configure_logging, set_scenario_config

if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("modify_historic_industrial_demand")
    configure_logging(snakemake)
    set_scenario_config(snakemake)

    bipc.snakemake = snakemake
    bipc.params = snakemake.params.industry

    countries = snakemake.params.countries
    year = snakemake.params.industry["reference_year"]
    jrc_dir = snakemake.input.jrc
    eurostat = pd.read_csv(snakemake.input.eurostat)

    demand = bipc.industry_production(countries, year, eurostat, jrc_dir)

    demand = apply_historic_industrial_demand_scaling(demand, snakemake)

    bipc.separate_basic_chemicals(demand, year)

    demand.fillna(0.0, inplace=True)

    fn = snakemake.output.industrial_production_per_country
    demand.to_csv(fn, float_format="%.2f")
