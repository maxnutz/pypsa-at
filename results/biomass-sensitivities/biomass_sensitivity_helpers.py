# SPDX-FileCopyrightText: 2023-2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Heavier-computation helpers for the biomass availability sensitivity notebook.

Scans ``results/biomass-sensitivities/BIO_*`` scenario directories (produced by
``config/config.sensitivities-biomass.yaml``), parses per-year solve status from the
Snakemake logs, and extracts a small set of statistics from each solved network into a
tidy long-format DataFrame. Extracted statistics are cached to disk (one parquet file
per solved scenario/year) so the marimo notebook stays fast on reruns; a cache entry is
recomputed automatically once the source ``.nc`` file is newer than it, so results stay
in sync as ``run_job_nora.sh`` finishes more scenario/year combinations in the
background.

Not covered by CLAUDE.md's ``test/`` layout (this is a results-specific analysis
artifact, not `mods/`, `evals/`, or `scripts/pypsa-at/`); verified instead by running
the notebook against the real (partially solved) campaign on disk.
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

RUN_PREFIX = "biomass-sensitivities"
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

# Bumped whenever extract_metrics()'s output schema or logic changes, so a code change
# doesn't silently keep serving stale cached rows computed under the old logic (the
# mtime check alone can't catch that, since the source .nc file didn't change).
CACHE_VERSION = 2

SOLVE_STATUS_LOG = "logs/{stem}_python.log"
TERMINATION_RE = re.compile(r"Termination condition:\s*(\S+)")


def discover_scenarios(root: Path) -> pd.DataFrame:
    """
    Find biomass sensitivity scenario directories and their scaling factor.

    Parameters
    ----------
    root
        The results folder to scan, e.g. ``results/biomass-sensitivities``.

    Returns
    -------
    :
        One row per scenario with columns ``scenario`` (e.g. "BIO_150"),
        ``factor`` (e.g. 1.5), and ``path``. Sorted by factor.
    """
    rows = []
    for path in sorted(root.glob("BIO_*")):
        if not path.is_dir():
            continue
        match = re.match(r"BIO_(\d+)$", path.name)
        if not match:
            continue
        rows.append(
            {
                "scenario": path.name,
                "factor": int(match.group(1)) / 100,
                "path": path,
            }
        )
    return pd.DataFrame(rows).sort_values("factor").reset_index(drop=True)


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
        The results folder to scan, e.g. ``results/biomass-sensitivities``.

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


def _series_to_tidy(series: pd.Series, metric: str) -> pd.DataFrame:
    """Turn a pypsa.statistics Series (any index shape) into tidy metric rows."""
    frame = series.rename("value").reset_index()
    if "carrier" not in frame.columns:
        frame["carrier"] = None
    if "bus_carrier" not in frame.columns:
        frame["bus_carrier"] = None
    frame["metric"] = metric
    return frame[["metric", "carrier", "bus_carrier", "value"]]


def _weighted_average_price(n, carrier: str) -> float:
    """
    Withdrawal-weighted average marginal price across all buses of one carrier.

    Buses are first time-weighted (via snapshot weightings) to one annual average
    price each, then averaged across regions weighted by each region's annual
    withdrawal of that carrier, so regions that actually consume more of it dominate
    the system-level number. Falls back to an unweighted mean across regions when
    total withdrawal is zero (e.g. the resource is not scarce and marginal price is
    ~0 everywhere).

    Parameters
    ----------
    n
        A loaded, statistics-patched pypsa.Network.
    carrier
        A bus carrier name, e.g. "solid biomass".

    Returns
    -------
    :
        The weighted-average marginal price in EUR/MWh, or NaN if the carrier has no
        buses in this network.
    """
    buses = n.buses[n.buses.carrier == carrier]
    if buses.empty:
        return np.nan

    weights_t = n.snapshot_weightings.objective
    marginal_price = n.buses_t.marginal_price[buses.index]
    price_per_bus = marginal_price.mul(weights_t, axis=0).sum() / weights_t.sum()
    price_per_location = price_per_bus.set_axis(
        buses.loc[price_per_bus.index, "location"]
    )

    withdrawal_per_location = n.statistics.withdrawal(
        bus_carrier=carrier, groupby=["location"]
    )
    weight = withdrawal_per_location.reindex(price_per_location.index).fillna(0)

    if weight.sum() > 0:
        return float((price_per_location * weight).sum() / weight.sum())
    return float(price_per_location.mean())


def extract_metrics(n, sector_map: dict[str, str]) -> pd.DataFrame:
    """
    Extract the statistics needed for the biomass sensitivity notebook.

    Runs all statistics calls once per network so a network only needs to be loaded a
    single time regardless of how many questions the notebook answers from it.

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
        ``bus_carrier``, ``value``, ``unit``. One of four ``metric`` values:
        "sector_use", "total_biomass_use", "biomass_price", "carrier_supply".
    """
    frames = []

    # Q2 - sector use: withdrawal from the primary biomass buses, bucketed by carrier
    # into a sector via sector_map. Inter-regional transport links are dropped first
    # since they do not represent end use.
    sector_withdrawal = n.statistics.withdrawal(
        bus_carrier=BIOMASS_PRIMARY_BUS_CARRIERS, groupby=["carrier", "bus_carrier"]
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
    total_use = n.statistics.supply(
        bus_carrier=BIOMASS_PRIMARY_BUS_CARRIERS, groupby=["carrier", "bus_carrier"]
    )
    total_use = total_use[
        ~total_use.index.get_level_values("carrier").isin(BIOMASS_TRANSPORT_CARRIERS)
    ]
    frames.append(_series_to_tidy(total_use, "total_biomass_use"))

    # Q3 - biomass price per primary carrier.
    price_rows = [
        {
            "metric": "biomass_price",
            "carrier": carrier,
            "bus_carrier": carrier,
            "value": _weighted_average_price(n, carrier),
        }
        for carrier in BIOMASS_PRIMARY_BUS_CARRIERS
    ]
    frames.append(pd.DataFrame(price_rows))

    # Q5 - system-wide supply of every carrier, to compare against the biomass factor.
    carrier_supply = n.statistics.supply(groupby=["carrier", "bus_carrier"])
    frames.append(_series_to_tidy(carrier_supply, "carrier_supply"))

    result = pd.concat(frames, ignore_index=True)
    result["unit"] = np.where(result["metric"] == "biomass_price", "EUR/MWh", "MWh")
    return result


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
        The results folder to scan, e.g. ``results/biomass-sensitivities``.
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
                "metric",
                "carrier",
                "bus_carrier",
                "sector",
                "value",
                "unit",
            ]
        )
    return pd.concat(frames, ignore_index=True)
