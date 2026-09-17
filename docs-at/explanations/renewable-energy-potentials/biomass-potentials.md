# Biomass Potentials and Availability Scaling

PyPSA-AT derives its biomass supply from the JRC ENSPRESO dataset and Eurostat energy
balances, exactly as PyPSA-Eur does. On top of that, PyPSA-AT can scale the resulting
regional potentials by a configurable factor, so that sensitivity campaigns can probe how
the system reacts when more or less biomass is available.

## Where the potentials come from

`rule build_biomass_potentials` reads ENSPRESO, groups its commodities into the classes
defined under `biomass.classes`, adds the unsustainable potentials derived from Eurostat,
and maps everything onto the model regions. It writes

```
resources/{prefix}/{scenario}/biomass_potentials_s_{clusters}_{planning_horizons}.csv
```

with one row per model region (`AT111`, `DE1`, `AL`, ...) and one column per carrier, in
MWh/a. This single file is the source of truth for biomass availability: `add_biomass()`
in `prepare_sector_network.py` turns it into non-extendable biomass generators, one per
region and carrier.

## Sustainable and unsustainable potentials

ENSPRESO potentials are phased in over time while today's unsustainable use is phased
out. Both shares live in `config/config.default.yaml`:

- `biomass.share_sustainable_potential_available` — 0 in 2025, 1 from 2040 on
- `biomass.share_unsustainable_use_retained` — 1 in 2025, 0 from 2040 on

The two groups behave very differently in the optimisation:

| Carrier | `e_sum_min` | `e_sum_max` | Character |
|---|---|---|---|
| `solid biomass` | 0 | potential | upper bound |
| `biogas` | 0 | potential | upper bound |
| `municipal solid waste` | potential | potential | **must-run** |
| `unsustainable solid biomass` | potential | potential | **must-run** |
| `unsustainable biogas` | potential | potential | **must-run** |
| `unsustainable bioliquids` | potential | potential | **must-run** |

For the upper-bound carriers the potential is an offer the optimiser may decline. For the
must-run carriers `e_sum_min == e_sum_max`, so the potential is consumed in full.

This matters because the split shifts strongly over the planning horizons. Austrian
totals in TWh/a at `AT35DE5` clustering:

| Horizon | solid biomass | biogas | unsust. solid | unsust. biogas | unsust. bioliquids |
|---|---|---|---|---|---|
| 2025 | 0.00 | 0.00 | 54.28 | 2.50 | 4.62 |
| 2030 | 11.83 | 0.45 | 35.82 | 1.65 | 3.05 |
| 2040 | 34.32 | 1.37 | 0.00 | 0.00 | 0.00 |
| 2050 | 35.38 | 1.37 | 0.00 | 0.00 | 0.00 |

Austrian municipal solid waste is zero in every horizon.

## Scaling the potentials

The AT rule `scale_biomass_potentials_at` sits between `build_biomass_potentials` and
`prepare_sector_network`:

```
build_biomass_potentials_at  →  biomass_potentials_s_{clusters}_{ph}_raw.csv
scale_biomass_potentials_at  →  biomass_potentials_s_{clusters}_{ph}.csv
```

Scaling the table rather than the network keeps generator `p_nom`, `e_sum_min` and
`e_sum_max` consistent across every planning horizon of the myopic loop without touching
PyPSA component internals. The business logic lives in `mods/potentials/biomass.py`.

Configuration, under `mods:` in `config/config.at.yaml`:

```yaml
  biomass_potential_scaling:
    enable: true
    carriers:
    - solid biomass
    - biogas
    - unsustainable solid biomass
    - unsustainable biogas
    - unsustainable bioliquids
    factors:
      default: 1.0
```

- `factors` maps a two-letter country code to a multiplier. A region belongs to the
  country given by the first two characters of its name, so the same factors work in
  every clustering mode.
- `factors.default` applies to every country without its own entry. To scale Austria
  alone, keep `default: 1.0` and add `AT: 0.5`.
- `carriers` selects which columns are scaled. `municipal solid waste` is deliberately
  excluded from the default list: it is mixed waste with a non-biogenic fraction,
  incinerated as waste-to-energy rather than sourced as a biomass feedstock, so it is a
  weak fit for a *biomass availability* sensitivity even though it shares this file and
  is added the same way in `add_biomass`. `not included` is never read by `add_biomass`
  and is never scaled either way.
- A factor of `1.0` everywhere, or `enable: false`, passes the table through unchanged.

!!! warning "Scaling up must-run carriers"
    Raising the factor above 1.0 while the must-run carriers are in the `carriers` list
    increases *forced* biomass combustion in 2025 and 2030, not just availability. At
    factor 4.0 the 2025 Austrian potential of 61 TWh/a becomes 245 TWh/a that the model
    has to burn. The mod logs a warning whenever this combination is active. Restricting
    `carriers` to `solid biomass` and `biogas` gives clean upper-bound semantics, at the
    price of a weak signal in 2030 and none at all in 2025.

## Running a sensitivity campaign

`config/config.sensitivities-biomass.yaml` together with
`config/scenarios-sensitivities/scenarios.biomass-sensitivities.yaml` defines 15 runs
from factor 0.5 to 4.0 in steps of 0.25, with `BIO_100` as the unscaled reference:

```bash
pixi run snakemake --configfile config/config.sensitivities-biomass.yaml -call
pixi run evals "results/biomass-sensitivities/BIO_050"
```

The campaign config runs at AT10 clustering (`mods.modify_nuts3_shapes: AT10DE5` with
`clustering.administrative.AT: 2`) and a very low temporal resolution of `365H`, i.e. 24
snapshots per year, because the runs are compared against each other rather than read as
absolute results.

Two interactions to keep in mind when interpreting the results:

- **Biomass crosses borders.** `sector.biomass_transport: true` adds
  `solid biomass transport` links between regions, so an Austria-only reduction is partly
  offset by imports from neighbouring countries. Report Austrian biomass imports next to
  the results, or scale all modelled countries via `factors.default`.
- **Minimum production targets can bind.** `solving.constraints.limits_volume_min.biomass`
  sets a floor of 5.6 TWh of Austrian biomass-to-electricity in 2030, counted over the
  carriers `solid biomass`, `biogas` and `renewable gas` only. Strong downscaling can make
  that floor infeasible; relax it in the scenario file if it does.
