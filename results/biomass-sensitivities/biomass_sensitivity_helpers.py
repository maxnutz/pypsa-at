# SPDX-FileCopyrightText: 2023-2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Heavier-computation helpers for the biomass availability sensitivity notebook.

Scans a campaign directory under ``results/`` for ``BIO_*`` scenario directories,
parses per-year solve status from the Snakemake logs, and extracts a small set of
statistics from each solved network into a tidy long-format DataFrame. Extracted
statistics are cached to disk (one parquet file per solved scenario/year) so the
marimo notebook stays fast on reruns; a cache entry is recomputed automatically once
the source ``.nc`` file is newer than it, so results stay in sync as
``run_job_nora.sh`` finishes more scenario/year combinations in the background.

Two campaigns share these helpers and the notebook:

``results/biomass-sensitivities``
    ``config/config.sensitivities-biomass.yaml``, scenarios ``BIO_<factor>``. The
    factor scales biomass potentials in *every* modelled country.
``results/biomass-at-sensitivities``
    ``config/config.sensitivities-biomass-at.yaml``, scenarios ``BIO_AT_<factor>``.
    The factor scales Austrian potentials only; everything else stays unscaled.

Every extracted metric carries a ``location`` (the AT10/NUTS-style region code, e.g.
``AT12``, ``DE1``, ``FR``), so the notebook can aggregate over all modelled regions or
restrict to Austria without re-reading any network.

