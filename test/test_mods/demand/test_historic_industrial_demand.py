# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Unit tests for mods/demand/historic_industrial_demand.py."""

from types import SimpleNamespace

import pandas as pd

from mods.demand.historic_industrial_demand import (
    apply_historic_industrial_demand_scaling,
)


def make_demand() -> pd.DataFrame:
    return pd.DataFrame(
        {"Cement": [100.0, 200.0], "Electric arc": [10.0, 20.0]},
        index=["AT", "DE"],
    )


def make_snakemake(**industry_config) -> SimpleNamespace:
    return SimpleNamespace(config={"industry": industry_config})


def test_scales_configured_country():
    demand = make_demand()
    snakemake = make_snakemake(
        manipulate_output_historical=True,
        manipulate_output_historical_scale_factor={"AT": 0.95},
    )

    result = apply_historic_industrial_demand_scaling(demand, snakemake)

    assert result.loc["AT", "Cement"] == 95.0
    assert result.loc["AT", "Electric arc"] == 9.5
    assert result.loc["DE", "Cement"] == 200.0


def test_disabled_by_default():
    demand = make_demand()
    snakemake = make_snakemake(
        manipulate_output_historical_scale_factor={"AT": 0.95},
    )

    result = apply_historic_industrial_demand_scaling(demand, snakemake)

    pd.testing.assert_frame_equal(result, make_demand())


def test_unknown_country_is_skipped():
    demand = make_demand()
    snakemake = make_snakemake(
        manipulate_output_historical=True,
        manipulate_output_historical_scale_factor={"FR": 0.5},
    )

    result = apply_historic_industrial_demand_scaling(demand, snakemake)

    pd.testing.assert_frame_equal(result, make_demand())
