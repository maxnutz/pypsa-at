# SPDX-FileCopyrightText: 2023-2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""
Collect solving benchmarks of finished runs into a growing CSV history.

Snakemake writes one benchmark file per solve job to
``results/{prefix}/{run_name}/benchmarks/{rule}/{wildcards}``. This module
reads those files, enriches them with metadata from the config snapshot
stored alongside them in ``results/{prefix}/{run_name}/configs/`` and
appends the result to a CSV file, so that solving times of past runs can
be used to estimate the runtime of future ones.

The script is meant to be run by hand after a run has finished:

``` shell
pixi run solve-benchmarks results/industrial-demand-sensitivities
```

``` shell
# collect several prefixes into a custom history file
pixi run solve-benchmarks results/v2025.02 results/sysgf -o data/solve_times.csv
```

``` shell
# show what would be collected without touching the CSV file
pixi run solve-benchmarks results --dry-run
```

An existing CSV file is never overwritten: new rows are appended at the
bottom, and rows that are already part of the history are skipped.
"""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import click
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path("solve_benchmarks.csv")
"""Default history file, outside the directories wiped by ``pixi run reset``."""

BENCHMARKS_DIR = "benchmarks"
SOLVE_RULE_PREFIX = "solve"

COLUMNS = [
    # identity
    "prefix",
    "run_name",
    "rule",
    "clusters",
    "opts",
    "sector_opts",
    "planning_horizon",
    # timing
    "start_time",
    "end_time",
    "solve_time_s",
    "solve_time_h",
    # cores
    "threads",
    "core_hours_allocated",
    "cpu_time_s",
    "core_hours_cpu",
    "mean_load",
    # memory
    "max_rss_gb",
    "max_pss_gb",
    "max_vms_gb",
    # resolution
    "at_admin_level",
    "resolution_sector",
    "resolution_elec",
    # provenance
    "solver",
    "solver_options",
    "benchmark_file",
]

KEY_COLUMNS = [
    "prefix",
    "run_name",
    "rule",
    "clusters",
    "opts",
    "sector_opts",
    "planning_horizon",
    "end_time",
]
"""Columns that identify a single solve job in the history file."""

_WILDCARD_PATTERNS = [
    # solve_sector_network, perfect foresight: base_s_adm__none_brownfield_all_years
    re.compile(
        r"^base_s_(?P<clusters>[^_]+)_(?P<opts>[^_]*)_(?P<sector_opts>[^_]*)"
        r"_brownfield_all_years\}?$"
    ),
    # solve_sector_network, myopic/overnight: base_s_adm__none_2025
    re.compile(
        r"^base_s_(?P<clusters>[^_]+)_(?P<opts>[^_]*)_(?P<sector_opts>[^_]*)"
        r"_(?P<planning_horizon>[0-9]{4})$"
    ),
    # solve_network / solve_operations_network: base_s_adm_elec_
    re.compile(r"^base_s_(?P<clusters>[^_]+)_elec_(?P<opts>[^_]*)$"),
]


_TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:")

METADATA_SECTIONS = ("clustering", "solving")
"""Config sections read for the run metadata."""


class _TolerantLoader(yaml.SafeLoader):
    """Loader that ignores the ``!!python/tuple`` tags in config snapshots."""


_TolerantLoader.add_multi_constructor("", lambda loader, suffix, node: None)


def read_config_sections(path: Path, sections: tuple[str, ...]) -> dict:
    """
    Read selected top-level sections of a config snapshot.

    Config snapshots are too large to parse in full - they reach tens of
    megabytes once the workflow inlines its resources - while the run
    metadata sits in a few sections near the top of the file. The
    sections are therefore sliced out line by line and only that slice
    is handed to the YAML parser.

    Parameters
    ----------
    path
        Path to the config snapshot.
    sections
        Names of the top-level sections to read.

    Returns
    -------
    :
        The parsed sections. Sections missing from the file are missing
        from the result.
    """
    missing = set(sections)
    section_lines: list[str] = []
    current = None

    with path.open() as f:
        for line in f:
            if _TOP_LEVEL_KEY.match(line):
                key = line.split(":", 1)[0]
                current = key if key in missing else None
                missing.discard(key)
            if current:
                section_lines.append(line)
            elif not missing:
                break

    return yaml.load("".join(section_lines), Loader=_TolerantLoader) or {}


def find_benchmark_files(paths: list[Path]) -> list[Path]:
    """
    Collect benchmark files of solve rules below the given paths.

    Parameters
    ----------
    paths
        Files or directories to search. Directories are searched
        recursively, so a results root, a run directory or a single
        benchmark directory are all valid inputs. Files are taken as is.

    Returns
    -------
    :
        Sorted list of benchmark files written by a solve rule.
    """
    files = []
    for path in paths:
        if path.is_file():
            if _is_solve_benchmark(path):
                files.append(path)
            continue
        files += [p for p in path.rglob("*") if p.is_file() and _is_solve_benchmark(p)]

    return sorted(set(files))


def _is_solve_benchmark(path: Path) -> bool:
    """Whether the path is a benchmark file written by a solve rule."""
    return path.parent.parent.name == BENCHMARKS_DIR and path.parent.name.startswith(
        SOLVE_RULE_PREFIX
    )


def parse_benchmark(path: Path) -> dict:
    """
    Read the single data row of a Snakemake benchmark file.

    Parameters
    ----------
    path
        Path to the benchmark file.

    Returns
    -------
    :
        Mapping of benchmark column names to float values, e.g.
        ``{"s": 1050.57, "max_rss": 22591.91, ...}``. Memory values are
        in MB, times in seconds.
    """
    df = pd.read_csv(path, sep="\t")
    if len(df) != 1:
        logger.warning(
            f"Expected one row in benchmark file {path}, found {len(df)}. "
            f"Using the maximum of all rows."
        )
    return df.max(numeric_only=True).to_dict()


def parse_wildcards(stem: str) -> dict:
    """
    Split a benchmark file name into its Snakemake wildcards.

    Parameters
    ----------
    stem
        The benchmark file name, e.g. ``base_s_adm__none_2025``.

    Returns
    -------
    :
        Mapping with the keys ``clusters``, ``opts``, ``sector_opts`` and
        ``planning_horizon``. Wildcards that are not part of the file
        name are set to an empty string.

    Examples
    --------
    >>> parse_wildcards("base_s_adm__none_2025")["planning_horizon"]
    '2025'
    """
    wildcards = {
        "clusters": "",
        "opts": "",
        "sector_opts": "",
        "planning_horizon": "",
    }
    for pattern in _WILDCARD_PATTERNS:
        match = pattern.match(stem)
        if match:
            return wildcards | match.groupdict(default="")

    logger.warning(f"Could not parse wildcards from benchmark file name '{stem}'.")
    return wildcards


def read_run_metadata(run_dir: Path, stem: str) -> dict:
    """
    Read run metadata from the config snapshot of a solved network.

    Parameters
    ----------
    run_dir
        The run directory, i.e. ``results/{prefix}/{run_name}``.
    stem
        The benchmark file name, used to find the matching config
        snapshot ``configs/config.{stem}.yaml``.

    Returns
    -------
    :
        Mapping with spatial and temporal resolution, solver and thread
        settings. Values are empty if no config snapshot exists.
    """
    empty = {
        "at_admin_level": "",
        "resolution_sector": "",
        "resolution_elec": "",
        "solver": "",
        "solver_options": "",
        "threads": "",
    }

    config_file = run_dir / "configs" / f"config.{stem}.yaml"
    if not config_file.exists():
        # perfect foresight and renamed wildcards do not map 1:1 onto a
        # snapshot name, but the metadata below does not vary within a run
        snapshots = sorted((run_dir / "configs").glob("config.*.yaml"))
        if not snapshots:
            logger.warning(f"Found no config snapshot for {run_dir / stem}.")
            return empty
        config_file = snapshots[0]
        logger.info(f"Using config snapshot {config_file.name} for '{stem}'.")

    config = read_config_sections(config_file, METADATA_SECTIONS)

    clustering = config.get("clustering", {})
    temporal = clustering.get("temporal", {})
    solving = config.get("solving", {})
    solver = solving.get("solver", {})
    option_set = solver.get("options", "")
    solver_options = solving.get("solver_options", {}).get(option_set, {})

    return {
        "at_admin_level": clustering.get("administrative", {}).get("AT", ""),
        "resolution_sector": temporal.get("resolution_sector", ""),
        "resolution_elec": temporal.get("resolution_elec", ""),
        "solver": solver.get("name", ""),
        "solver_options": option_set,
        "threads": solver_options.get("threads") or solver_options.get("Threads") or "",
    }


def build_record(path: Path) -> dict:
    """
    Build a single history row from one benchmark file.

    The run start time is derived from the modification time of the
    benchmark file, which Snakemake writes when the job finishes, minus
    the measured wall clock time of the job.

    Parameters
    ----------
    path
        Path to the benchmark file.

    Returns
    -------
    :
        Mapping of :data:`COLUMNS` to values.
    """
    benchmark = parse_benchmark(path)
    run_dir = path.parent.parent.parent
    stem = path.name

    seconds = benchmark["s"]
    end_time = datetime.fromtimestamp(path.stat().st_mtime)
    start_time = end_time - timedelta(seconds=seconds)

    metadata = read_run_metadata(run_dir, stem)
    threads = metadata["threads"]
    hours = seconds / 3600

    record = {
        "prefix": run_dir.parent.name,
        "run_name": run_dir.name,
        "rule": path.parent.name,
        "start_time": start_time.isoformat(timespec="seconds"),
        "end_time": end_time.isoformat(timespec="seconds"),
        "solve_time_s": round(seconds, 2),
        "solve_time_h": round(hours, 3),
        "core_hours_allocated": round(threads * hours, 2) if threads else "",
        "cpu_time_s": round(benchmark["cpu_time"], 2),
        "core_hours_cpu": round(benchmark["cpu_time"] / 3600, 2),
        "mean_load": round(benchmark["mean_load"], 2),
        "max_rss_gb": round(benchmark["max_rss"] / 1024, 2),
        "max_pss_gb": round(benchmark["max_pss"] / 1024, 2),
        "max_vms_gb": round(benchmark["max_vms"] / 1024, 2),
        "benchmark_file": str(path),
    }
    record |= parse_wildcards(stem)
    record |= metadata

    return {column: record[column] for column in COLUMNS}


def collect(paths: list[Path]) -> pd.DataFrame:
    """
    Collect all solve benchmarks below the given paths.

    Parameters
    ----------
    paths
        Files or directories to search, see :func:`find_benchmark_files`.

    Returns
    -------
    :
        One row per solve job, with :data:`COLUMNS` as columns.
    """
    files = find_benchmark_files(paths)
    logger.info(f"Found {len(files)} solve benchmark files.")

    records = [build_record(file) for file in files]

    return pd.DataFrame(records, columns=COLUMNS)


def _keys(df: pd.DataFrame) -> pd.Series:
    """Build a comparable identity string per row from :data:`KEY_COLUMNS`."""
    # empty wildcards are written as empty fields, so they must compare
    # equal to the empty strings of freshly collected rows
    return df[KEY_COLUMNS].fillna("").astype(str).agg("|".join, axis=1)


def drop_known_records(df: pd.DataFrame, output: Path) -> pd.DataFrame:
    """
    Drop rows that are already part of the history file.

    Parameters
    ----------
    df
        Newly collected benchmark rows.
    output
        The history CSV file. A missing file leaves ``df`` unchanged,
        apart from duplicates within ``df`` itself.

    Returns
    -------
    :
        The rows of ``df`` that are not in the history file yet.

    Raises
    ------
    ValueError
        If the existing history file has different columns, which would
        misalign appended rows.
    """
    df = df.drop_duplicates(subset=KEY_COLUMNS)
    if not output.exists():
        return df

    history = pd.read_csv(output, dtype=str, keep_default_na=False)
    if list(history.columns) != COLUMNS:
        raise ValueError(
            f"Existing history file {output} has columns {list(history.columns)}, "
            f"expected {COLUMNS}. Append to a different file with --output instead."
        )

    return df[~_keys(df).isin(_keys(history))]


def append_csv(df: pd.DataFrame, output: Path) -> None:
    """
    Append rows to the history file, creating it if necessary.

    Existing rows are never rewritten: the new rows are appended at the
    bottom of the file, and the header is only written for a new file.

    Parameters
    ----------
    df
        Rows to append.
    output
        The history CSV file.
    """
    is_new = not output.exists()
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output, mode="a", header=is_new, index=False)


@click.command()
@click.argument(
    "paths",
    type=click.Path(path_type=Path, exists=True),
    nargs=-1,
    required=True,
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=DEFAULT_OUTPUT,
    show_default=True,
    help="CSV file new benchmark rows are appended to.",
)
@click.option(
    "--allow-duplicates",
    is_flag=True,
    default=False,
    help="Append all collected rows, including ones already in the file.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the collected rows without writing the CSV file.",
)
def main(
    paths: tuple[Path, ...],
    output: Path,
    allow_duplicates: bool,
    dry_run: bool,
) -> None:
    """
    Collect solving benchmarks below PATHS into a CSV history file.

    PATHS are results directories, run directories or single benchmark
    files. Directories are searched recursively for benchmark files of
    solve rules.
    """
    df = collect(list(paths))
    if df.empty:
        logger.warning("Found no solve benchmark files, nothing to do.")
        return

    if not allow_duplicates:
        df = drop_known_records(df, output)
        if df.empty:
            logger.info(f"All collected benchmarks are already in {output}.")
            return

    if dry_run:
        logger.info(f"Would append {len(df)} rows to {output}:")
        print(df.to_string(index=False))
        return

    append_csv(df, output)
    logger.info(f"Appended {len(df)} rows to {output}.")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="{levelname} - {name} - {message}",
        datefmt="%Y-%m-%d %H:%M",
        style="{",
    )
    main()