Not covered by CLAUDE.md's ``test/`` layout (this is a results-specific analysis
artifact, not `mods/`, `evals/`, or `scripts/pypsa-at/`); verified instead by running
the notebook against the real (partially solved) campaigns on disk.
"""

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.fileio import read_networks  # noqa: E402

RESULTS_ROOT = REPO_ROOT / "results"
PLANNING_HORIZONS = ["2025", "2030", "2040", "2050"]
NETWORK_SUBDIR = "networks"
NETWORK_STEM = "base_s_adm__none_{year}"

# Buses where the biomass *resource* generators sit (upper-bound / must-run
# generators from `biomass_potentials_s_*.csv`, see `mods/potentials/biomass.py`).
# Downstream buses such as "solid biomass for industry" are fed *from* these via a
# Link and are deliberately excluded here to avoid double-counting the same primary
# energy twice.
#
# "municipal solid waste" is deliberately excluded, mirroring
# `mods.biomass_potential_scaling.carriers` in config/config.at.yaml: it is mixed
# waste (partly non-biogenic) incinerated as waste-to-energy, not a biomass feedstock
# whose availability this sensitivity scales or is meant to evaluate.
BIOMASS_PRIMARY_BUS_CARRIERS = ["solid biomass", "biogas"]

# Carriers that only move biomass between regions on the same bus_carrier rather than
# consuming it - excluded from the sector-use breakdown (see SECTOR_MAP in the
# notebook).
BIOMASS_TRANSPORT_CARRIERS = ["solid biomass transport"]

# Region scopes offered by the notebook's radio button, mapped to the prefix a
# `location` has to start with to be counted. ``None`` keeps every modelled region.
# Austrian locations are the AT10 codes (AT11 ... AT34, plus the AT333 subnet); there
# is no bare "AT" location under administrative clustering, so a prefix match is both
# sufficient and exact.
REGION_SCOPES: dict[str, str | None] = {
    "All regions": None,
    "Austria": "AT",
}

# Bumped whenever extract_metrics()'s output schema or logic changes, so a code change
# doesn't silently keep serving stale cached rows computed under the old logic (the
# mtime check alone can't catch that, since the source .nc file didn't change).
CACHE_VERSION = 3

SOLVE_STATUS_LOG = "logs/{stem}_python.log"
TERMINATION_RE = re.compile(r"Termination condition:\s*(\S+)")


SCENARIO_RE = re.compile(r"^BIO(?:_[A-Z]+)*_(\d+)$")

SCENARIO_COLUMNS = ["scenario", "factor", "path"]


def discover_scenarios(root: Path) -> pd.DataFrame:
    """
    Find biomass sensitivity scenario directories and their scaling factor.

    Matches both campaigns' naming schemes: ``BIO_150`` (all countries scaled) and
    ``BIO_AT_150`` (Austria only), i.e. ``BIO`` followed by any number of uppercase
    qualifiers and a trailing percentage.

    Parameters
    ----------
    root
        The campaign folder to scan, e.g. ``results/biomass-sensitivities`` or
        ``results/biomass-at-sensitivities``.

    Returns
    -------
    :
        One row per scenario with columns ``scenario`` (e.g. "BIO_150"),
        ``factor`` (e.g. 1.5), and ``path``. Sorted by factor. Empty (but with those
        columns) when ``root`` holds no scenario directories.
    """
    rows = []
    for path in sorted(root.glob("BIO_*")):
        if not path.is_dir():
            continue
        match = SCENARIO_RE.match(path.name)
        if not match:
            continue
        rows.append(
            {
                "scenario": path.name,
                "factor": int(match.group(1)) / 100,
                "path": path,
            }
        )
    if not rows:
        return pd.DataFrame(columns=SCENARIO_COLUMNS)
    return pd.DataFrame(rows).sort_values("factor").reset_index(drop=True)


def discover_campaigns(results_root: Path = RESULTS_ROOT) -> list[str]:
    """
    List the campaign folder names under ``results/`` that hold biomass scenarios.

    Used to populate the notebook's campaign selector, so a newly finished campaign
    shows up without editing the notebook.

    Parameters
    ----------
    results_root
        The folder holding one subfolder per run prefix, i.e. ``results/``.

    Returns
    -------
    :
        Sorted campaign names, e.g. ``["biomass-at-sensitivities",
        "biomass-sensitivities"]``.
    """
    if not results_root.is_dir():
        return []
    return sorted(
        path.name
        for path in results_root.iterdir()
        if path.is_dir() and not discover_scenarios(path).empty
    )


def parse_solve_status(scenario_dir: Path) -> pd.DataFrame:
    """
    Determine the solve status of every planning horizon for one scenario.

    Reads the Snakemake ``solve_sector_network`` python log per year. A missing log
    means the year has not been attempted yet; a log without an "optimal" termination
    condition means the solve failed (most commonly infeasible at low biomass
    availability); a log reporting "optimal" whose network file is missing means a run
    that was killed mid-write.

    Parameters
    ----------
    scenario_dir
        Path to one scenario's result folder, e.g.
        ``results/biomass-sensitivities/BIO_150``.

    Returns
    -------
    :
        One row per planning horizon with columns ``year``, ``status`` (one of
        "optimal", "infeasible", "not run", "incomplete") and ``termination_condition``
        (raw string from the log, or ``None``).
    """
    rows = []
    for year in PLANNING_HORIZONS:
        stem = NETWORK_STEM.format(year=year)
        log_path = scenario_dir / SOLVE_STATUS_LOG.format(stem=stem)
        network_path = scenario_dir / NETWORK_SUBDIR / f"{stem}.nc"

        if not log_path.exists():
            rows.append(
                {
                    "year": year,
                    "status": "not run",
                    "termination_condition": None,
                }
            )
            continue

        matches = TERMINATION_RE.findall(log_path.read_text())
        termination_condition = matches[-1] if matches else None

        if termination_condition == "optimal" and network_path.exists():
            status = "optimal"
        elif termination_condition == "optimal":
            status = "incomplete"
        else:
            status = "infeasible"

        rows.append(
            {
                "year": year,
                "status": status,
                "termination_condition": termination_condition,
            }
        )
    return pd.DataFrame(rows)


def all_solve_status(root: Path) -> pd.DataFrame:
    """
    Build the solve-status table for every discovered scenario.

    Parameters
    ----------
    root
        The campaign folder to scan, e.g. ``results/biomass-sensitivities``.

    Returns
    -------
    :
        One row per (scenario, year) with columns ``scenario``, ``factor``, ``year``,
        ``status``, ``termination_condition``.
    """
    scenarios = discover_scenarios(root)
    frames = []
    for _, row in scenarios.iterrows():
        status = parse_solve_status(row["path"])
        status["scenario"] = row["scenario"]
        status["factor"] = row["factor"]
        frames.append(status)
    if not frames:
        return pd.DataFrame(
            columns=["scenario", "factor", "year", "status", "termination_condition"]
        )
    return pd.concat(frames, ignore_index=True)[
        ["scenario", "factor", "year", "status", "termination_condition"]
    ]


METRIC_COLUMNS = ["metric", "carrier", "bus_carrier", "location", "value", "weight"]


def _series_to_tidy(series: pd.Series, metric: str) -> pd.DataFrame:
    """Turn a pypsa.statistics Series (any index shape) into tidy metric rows."""
    frame = series.rename("value").reset_index()
    for column in ("carrier", "bus_carrier", "location"):
        if column not in frame.columns:
            frame[column] = None
    frame["metric"] = metric
    frame["weight"] = np.nan
    return frame[METRIC_COLUMNS]


def _price_per_location(n, carrier: str) -> pd.DataFrame:
    """
    Annual average marginal price of one bus carrier, per region, with its weight.

    Each bus of the carrier is time-weighted (via snapshot weightings) into one annual
    average price, then averaged per region. The region's annual withdrawal of the
    carrier is returned alongside as ``weight``, so the notebook can form a
    withdrawal-weighted average over whatever set of regions the user selected (see
    :func:`weighted_price`) rather than being locked into a system-wide number here.

    Parameters
    ----------
    n
        A loaded, statistics-patched pypsa.Network.
    carrier
        A bus carrier name, e.g. "solid biomass".

    Returns
    -------
    :
        One row per region with columns ``location``, ``value`` (EUR/MWh) and
        ``weight`` (MWh withdrawn). Empty when the carrier has no buses.
    """
    buses = n.buses[n.buses.carrier == carrier]
    if buses.empty:
        return pd.DataFrame(columns=["location", "value", "weight"])

    weights_t = n.snapshot_weightings.objective
    marginal_price = n.buses_t.marginal_price[buses.index]
    price_per_bus = marginal_price.mul(weights_t, axis=0).sum() / weights_t.sum()
    price_per_location = price_per_bus.groupby(
        buses.loc[price_per_bus.index, "location"]
    ).mean()

    # statistics.withdrawal() keeps the component level in the index even when only
    # "location" is requested, so it has to be summed down to a plain location index
    # before it can line up with the prices.
    withdrawal = n.statistics.withdrawal(bus_carrier=carrier, groupby=["location"])
    withdrawal = withdrawal.groupby(level="location").sum()

    return pd.DataFrame(
        {
            "location": price_per_location.index,
            "value": price_per_location.to_numpy(),
            "weight": withdrawal.reindex(price_per_location.index)
            .fillna(0.0)
            .to_numpy(),
        }
    )


def extract_metrics(n, sector_map: dict[str, str]) -> pd.DataFrame:
    """
    Extract the statistics needed for the biomass sensitivity notebook.

    Runs all statistics calls once per network so a network only needs to be loaded a
    single time regardless of how many questions the notebook answers from it. Every
    metric is broken down by ``location`` so the notebook can aggregate over all
    modelled regions or restrict to Austria from the same cached rows.

    Parameters
    ----------
    n
        A loaded, statistics-patched pypsa.Network (as produced by
        ``evals.fileio.read_networks``).
    sector_map
        Mapping from end-use Link/Load carrier name to a human-readable sector label,
        applied to the sector-use breakdown. Carriers touching a biomass bus that are
        not in this map fall into an "(unmapped)" bucket rather than being dropped
        silently.

    Returns
    -------
    :
        Tidy long-format DataFrame with columns ``metric``, ``carrier``,
        ``bus_carrier``, ``location``, ``value``, ``weight``, ``sector``, ``unit``.
        One of four ``metric`` values: "sector_use", "total_biomass_use",
        "biomass_price", "carrier_supply". ``weight`` is only populated for
        "biomass_price" rows (see :func:`weighted_price`).
    """
    frames = []

    # Q2 - sector use: withdrawal from the primary biomass buses, bucketed by carrier
    # into a sector via sector_map. Inter-regional transport links are dropped first
    # since they do not represent end use.
    sector_withdrawal = n.statistics.withdrawal(
        bus_carrier=BIOMASS_PRIMARY_BUS_CARRIERS,
        groupby=["carrier", "bus_carrier", "location"],
    )
    sector_withdrawal = sector_withdrawal[
        ~sector_withdrawal.index.get_level_values("carrier").isin(
            BIOMASS_TRANSPORT_CARRIERS
        )
    ]
    sector_frame = _series_to_tidy(sector_withdrawal, "sector_use")
    sector_frame["sector"] = (
        sector_frame["carrier"].map(sector_map).fillna("(unmapped)")
    )
    frames.append(sector_frame)

    # Q4 - total biomass dispatched (per primary carrier; sum in the notebook for the
    # headline total). Transport links also show up as "supply" at the destination
    # region under statistics.supply(), so they are dropped here too, exactly like in
    # the sector-use breakdown, or the same shipped energy would be counted twice.
    # Note that dropping them is also what makes the Austria-only view meaningful:
    # what is left is biomass raised from Austrian potential, not biomass railed in.
    total_use = n.statistics.supply(
        bus_carrier=BIOMASS_PRIMARY_BUS_CARRIERS,
        groupby=["carrier", "bus_carrier", "location"],
    )
    total_use = total_use[
        ~total_use.index.get_level_values("carrier").isin(BIOMASS_TRANSPORT_CARRIERS)
    ]
    frames.append(_series_to_tidy(total_use, "total_biomass_use"))

    # Q3 - biomass price per primary carrier and region, with the withdrawal weight
    # kept alongside so the region scope chosen in the notebook decides the average.
    for carrier in BIOMASS_PRIMARY_BUS_CARRIERS:
        price_frame = _price_per_location(n, carrier)
        price_frame["metric"] = "biomass_price"
        price_frame["carrier"] = carrier
        price_frame["bus_carrier"] = carrier
        frames.append(price_frame[METRIC_COLUMNS])

    # Q5 - system-wide supply of every carrier, to compare against the biomass factor.
    carrier_supply = n.statistics.supply(groupby=["carrier", "bus_carrier", "location"])
    frames.append(_series_to_tidy(carrier_supply, "carrier_supply"))

    result = pd.concat(frames, ignore_index=True)
    result["unit"] = np.where(result["metric"] == "biomass_price", "EUR/MWh", "MWh")
    return result


def select_region(frame: pd.DataFrame, scope: str) -> pd.DataFrame:
    """
    Restrict tidy metric rows to one region scope.

    Parameters
    ----------
    frame
        Tidy metric rows carrying a ``location`` column, as produced by
        :func:`load_all_metrics`.
    scope
        A key of :data:`REGION_SCOPES`, e.g. "All regions" or "Austria".

    Returns
    -------
    :
        ``frame`` unchanged for the unrestricted scope, otherwise only the rows whose
        ``location`` starts with the scope's prefix.

    Raises
    ------
    KeyError
        If ``scope`` is not a known region scope.
    """
    prefix = REGION_SCOPES[scope]
    if prefix is None:
        return frame
    return frame[frame["location"].fillna("").str.startswith(prefix)]


def weighted_price(frame: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    """
    Collapse per-region price rows into a withdrawal-weighted average.

    Regions that actually consume more of the carrier dominate the resulting number.
    Falls back to an unweighted mean across regions when the group's total withdrawal
    is zero (e.g. the resource is not scarce and the marginal price is ~0 everywhere).

    Parameters
    ----------
    frame
        "biomass_price" rows with ``value`` (EUR/MWh) and ``weight`` (MWh) columns.
    group_columns
        Columns to average within, e.g. ``["factor", "year", "carrier"]``.

    Returns
    -------
    :
        One row per group with the averaged ``value``.
    """

    def _average(group: pd.DataFrame) -> float:
        weight = group["weight"].fillna(0.0)
        if weight.sum() > 0:
            return float((group["value"] * weight).sum() / weight.sum())
        return float(group["value"].mean())

    return (
        frame.groupby(group_columns)
        .apply(_average, include_groups=False)
        .rename("value")
        .reset_index()
    )


def cache_path(root: Path, scenario: str, year: str) -> Path:
    """Return the parquet cache path for one scenario/year extraction."""
    return root / ".cache" / f"{scenario}_{year}_v{CACHE_VERSION}.parquet"


def load_all_metrics(
    root: Path, sector_map: dict[str, str], force_recompute: bool = False
) -> pd.DataFrame:
    """
    Load (or compute and cache) extracted metrics for every solved scenario/year.

    Parameters
    ----------
    root
        The campaign folder to scan, e.g. ``results/biomass-sensitivities``.
    sector_map
        Passed through to :func:`extract_metrics`.
    force_recompute
        Ignore existing cache entries and recompute everything from the source
        networks.

    Returns
    -------
    :
        Concatenation of :func:`extract_metrics` output across all optimally-solved
        scenario/year combinations, with ``scenario``, ``factor`` and ``year`` columns
        added.
    """
    status = all_solve_status(root)
    solved = status[status["status"] == "optimal"]

    frames = []
    for _, row in solved.iterrows():
        scenario, factor, year = row["scenario"], row["factor"], row["year"]
        network_path = (
            root / scenario / NETWORK_SUBDIR / f"{NETWORK_STEM.format(year=year)}.nc"
        )
        entry_cache_path = cache_path(root, scenario, year)

        use_cache = (
            not force_recompute
            and entry_cache_path.exists()
            and entry_cache_path.stat().st_mtime >= network_path.stat().st_mtime
        )
        if use_cache:
            frame = pd.read_parquet(entry_cache_path)
        else:
            nc = read_networks([network_path])
            n = nc[year]
            frame = extract_metrics(n, sector_map)
            entry_cache_path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(entry_cache_path)

        frame = frame.copy()
        frame["scenario"] = scenario
        frame["factor"] = factor
        frame["year"] = year
        frames.append(frame)

    if not frames:
        return pd.DataFrame(
            columns=[
                "scenario",
                "factor",
                "year",
                *METRIC_COLUMNS,
                "sector",
                "unit",
            ]
        )
    return pd.concat(frames, ignore_index=True)
