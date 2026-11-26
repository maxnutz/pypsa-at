# Historical Industry Production

The industry-history mini-workflow builds the industrial production per country
(kt/a, all model countries) for every historical year from 2014 to 2023. It runs
only the first industry preprocessing steps of PyPSA-AT. Nothing is clustered or
solved.

```bash
pixi run industry-history
```

The task runs `snakemake industry_history_at` with the overlay
`config/config.industry-history.yaml`. The overlay defines one scenario per
year in `config/scenarios.industry-history.yaml`. Each scenario sets
`industry.reference_year`. A run takes about 8 minutes when the input data has
already been retrieved.

## Outputs

One folder per year in `resources/industry-history/IND{year}/`:

| File                                                     | Content                                                                                                                                    |
|----------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `industrial_production_per_country.csv`                  | Historical production from JRC-IDEES. For non-EU countries it is scaled by Eurostat energy balances. Real split between electric arc and blast furnace steel |
| `industrial_production_per_country_tomorrow_{year}-modified.csv` | The same production after `build_industrial_production_per_country_tomorrow` and `modify_industry_production` |

## What the processing steps do

- `build_industrial_production_per_country_tomorrow` uses the year as planning
  horizon. It re-splits steel, aluminium and HVC with the `industry.*_fraction`
  config values. Years before 2020 take the 2020 values. The fractions are
  European averages, so countries with a high share of blast furnace steel
  (e.g. AT, CZ, SK) get **negative** `Electric arc` values. Use
  `industrial_production_per_country.csv` when you need the real historical
  steel route split.
- `modify_industry_production` (PyPSA-DE) replaces Germany's values with
  FORECAST values. FORECAST values exist only for 2020 (hardcoded) and from
  2025 on. For all other years the file passes through unchanged.

## Data limits

- **JRC-IDEES 2023-v1** and the Swiss BFE data end in 2023, so the
  workflow ends there. Eurostat balances reach 2024, but they are not enough
  on their own.
- **Ammonia production** (USGS) covers 2019–2023. Earlier years use the 2019
  values.

To add a year, add a scenario `IND{year}` to
`config/scenarios.industry-history.yaml`.
