# Wind Build-out Sensitivities

The wind build-out sensitivities cap total installed Austrian onshore wind
capacity at a multiple of today's level. They show how system costs, the
Austrian technology mix and imports respond to restricted wind expansion.

## Scenarios

The scenarios live in
`config/scenarios-sensitivities/scenarios.wind-sensitivities.yaml`:

| Scenario | AT onwind cap (2030, 2040, 2050) |
|---|---|
| `WIND_REF_*` | none |
| `WIND_100_*` | 4.0 GW (1.0 × 2025 level) |
| `WIND_150_*` | 6.0 GW |
| `WIND_200_*` | 8.0 GW |
| `WIND_250_*` | 10.0 GW |
| `WIND_300_*` | 12.0 GW |

The 2025 reference of 4.0 GW is the AT onwind capacity of the model's 2025
base year. Every scenario comes in two variants:

- `*_aut` keeps the national yearly net electricity autarky
  (`mods.net_zero_electricity`). Missing wind must be replaced by domestic
  generation or imported fuels.
- `*_open` disables it, so electricity imports can replace domestic wind.

The cap uses the PyPSA-DE capacity limit on existing plus newly built capacity:

```yaml
solving:
  constraints:
    limits_capacity_max:
      Generator:
        onwind:
          AT:
            2030: 6.0  # GW
```

All scenarios, including `WIND_REF_*`, set the EAG 2030 wind production
target (`limits_volume_min.wind.AT`) to 0, so that only the cap differs between
the reference and the capped runs. See [Production Targets](production-targets.md).

## Running

Run the 365H set:

```bash
pixi run snakemake -call --configfile config/config.sensitivities-wind.yaml
```

Run the 3-hourly set, written to `results/wind-buildout-sensitivities-3h/`:

```bash
pixi run snakemake -call \
  --configfile config/config.sensitivities-wind.yaml config/config.sensitivities-wind-3h.yaml
```

!!! note
    The cap is binding only if the solver would build more onshore wind than
    allowed. The unconstrained baseline builds roughly three times the 2025
    level, so factors above 3.0 are not informative.
