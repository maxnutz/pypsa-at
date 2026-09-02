# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Build Austrian road-transport technology shares and stock from the NetZero2040
Zenodo scenario data.

Vendored (and extended) from
``esm_scenario_preprocessing.input_preprocessing.get_transport_sector_technology_shares``
(https://github.com/inwe-boku/ESM_scenario_preprocessing, commit
``ee868938732f66d63bff97f0e8c1bce49ce44248``). Not added as a package
dependency because no PyPI release exists for it.
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


def _extrapolate(demand_factor: pd.Series, target_year: int) -> float:
    """Fit polynomials (degree 1-3) to `demand_factor`, pick the best by LOOCV, evaluate at `target_year`."""
    years = demand_factor.index.to_numpy(dtype=float)
    values = demand_factor.to_numpy(dtype=float)
    cv_errors = {degree: _loocv_mse(years, values, degree) for degree in (1, 2, 3)}
    best_degree = min(cv_errors, key=cv_errors.get)
    coeffs = np.polyfit(years, values, best_degree)
    return float(np.polyval(coeffs, target_year))


def get_transport_sector_technology_stock(
    file_path: str, scenario: Literal["High Demand", "Low Demand"]
) -> pd.DataFrame:
    """
    Compute passenger car stock (absolute and share) by technology.

    Filters passenger car stock (combustion/electric/fuel cell) for Austria
    from a pyam-valid IAMC scenario file, normalizes it against a fixed base
    year (2023) to get shares including demand changes, and extrapolates
    years beyond the model's reported horizon via a polynomial fit. Returns
    both the absolute stock and the technology share for each of
    ``TARGET_YEARS``.

    Note
    ----
    Vendored from ``esm_scenario_preprocessing.input_preprocessing``, extended
    to also return absolute stock counts (upstream only returns shares) and
    to extrapolate every target year beyond the reported horizon, not just
    2050.

    The share output does not necessarily sum to 1 across technologies for a
    given year -- it also encodes a demand-growth factor relative to the 2023
    car-stock baseline. This is intentional; see
    ``check_land_transport_shares()`` in ``scripts/prepare_sector_network.py``.

    Parameters
    ----------
    file_path
        Path to the IAMC-format scenario xlsx file.
    scenario
        Demand scenario to select ("High Demand" or "Low Demand"); each maps
        to a pair of IAMC scenario names that are averaged together.

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

    # Technologies are not necessarily reported for every year the source
    # file covers (e.g. NetZero2040 stops reporting Combustion/Fuel Cell
    # stock at 2035 while Electric continues to 2040): treat a missing
    # technology/year cell as reported-zero stock rather than NaN, so it
    # doesn't propagate into technology_fractions_last_known below.
    cars_absolute_numeric = cars_absolute.apply(pd.to_numeric, errors="coerce").fillna(
        0
    )
    common_basis = cars_absolute_numeric.sum(axis=0, skipna=True).loc[2023]
    if math.isclose(common_basis, 0):
        raise ValueError("Number of cars in 2023 used for comparison is near zero.")

    technology_shares = cars_absolute_numeric.div(common_basis, axis=1)
    technology_shares.index = [
        f"{variable} Share" for variable in technology_shares.index
    ]
    cars_absolute_with_shares = pd.concat([cars_absolute_numeric, technology_shares])

    known_years = [
        year for year in KNOWN_YEARS if year in cars_absolute_with_shares.columns
    ]
    last_known_year = max(known_years)
    demand_factor = cars_absolute_with_shares.filter(like="Share", axis=0)[
        known_years
    ].sum(axis=0)

    technology_shares_last_known = cars_absolute_with_shares.filter(
        like="Share", axis=0
    )[last_known_year]
    technology_fractions_last_known = (
        technology_shares_last_known / technology_shares_last_known.sum()
    )
    # align by label (variable name, stripped of " Share"), not position
    technology_fractions_last_known.index = [
        name.removesuffix(" Share") for name in technology_fractions_last_known.index
    ]

    for target_year in TARGET_YEARS:
        if target_year in known_years:
            continue
        demand_factor_target = _extrapolate(demand_factor, target_year)
        # Share rows: mix fraction (from the last known year) x extrapolated
        # total demand factor - mirrors the upstream 2050-only logic.
        cars_absolute_with_shares.loc[
            [f"{name} Share" for name in technology_fractions_last_known.index],
            target_year,
        ] = (technology_fractions_last_known * demand_factor_target).to_numpy()
        # Absolute rows: same share value scaled back up by common_basis -
        # this is the part upstream never computed (it only ever returned
        # shares), which is what we need for absolute-count consumers.
        cars_absolute_with_shares.loc[
            technology_fractions_last_known.index, target_year
        ] = (
            technology_fractions_last_known * demand_factor_target * common_basis
        ).to_numpy()

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
        snakemake.input.netzero2040_scenarios, snakemake.params.scenario
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
