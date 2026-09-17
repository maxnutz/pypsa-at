# How-To: Track Solving Times

Estimating how long a run will occupy the SLURM cluster is guesswork without a record of past
runs. [`evals.solve_benchmarks`][evals.solve_benchmarks] builds a record of the resources, past runs took from the SLURM cluster: it reads the benchmark files Snakemake writes for every solve job, adds the resolution and solver settings of
the run, and appends one row per solve job to a CSV file that grows over time.

---

## Collect benchmarks after a run

```shell
pixi run solve-benchmarks results/<your_scenario>
```

The argument is any path below which solve benchmarks are searched — a results root, a single run
directory, or a single benchmark file. Several paths can be passed at once:

```shell
pixi run solve-benchmarks results/v2025.02 results/sysgf
```

By default rows are appended to `solve_benchmarks.csv` in the project root. The file lives in 
the pypsa-at - folder.

| Option | Effect |
|--------|--------|
| `-o`, `--output` | Append to a different CSV file |
| `--dry-run` | Print the collected rows without writing the file |
| `--allow-duplicates` | Append every collected row, including ones already in the file |

Rows already in the file are skipped, so the command can be run after every finished run without
producing duplicates, and an existing file is never rewritten — new rows go to the bottom.

!!! note "Only solve rules"
    Benchmarks of build rules are ignored. A file is collected when it sits in
    `<run>/benchmarks/<rule>/` and its rule name starts with `solve`, which covers
    `solve_network`, `solve_sector_network` and `solve_operations_network`.

## What ends up in the CSV file

| Group | Columns |
|-------|---------|
| Identity | `prefix`, `run_name`, `rule`, `clusters`, `opts`, `sector_opts`, `planning_horizon` |
| Timing | `start_time`, `end_time`, `solve_time_s`, `solve_time_h` |
| Cores | `threads`, `core_hours_allocated`, `cpu_time_s`, `core_hours_cpu`, `mean_load` |
| Memory | `max_rss_gb`, `max_pss_gb`, `max_vms_gb` |
| Resolution | `at_admin_level`, `resolution_sector`, `resolution_elec` |
| Provenance | `solver`, `solver_options`, `benchmark_file` |


- **Two core-hour columns.** `core_hours_allocated` is `threads × solve_time_h`, i.e. what the
  cluster bills for the job. `core_hours_cpu` is the CPU time the solver actually spent. Their
  ratio is the parallel efficiency of the solve — for the AT10 myopic runs it sits around 80 %.
- **Timestamps are derived.** Snakemake does not record when a job started, so `end_time` is the
  modification time of the benchmark file and `start_time` is that time minus the measured wall
  clock time.

The spatial resolution is reported as `at_admin_level`, the NUTS level Austria is clustered to
(`clustering.administrative.AT`): `1` for AT3, `2` for AT10, `3` for AT35. The temporal resolution
is `resolution_sector`, e.g. `24H`.

## Estimate the runtime of a planned run

With a few runs collected, the history answers the usual questions with pandas:

```python
import pandas as pd

df = pd.read_csv("solve_benchmarks.csv")

# median wall clock time per planning horizon at AT10 / 24H
mask = (df.at_admin_level == 2) & (df.resolution_sector == "24H")
df[mask].groupby("planning_horizon").solve_time_h.median()

# core hours a myopic run of all horizons costs, per scenario
df.groupby(["prefix", "run_name"]).core_hours_allocated.sum()
```

Memory matters as much as time when sizing a job: `max_rss_gb` is the peak resident memory of the
solve and should stay below the memory the SLURM job requests.
