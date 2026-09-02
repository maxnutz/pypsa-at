# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Unit tests for the vendored NetZero2040 transport technology stock builder."""

import importlib
import math

import pandas as pd
import pytest

build_transport_technology_shares_at = importlib.import_module(
    "scripts.pypsa-at.build_transport_technology_shares_at"
)
get_transport_sector_technology_stock = (
    build_transport_technology_shares_at.get_transport_sector_technology_stock
)
KNOWN_YEARS = build_transport_technology_shares_at.KNOWN_YEARS
TARGET_YEARS = build_transport_technology_shares_at.TARGET_YEARS
TRANSPORT_CAR_VARIABLES = build_transport_technology_shares_at.TRANSPORT_CAR_VARIABLES

SCENARIO_VALUES = {
    "Stock|Cars|Passenger|Combustion": {2021: 1000, 2023: 900, 2030: 700, 2040: 400},
    "Stock|Cars|Passenger|Electric": {2021: 100, 2023: 150, 2030: 300, 2040: 600},
    "Stock|Cars|Passenger|Fuel Cell": {2021: 0, 2023: 5, 2030: 20, 2040: 50},
}
COMMON_BASIS = SCENARIO_VALUES["Stock|Cars|Passenger|Combustion"][2023] + (
    SCENARIO_VALUES["Stock|Cars|Passenger|Electric"][2023]
    + SCENARIO_VALUES["Stock|Cars|Passenger|Fuel Cell"][2023]
)


@pytest.fixture
def netzero2040_file(tmp_path):
    """A synthetic IAMC-format xlsx with both 'High Demand' scenario names."""
    rows = []
    for scenario in (
        "NetZero2040 high-import/high-demand base",
        "NetZero2040 low-import/high-demand base",
    ):
        for variable, years in SCENARIO_VALUES.items():
            rows.append(
                {
                    "model": "NetZero2040",
                    "scenario": scenario,
                    "region": "Austria",
                    "variable": variable,
                    "unit": "1000 Stock",
                    **years,
                }
            )
    path = tmp_path / "netzero2040-times-pyam.xlsx"
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


def test_unknown_scenario_raises(netzero2040_file):
    with pytest.raises(ValueError, match="Unknown scenario"):
        get_transport_sector_technology_stock(netzero2040_file, "Medium Demand")


def test_output_shape(netzero2040_file):
    result = get_transport_sector_technology_stock(netzero2040_file, "High Demand")

    expected_rows = TRANSPORT_CAR_VARIABLES + [
        f"{variable} Share" for variable in TRANSPORT_CAR_VARIABLES
    ]
    assert list(result.index) == expected_rows
    assert list(result.columns) == TARGET_YEARS


def test_known_years_pass_through_unchanged(netzero2040_file):
    """Years directly reported in the source file are not extrapolated."""
    result = get_transport_sector_technology_stock(netzero2040_file, "High Demand")

    for variable, years in SCENARIO_VALUES.items():
        for year in (2030, 2040):
            assert year in KNOWN_YEARS
            assert result.loc[variable, year] == pytest.approx(years[year])


def test_extrapolated_years_are_share_absolute_consistent(netzero2040_file):
    """For years beyond the reported horizon, absolute == share * common_basis."""
    result = get_transport_sector_technology_stock(netzero2040_file, "High Demand")

    extrapolated_years = [year for year in TARGET_YEARS if year not in KNOWN_YEARS]
    assert extrapolated_years == [2025, 2035, 2045, 2050]

    for variable in TRANSPORT_CAR_VARIABLES:
        for year in extrapolated_years:
            absolute = result.loc[variable, year]
            share = result.loc[f"{variable} Share", year]
            assert absolute == pytest.approx(share * COMMON_BASIS)


def test_shares_need_not_sum_to_one_but_are_non_negative(netzero2040_file):
    """Shares encode demand growth too, so they need not sum to 1; stock is non-negative."""
    result = get_transport_sector_technology_stock(netzero2040_file, "High Demand")

    share_rows = [f"{variable} Share" for variable in TRANSPORT_CAR_VARIABLES]
    share_sums = result.loc[share_rows].sum()
    assert not all(math.isclose(total, 1.0) for total in share_sums)

    absolute_rows = result.loc[TRANSPORT_CAR_VARIABLES]
    assert (absolute_rows >= 0).all().all()
