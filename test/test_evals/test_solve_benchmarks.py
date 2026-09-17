# SPDX-FileCopyrightText: 2023-2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Tests for the solving benchmark collector."""

import os
from datetime import datetime

import pandas as pd
import pytest

from evals.solve_benchmarks import (
    COLUMNS,
    append_csv,
    build_record,
    collect,
    drop_known_records,
    find_benchmark_files,
    parse_benchmark,
    parse_wildcards,
    read_config_sections,
    read_run_metadata,
)

BENCHMARK_CONTENT = (
    "s\th:m:s\tmax_rss\tmax_vms\tmax_uss\tmax_pss\tio_in\tio_out\tmean_load\tcpu_time\n"
    "1050.57\t0:17:30\t22591.91\t51743.37\t22095.82\t22291.71\t0.20\t0.00\t2647.63\t27816.56\n"
)

CONFIG_CONTENT = """\
clustering:
  administrative:
    level: 0
    AT: 2
  temporal:
    resolution_sector: 24H
    resolution_elec: false
solving:
  solver:
    name: gurobi
    options: gurobi-default
  solver_options:
    gurobi-default:
      threads: 32
  # config snapshots written by snakemake contain python specific tags
  tagged: !!python/tuple [1, 2]
"""

END_TIME = datetime(2026, 9, 16, 0, 32, 41)


@pytest.fixture
def run_dir(tmp_path):
    """A solved run directory with one solve benchmark and its config."""
    stem = "base_s_adm__none_2025"
    run = tmp_path / "industrial-demand-sensitivities" / "Industry_1.0"

    benchmark = run / "benchmarks" / "solve_sector_network" / stem
    benchmark.parent.mkdir(parents=True)
    benchmark.write_text(BENCHMARK_CONTENT)
    os.utime(benchmark, (END_TIME.timestamp(), END_TIME.timestamp()))

    config = run / "configs" / f"config.{stem}.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(CONFIG_CONTENT)

    return run


def test_parse_benchmark_reads_single_row(run_dir):
    result = parse_benchmark(
        run_dir / "benchmarks" / "solve_sector_network" / "base_s_adm__none_2025"
    )

    assert result["s"] == 1050.57
    assert result["max_rss"] == 22591.91
    assert result["cpu_time"] == 27816.56


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        (
            "base_s_adm__none_2025",
            {
                "clusters": "adm",
                "opts": "",
                "sector_opts": "none",
                "planning_horizon": "2025",
            },
        ),
        (
            "base_s_10m_1H-T_none_2040",
            {
                "clusters": "10m",
                "opts": "1H-T",
                "sector_opts": "none",
                "planning_horizon": "2040",
            },
        ),
        (
            "base_s_adm__none_brownfield_all_years",
            {
                "clusters": "adm",
                "opts": "",
                "sector_opts": "none",
                "planning_horizon": "",
            },
        ),
        (
            "base_s_adm_elec_",
            {
                "clusters": "adm",
                "opts": "",
                "sector_opts": "",
                "planning_horizon": "",
            },
        ),
    ],
)
def test_parse_wildcards(stem, expected):
    assert parse_wildcards(stem) == expected


def test_parse_wildcards_returns_empty_wildcards_for_unknown_name(caplog):
    result = parse_wildcards("some_other_file")

    assert set(result.values()) == {""}
    assert "Could not parse wildcards" in caplog.text


def test_read_config_sections_stops_after_the_last_section(tmp_path):
    """Huge sections behind the wanted ones are never parsed."""
    config = tmp_path / "config.yaml"
    config.write_text(
        "clustering:\n  temporal:\n    resolution_sector: 24H\nrest: !!python/object/apply:os.system ['echo unparsed']\n"
    )

    result = read_config_sections(config, ("clustering",))

    assert result == {"clustering": {"temporal": {"resolution_sector": "24H"}}}


