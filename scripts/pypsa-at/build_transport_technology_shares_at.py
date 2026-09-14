# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Build Austrian road-transport technology shares and stock from the NetZero2040
Zenodo scenario data.


"""

import math
from typing import Literal

import numpy as np
import pandas as pd
import pyam
from snakemake.script import Snakemake

from scripts._helpers import configure_logging, set_scenario_config

TRANSPORT_SCENARIO_DEMAND_DICT: dict[str, list[str]] = {
    "High Demand": [
        "NetZero2040 high-import/high-demand base",
        "NetZero2040 low-import/high-demand base",
    ],
    "Low Demand": [
        "NetZero2040 high-import/low-demand base",
        "NetZero2040 low-import/low-demand base",
    ],
}

TRANSPORT_CAR_VARIABLES: list[str] = [
    "Stock|Cars|Passenger|Combustion",
    "Stock|Cars|Passenger|Electric",
    "Stock|Cars|Passenger|Fuel Cell",
]

# Years the model reports directly. 2025 and 2035 are not reported and are
# always covered by the extrapolation/interpolation logic below.
KNOWN_YEARS: list[int] = [2021, 2023, 2030, 2040]
TARGET_YEARS: list[int] = [2025, 2030, 2035, 2040, 2045, 2050]

VARIABLE_TO_ENGINE = {
    "Stock|Cars|Passenger|Combustion": "ice",
    "Stock|Cars|Passenger|Electric": "electric",
    "Stock|Cars|Passenger|Fuel Cell": "fuel_cell",
}


def _loocv_mse(years: np.ndarray, values: np.ndarray, degree: int) -> float:
    """Leave-one-out cross-validation MSE for a polynomial fit of the given degree."""
    squared_errors = []
    for i in range(len(years)):
        train_years = np.delete(years, i)
        train_values = np.delete(values, i)
        coeffs = np.polyfit(train_years, train_values, degree)
        prediction = np.polyval(coeffs, years[i])
        squared_errors.append((prediction - values[i]) ** 2)
    return float(np.mean(squared_errors))


def _extrapolate(series: pd.Series, target_year: int) -> float:
    """Fit polynomials (degree 1-3) to `series`, pick the best by LOOCV, evaluate at `target_year`."""
    years = series.index.to_numpy(dtype=float)
    values = series.to_numpy(dtype=float)
    cv_errors = {degree: _loocv_mse(years, values, degree) for degree in (1, 2, 3)}
    best_degree = min(cv_errors, key=cv_errors.get)
    coeffs = np.polyfit(years, values, best_degree)
    return float(np.polyval(coeffs, target_year))


def _extrapolate_nonnegative(series: pd.Series, target_year: int) -> float:
    """`_extrapolate` clipped at zero to prevent extrapolation artefacts - a technology's car stock cannot be negative."""
    return max(_extrapolate(series, target_year), 0.0)


def get_transport_sector_technology_stock(
    file_path: str,
    scenario: Literal["High Demand", "Low Demand"],
    common_basis_year: int,
) -> pd.DataFrame:
    """
    Compute passenger car stock (absolute and share) by technology as basis
    for transport demand per technology assuming constant km/car.

    Filters passenger car stock (combustion/electric/fuel cell) for Austria
    from a pyam-valid IAMC scenario file, normalizes it against a fixed base
    year (``common_basis_year``) to get shares including demand changes, and
    extrapolates years beyond the model's reported horizon via a polynomial
    fit. Calculates both, the absolute stock and the technology share for
    each of ``TARGET_YEARS``.

    Note
    ----

    The share output does not necessarily sum to 1 across technologies for a
    given year - it also encodes a demand-growth factor relative to the
    ``common_basis_year`` car-stock baseline.

    Parameters
    ----------
    file_path
        Path to the IAMC-format scenario xlsx file.
    scenario
        Demand scenario to select ("High Demand" or "Low Demand"); each maps
        to a pair of IAMC scenario names that are averaged together.
    common_basis_year
        Year the technology shares are normalized against - the model's
        base year (``scenario.planning_horizons[0]`` in config), since that
        horizon's demand is patched separately from real statistics.

    Returns
    -------
    :
        DataFrame indexed by the three car variables and their " Share"
        counterparts (6 rows), columns are ``TARGET_YEARS``.
    """

    if scenario not in TRANSPORT_SCENARIO_DEMAND_DICT:
        raise ValueError(
            f"Unknown scenario {scenario!r}. Expected one of "
            f"{list(TRANSPORT_SCENARIO_DEMAND_DICT)}."
        )

    pdf = pyam.IamDataFrame(file_path)
    cars_absolute = pdf.filter(
        scenario=TRANSPORT_SCENARIO_DEMAND_DICT[scenario],
        variable=TRANSPORT_CAR_VARIABLES,
        region="Austria",
    ).pivot_table(index="variable", columns="year", values="value", aggfunc="mean")

    # treat a missing technology/year cell as reported-zero stock (IAMC valid)
    cars_absolute_numeric = cars_absolute.apply(pd.to_numeric, errors="coerce").fillna(
        0
    )

    known_years = [
        year for year in KNOWN_YEARS if year in cars_absolute_numeric.columns
    ]

    # Extrapolate each technology's absolute stock independently from the
    # known years
    # the technology composition for a given year then falls out
    # of these independently-derived absolutes
    extrapolation_years = sorted(
        (set(TARGET_YEARS) | {common_basis_year}) - set(known_years)
    )
    absolute_stock = cars_absolute_numeric.copy()
    for target_year in extrapolation_years:
        for variable in TRANSPORT_CAR_VARIABLES:
            absolute_stock.loc[variable, target_year] = _extrapolate_nonnegative(
                cars_absolute_numeric.loc[variable, known_years], target_year
            )

    common_basis = absolute_stock.loc[TRANSPORT_CAR_VARIABLES, common_basis_year].sum()
    if math.isclose(common_basis, 0):
        raise ValueError(
            f"Number of cars in {common_basis_year} used for comparison is near zero."
        )

    technology_shares = absolute_stock.div(common_basis, axis=1)
    technology_shares.index = [
        f"{variable} Share" for variable in technology_shares.index
    ]
    cars_absolute_with_shares = pd.concat([absolute_stock, technology_shares])

    relevant_rows = TRANSPORT_CAR_VARIABLES + [
        f"{variable} Share" for variable in TRANSPORT_CAR_VARIABLES
    ]
    return cars_absolute_with_shares.loc[relevant_rows, TARGET_YEARS]


def main(snakemake: Snakemake) -> None:
    """
    Build the Austrian road-transport technology shares/stock CSV.

    Parameters
    ----------
    snakemake
        The Snakemake workflow object providing inputs, params, and config.

    Returns
    -------
    :
        Result is written to the snakemake output.
    """
    stock = get_transport_sector_technology_stock(
        snakemake.input.netzero2040_scenarios,
        snakemake.params.scenario,
        common_basis_year=snakemake.params.planning_horizons[0],
    )
    rename_map = {}
    for variable, engine in VARIABLE_TO_ENGINE.items():
        rename_map[variable] = engine
        rename_map[f"{variable} Share"] = f"{engine}_share"
    stock = stock.rename(index=rename_map)
    stock.to_csv(snakemake.output.transport_technology_shares)


if __name__ == "__main__":
    if "snakemake" not in globals():
        from scripts._helpers import mock_snakemake

        snakemake = mock_snakemake("build_transport_technology_shares_at")

    configure_logging(snakemake)
    set_scenario_config(snakemake)
    main(snakemake)
