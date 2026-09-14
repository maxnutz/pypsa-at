# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Unit tests for the vendored NetZero2040 transport technology stock builder."""

import importlib
import math
from types import SimpleNamespace

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
VARIABLE_TO_ENGINE = build_transport_technology_shares_at.VARIABLE_TO_ENGINE

# The model's base year in these tests mirrors currents scenario.planning_horizons[0]
# from config, which get_transport_sector_technology_stock's caller
# (build_transport_technology_shares_at.main) passes through as
# common_basis_year.
COMMON_BASIS_YEAR = 2025

SCENARIO_VALUES = {
    "Stock|Cars|Passenger|Combustion": {2021: 1000, 2023: 900, 2030: 700, 2040: 400},
    "Stock|Cars|Passenger|Electric": {2021: 100, 2023: 150, 2030: 300, 2040: 600},
    "Stock|Cars|Passenger|Fuel Cell": {2021: 0, 2023: 5, 2030: 20, 2040: 50},
}


def _expected_absolute(variable, year):
    """Reference absolute stock for one technology at `year`"""
    known = pd.Series({yr: SCENARIO_VALUES[variable][yr] for yr in KNOWN_YEARS})
    if year in KNOWN_YEARS:
        return known.loc[year]
    return build_transport_technology_shares_at._extrapolate_nonnegative(known, year)


def _expected_common_basis(basis_year):
    """Reference total stock at `basis_year`: known-year sum, or sum of each technology's own extrapolation."""
    return sum(_expected_absolute(variable, basis_year) for variable in SCENARIO_VALUES)


# extrapolate COMMON_BASIS year from known years.
COMMON_BASIS = _expected_common_basis(COMMON_BASIS_YEAR)


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
        get_transport_sector_technology_stock(
            netzero2040_file, "Medium Demand", common_basis_year=COMMON_BASIS_YEAR
        )


def test_output_shape(netzero2040_file):
    result = get_transport_sector_technology_stock(
        netzero2040_file, "High Demand", common_basis_year=COMMON_BASIS_YEAR
    )

    expected_rows = TRANSPORT_CAR_VARIABLES + [
        f"{variable} Share" for variable in TRANSPORT_CAR_VARIABLES
    ]
    assert list(result.index) == expected_rows
    assert list(result.columns) == TARGET_YEARS


def test_known_years_pass_through_unchanged(netzero2040_file):
    """Years directly reported in the source file are not extrapolated."""
    result = get_transport_sector_technology_stock(
        netzero2040_file, "High Demand", common_basis_year=COMMON_BASIS_YEAR
    )

    for variable, years in SCENARIO_VALUES.items():
        for year in (2030, 2040):
            assert year in KNOWN_YEARS
            assert result.loc[variable, year] == pytest.approx(years[year])


def test_extrapolated_absolute_values_are_independent_per_technology(netzero2040_file):
    """Each technology's extrapolated stock follows its own trend, not a mix frozen at the last known year."""
    result = get_transport_sector_technology_stock(
        netzero2040_file, "High Demand", common_basis_year=COMMON_BASIS_YEAR
    )

    for variable in TRANSPORT_CAR_VARIABLES:
        for year in (2025, 2035, 2045, 2050):
            assert result.loc[variable, year] == pytest.approx(
                _expected_absolute(variable, year)
            )


def test_extrapolated_years_are_share_absolute_consistent(netzero2040_file):
    """For years beyond the reported horizon, absolute == share * common_basis."""
    result = get_transport_sector_technology_stock(
        netzero2040_file, "High Demand", common_basis_year=COMMON_BASIS_YEAR
    )

    extrapolated_years = [year for year in TARGET_YEARS if year not in KNOWN_YEARS]
    assert extrapolated_years == [2025, 2035, 2045, 2050]

    for variable in TRANSPORT_CAR_VARIABLES:
        for year in extrapolated_years:
            absolute = result.loc[variable, year]
            share = result.loc[f"{variable} Share", year]
            assert absolute == pytest.approx(share * COMMON_BASIS)


def test_common_basis_year_shares_sum_to_one(netzero2040_file):
    """At the configured basis year itself, shares reduce to the plain technology mix (sum to 1)."""
    result = get_transport_sector_technology_stock(
        netzero2040_file, "High Demand", common_basis_year=COMMON_BASIS_YEAR
    )

    share_rows = [f"{variable} Share" for variable in TRANSPORT_CAR_VARIABLES]
    assert result.loc[share_rows, COMMON_BASIS_YEAR].sum() == pytest.approx(1.0)


