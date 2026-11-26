# SPDX-FileCopyrightText: 2026 Infra-Ensure contributors
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Infra-Ensure collection rules.
"""


def industry_history_inputs(w):
    """
    Per-year industry files for config/infra-ensure/config.industry-history.yaml.

    Each scenario sets industry.reference_year; that year is also used as
    {horizon}. Scenarios without it (e.g. the default stack) are skipped.
    """
    files = []
    for name in config["run"]["name"]:
        year = scenarios.get(name, {}).get("industry", {}).get("reference_year")
        if year is None:
            continue
        files += [
            resources(f).format(run=name)
            for f in [
                "ammonia_production.csv",
                "eurostat_energy_balances.csv",
                "industrial_production_per_country.csv",
                f"industrial_production_per_country_tomorrow_{year}.csv",
                f"industrial_production_per_country_tomorrow_{year}-modified.csv",
            ]
        ]
    return files


rule industry_history:
    input:
        industry_history_inputs,
    message:
        "Collecting historical industrial production per country (one scenario per year)"