def test_read_config_sections_skips_missing_sections(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("clustering:\n  temporal: {}\n")

    assert read_config_sections(config, ("clustering", "solving")) == {
        "clustering": {"temporal": {}}
    }


def test_read_run_metadata(run_dir):
    result = read_run_metadata(run_dir, "base_s_adm__none_2025")

    assert result == {
        "at_admin_level": 2,
        "resolution_sector": "24H",
        "resolution_elec": False,
        "solver": "gurobi",
        "solver_options": "gurobi-default",
        "threads": 32,
    }


def test_read_run_metadata_falls_back_to_other_snapshot(run_dir):
    """Wildcards without a matching snapshot reuse another one of the run."""
    result = read_run_metadata(run_dir, "base_s_adm__none_brownfield_all_years")

    assert result["resolution_sector"] == "24H"


def test_read_run_metadata_without_snapshot_is_empty(tmp_path, caplog):
    result = read_run_metadata(tmp_path, "base_s_adm__none_2025")

    assert set(result.values()) == {""}
    assert "Found no config snapshot" in caplog.text


def test_find_benchmark_files_ignores_other_rules(run_dir):
    other = run_dir / "benchmarks" / "prepare_sector_network" / "base_s_adm__none_2025"
    other.parent.mkdir(parents=True)
    other.write_text(BENCHMARK_CONTENT)
    log = run_dir / "logs" / "base_s_adm__none_2025_solver.log"
    log.parent.mkdir(parents=True)
    log.write_text("")

    result = find_benchmark_files([run_dir.parent.parent])

    assert result == [
        run_dir / "benchmarks" / "solve_sector_network" / "base_s_adm__none_2025"
    ]


def test_find_benchmark_files_accepts_single_file(run_dir):
    benchmark = (
        run_dir / "benchmarks" / "solve_sector_network" / "base_s_adm__none_2025"
    )

    assert find_benchmark_files([benchmark]) == [benchmark]


class TestBuildRecord:
    """The history row built from a single benchmark file."""

    @pytest.fixture
    def record(self, run_dir):
        return build_record(
            run_dir / "benchmarks" / "solve_sector_network" / "base_s_adm__none_2025"
        )

    def test_columns_match_the_history_file(self, record):
        assert list(record) == COLUMNS

    def test_identity_comes_from_the_path(self, record):
        assert record["prefix"] == "industrial-demand-sensitivities"
        assert record["run_name"] == "Industry_1.0"
        assert record["rule"] == "solve_sector_network"
        assert record["planning_horizon"] == "2025"

    def test_start_time_is_end_time_minus_runtime(self, record):
        assert record["end_time"] == "2026-09-16T00:32:41"
        assert record["start_time"] == "2026-09-16T00:15:10"

    def test_core_hours(self, record):
        assert record["solve_time_h"] == 0.292
        assert record["core_hours_allocated"] == 9.34  # 32 threads * 0.2918 h
        assert record["core_hours_cpu"] == 7.73  # 27816.56 s / 3600

    def test_memory_is_converted_to_gigabytes(self, record):
        assert record["max_rss_gb"] == 22.06

    def test_metadata_comes_from_the_config_snapshot(self, record):
        assert record["at_admin_level"] == 2
        assert record["resolution_sector"] == "24H"
        assert record["solver"] == "gurobi"


def test_collect_returns_empty_frame_without_benchmarks(tmp_path):
    result = collect([tmp_path])

    assert result.empty
    assert list(result.columns) == COLUMNS


class TestHistoryFile:
    """Appending to the CSV history file never rewrites existing rows."""

    @pytest.fixture
    def df(self, run_dir):
        return collect([run_dir])

    @pytest.fixture
    def output(self, tmp_path):
        return tmp_path / "solve_benchmarks.csv"

    def test_new_file_is_written_with_header(self, df, output):
        append_csv(df, output)

        assert list(pd.read_csv(output).columns) == COLUMNS
        assert len(pd.read_csv(output)) == 1

    def test_rows_are_appended_at_the_bottom(self, df, output):
        append_csv(df, output)
        other = df.assign(run_name="Industry_1.1")
        append_csv(other, output)

        history = pd.read_csv(output)
        assert history["run_name"].tolist() == ["Industry_1.0", "Industry_1.1"]

    def test_known_records_are_dropped(self, df, output):
        """Empty wildcards round trip through the CSV file as empty fields."""
        append_csv(df, output)

        assert df["opts"].tolist() == [""]
        assert drop_known_records(df, output).empty

    def test_new_records_are_kept(self, df, output):
        append_csv(df, output)
        other = df.assign(run_name="Industry_1.1")

        result = drop_known_records(other, output)

        assert result["run_name"].tolist() == ["Industry_1.1"]

    def test_duplicates_within_one_batch_are_dropped(self, df, output):
        result = drop_known_records(pd.concat([df, df]), output)

        assert len(result) == 1

    def test_foreign_file_raises(self, df, output):
        output.write_text("foo,bar\n1,2\n")

        with pytest.raises(ValueError, match="expected"):
            drop_known_records(df, output)