def test_common_basis_year_follows_argument(netzero2040_file):
    """
    The normalization year tracks whatever is passed in, not a hardcoded year.

    Mirrors the scenario where the model's first planning horizon moves from
    2025 to a year that already has real (NEA) data, e.g. 2030: the shares
    must then be normalized against 2030 instead.
    """
    other_basis_year = 2030
    result = get_transport_sector_technology_stock(
        netzero2040_file, "High Demand", common_basis_year=other_basis_year
    )

    share_rows = [f"{variable} Share" for variable in TRANSPORT_CAR_VARIABLES]
    assert result.loc[share_rows, other_basis_year].sum() == pytest.approx(1.0)

    other_common_basis = _expected_common_basis(other_basis_year)
    assert other_common_basis != pytest.approx(COMMON_BASIS)
    for variable in TRANSPORT_CAR_VARIABLES:
        for year in (2025, 2035, 2045, 2050):
            absolute = result.loc[variable, year]
            share = result.loc[f"{variable} Share", year]
            assert absolute == pytest.approx(share * other_common_basis)


def test_shares_need_not_sum_to_one_but_are_non_negative(netzero2040_file):
    """Shares encode demand growth too, so they need not sum to 1; stock is non-negative."""
    result = get_transport_sector_technology_stock(
        netzero2040_file, "High Demand", common_basis_year=COMMON_BASIS_YEAR
    )

    share_rows = [f"{variable} Share" for variable in TRANSPORT_CAR_VARIABLES]
    share_sums = result.loc[share_rows].sum()
    assert not all(math.isclose(total, 1.0) for total in share_sums)

    absolute_rows = result.loc[TRANSPORT_CAR_VARIABLES]
    assert (absolute_rows >= 0).all().all()


ZERO_STOCK_YEAR = 2021
ZERO_STOCK_SCENARIO_VALUES = {
    "Stock|Cars|Passenger|Combustion": {2021: 0, 2023: 900, 2030: 700, 2040: 400},
    "Stock|Cars|Passenger|Electric": {2021: 0, 2023: 150, 2030: 300, 2040: 600},
    "Stock|Cars|Passenger|Fuel Cell": {2021: 0, 2023: 5, 2030: 20, 2040: 50},
}


@pytest.fixture
def netzero2040_zero_stock_file(tmp_path):
    """A synthetic IAMC file where every technology reports zero stock in ZERO_STOCK_YEAR."""
    rows = []
    for scenario in (
        "NetZero2040 high-import/high-demand base",
        "NetZero2040 low-import/high-demand base",
    ):
        for variable, years in ZERO_STOCK_SCENARIO_VALUES.items():
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
    path = tmp_path / "netzero2040-times-pyam-zero-stock.xlsx"
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


def test_raises_when_common_basis_year_stock_is_near_zero(netzero2040_zero_stock_file):
    """A common-basis year with (near-)zero total car stock cannot be used to normalize shares."""
    with pytest.raises(ValueError, match="near zero"):
        get_transport_sector_technology_stock(
            netzero2040_zero_stock_file,
            "High Demand",
            common_basis_year=ZERO_STOCK_YEAR,
        )


def test_main_writes_csv_with_engine_renamed_rows(tmp_path, netzero2040_file):
    """``main`` writes the same values as ``get_transport_sector_technology_stock``, with rows renamed to engine keys."""
    output_path = tmp_path / "transport_technology_shares_at.csv"
    snakemake = SimpleNamespace(
        input=SimpleNamespace(netzero2040_scenarios=netzero2040_file),
        params=SimpleNamespace(
            scenario="High Demand", planning_horizons=[COMMON_BASIS_YEAR, 2030, 2040]
        ),
        output=SimpleNamespace(transport_technology_shares=output_path),
    )

    build_transport_technology_shares_at.main(snakemake)

    result = pd.read_csv(output_path, index_col=0)
    result.columns = result.columns.astype(int)

    expected_rows = list(VARIABLE_TO_ENGINE.values()) + [
        f"{engine}_share" for engine in VARIABLE_TO_ENGINE.values()
    ]
    assert list(result.index) == expected_rows
    assert list(result.columns) == TARGET_YEARS

    rename_map = {}
    for variable, engine in VARIABLE_TO_ENGINE.items():
        rename_map[variable] = engine
        rename_map[f"{variable} Share"] = f"{engine}_share"
    reference = get_transport_sector_technology_stock(
        netzero2040_file, "High Demand", common_basis_year=COMMON_BASIS_YEAR
    ).rename(index=rename_map)

    pd.testing.assert_frame_equal(
        result, reference, check_dtype=False, check_names=False
    )
