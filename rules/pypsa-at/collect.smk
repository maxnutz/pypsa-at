# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
PyPSA-AT main rule to run the workflow.
"""


rule export_evaluation_pypsa_at:
    input:
        networks=expand(
            RESULTS + "networks/solved_{horizon}.nc",
            horizon=config["planning_horizons"],
            allow_missing=True,
        ),
    output:
        touch(
            RESULTS + "evaluation/.run_by_snakemake",
        ),
    resources:
        mem_mb=8000,
    params:
        rdir=RESULTS,
    message:
        "Runs all evaluations from the evals module to generate aggregated result views."
    shell:
        "pixi run evals {params.rdir}"


rule validate_pypsa_at:
    input:
        networks=expand(
            RESULTS + "networks/solved_{horizon}.nc",
            horizon=config["planning_horizons"],
            allow_missing=True,
        ),
    output:
        validity_report=RESULTS + "test_report.html",
    resources:
        mem_mb=8000,
    params:
        clustering=config_provider("clustering"),
        rdir=RESULTS,
    message:
        "Execute pypsa-at modifications layer tests. They are marked as 'AT' and require the `--result-path` extra argument."
    shell:
        'pixi run -e test pytest -m "AT" --html {params.rdir}/test_report.html --result-path={params.rdir}'


# CI runs (config/test/config.at10.yaml) skip the evaluations to save time.
# Exact match on purpose: a renamed CI prefix runs the evaluations again
# instead of silently skipping them for other runs.
SKIP_EVALUATION = config["run"]["prefix"] == "test-sector-myopic-at10"


rule all_at:
    default_target: True
    input:
        expand(RESULTS + "test_report.html", run=config["run"]["name"]),
        (
            []
            if SKIP_EVALUATION
            else expand(
                RESULTS + "evaluation/.run_by_snakemake", run=config["run"]["name"]
            )
        ),


def industry_history_inputs(w):
    """
    Per-year industry files for config/config.industry-history.yaml.

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


rule industry_history_at:
    input:
        industry_history_inputs,
    message:
        "Collecting historical industrial production per country (one scenario per year)"
