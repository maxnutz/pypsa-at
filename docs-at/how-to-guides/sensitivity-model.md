# Minimal Model for Sensitivities

`config/config.sensitivities.yaml` overlays the default configuration with a
minimal PyPSA-AT model that solves one planning horizon in about a minute.
Sensitivity analyses branch off from it and vary a single input.

```shell
pixi run workflow-sensitivities
```

The overlay keeps every model setting of the default configuration except:

| Setting | Value | Why |
|---|---|---|
| `clustering.administrative.AT` | `2` | 10 Austrian regions (NUTS 2) instead of 35 |
| `mods.modify_nuts3_shapes` | `AT10DE5` | must match the Austrian NUTS level |
| `solving.solver` | `gurobi` | |
| `solving.mem_mb`, `solving.runtime` | `8000`, `1h` | |

The temporal resolution (`clustering.temporal.averaging: 365h`, 24 snapshots
per year) and the planning horizons 2025, 2030, 2040 and 2050 are the defaults.
Results are written to `results/main-sensitivities/`.

## Solving times

Measured on a 32-core machine with Gurobi (16 threads):

| Horizon | Wall clock of `solve_network` | Barrier | Peak memory |
|---|---|---|---|
| 2025 | 51 s | 11 s | 1.4 GB |
| 2030 | 68 s | 13 s | 1.4 GB |
| 2040 | 68 s | 13 s | 1.6 GB |
| 2050 | 56 s | 12 s | 1.4 GB |

Use [`pixi run solve-benchmarks`](solve-benchmarks.md) to collect the solving
times of your own runs.

## Add a sensitivity

1. Create a branch from `main-sensitivities`.
2. Add one entry per sensitivity to a scenarios file and list the names in
   `run.name` of the overlay; point `run.scenarios.file` to that file.

`run.shared_resources.policy` stays `false`, so every scenario builds its own
resources: with `base`, shared files land in the `resources/` root across all
prefixes, and several AT rules combine per-horizon inputs into outputs that
cannot be shared.
