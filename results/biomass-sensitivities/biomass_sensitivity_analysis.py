import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    import sys

    import marimo as mo
    import numpy as np
    import pandas as pd
    import plotly.express as px

    # cwd is the repo root (`pixi run marimo edit results/biomass-sensitivities/...`),
    # matching the existing .marimo/*.py notebooks, which also use repo-root-relative
    # paths rather than paths relative to the notebook file itself. The helper module
    # lives next to this notebook; the campaign it is pointed at is chosen below.
    sys.path.insert(0, "results/biomass-sensitivities")
    import biomass_sensitivity_helpers as bsh

    return bsh, mo, np, pd, px


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Biomass availability sensitivity

    Compares the `BIO_*` runs of one sensitivity campaign. Each scenario scales every
    biomass potential column (`biomass_potentials_s_*.csv`) by one factor; `*_100` is
    the unscaled reference (factor 1.0). Two campaigns share this notebook:

    | Campaign (`results/` subfolder) | Config | Scaled countries |
    | --- | --- | --- |
    | `biomass-sensitivities` | `config/config.sensitivities-biomass.yaml` | all modelled countries |
    | `biomass-at-sensitivities` | `config/config.sensitivities-biomass-at.yaml` | Austria only |

    Pick the campaign and the region the evaluation covers below — both are echoed in
    every plot subtitle so exported figures stay self-describing.

    ### :warning: Carveats
    - Q2 (withdrawal) under "Austria" includes biomass railed in over solid biomass transport; Q4 (supply) excludes those links, so it's biomass raised from Austrian potential. The gap is the net import.
    - Q5 drops carriers on copper-plated EU buses entirely under "Austria". There is not yet Austria-scaling for EU buses.
    """)
    return


@app.cell(hide_code=True)
def _(bsh, mo):
    _campaigns = bsh.discover_campaigns()
    campaign_selector = mo.ui.dropdown(
        options=_campaigns,
        value="biomass-sensitivities"
        if "biomass-sensitivities" in _campaigns
        else _campaigns[0],
        label="Campaign",
    )
    region_selector = mo.ui.radio(
        options=list(bsh.REGION_SCOPES),
        value="All regions",
        label="Region scope",
        inline=True,
    )
    mo.vstack([campaign_selector, region_selector])
    return campaign_selector, region_selector


@app.cell
def _(bsh, campaign_selector):
    # Absolute, so the notebook keeps working no matter which directory marimo was
    # started from (the helper import above is the only cwd-sensitive line left).
    ROOT = bsh.RESULTS_ROOT / campaign_selector.value
    return (ROOT,)


@app.cell
def _(campaign_selector, region_selector):
    def plot_title(text: str) -> str:
        """Plot title carrying the campaign and region scope as a subtitle."""
        return (
            f"{text}<br><sup>{campaign_selector.value} · {region_selector.value}</sup>"
        )

    return (plot_title,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Sector mapping

    End-use carriers touching a biomass bus, bucketed into a sector for the chart
    in Q2 — edit freely; anything not listed here shows up under
    `"(unmapped)"` instead of being silently dropped, so gaps are visible.
    Inter-regional `*_transport` links are excluded upstream (they move biomass
    between regions rather than consuming it) and never reach this mapping.
    """)
    return


@app.cell
def _():
    SECTOR_MAP = {
        "rural biomass boiler": "Heat",
        "urban decentral biomass boiler": "Heat",
        "urban central solid biomass CHP": "CHP (Electricity & Heat)",
        "urban central solid biomass CHP CC": "CHP (Electricity & Heat)",
        # "waste CHP": "CHP (Electricity & Heat)",
        # "waste CHP CC": "CHP (Electricity & Heat)",
        "solid biomass for industry": "Industry",
        "solid biomass for industry CC": "Industry",
        "biogas to gas": "Gas grid",  # double counting?!
        "biogas to gas CC": "Gas grid",  # double counting?!
        "BioSNG": "Gas grid",
        "BioSNG CC": "Gas grid",
        "biomass to liquid": "Fuels",
        "biomass to liquid CC": "Fuels",
        "electrobiofuels": "Fuels",  # double counting?!
        "biomass-to-methanol": "Methanol",
        "biomass-to-methanol CC": "Methanol",
        "solid biomass to hydrogen": "Hydrogen",
    }
    return (SECTOR_MAP,)


@app.cell(hide_code=True)
def _(mo):
    force_recompute = mo.ui.checkbox(
        label="Force recompute (ignore the statistics cache)"
    )
    force_recompute
    return (force_recompute,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ---
    lets start with the best part
    ## Q1 — Which years failed to solve?
    """)
    return


@app.cell
def _(ROOT, bsh):
    status = bsh.all_solve_status(ROOT)
    status_pivot = status.pivot(index="scenario", columns="year", values="status")
    return status, status_pivot


@app.cell
def _(campaign_selector, mo, status_pivot):
    mo.ui.table(
        status_pivot.reset_index(),
        label=f"Solve status by scenario and year ({campaign_selector.value})",
    )
    return


@app.cell
def _(status):
    not_solved = status[status["status"] != "optimal"].sort_values(["scenario", "year"])
    return (not_solved,)


@app.cell
def _(mo, not_solved):
    mo.vstack(
        [
            mo.md(
                f"**{len(not_solved)}** scenario/year combinations are not solved "
                "(either genuinely infeasible, or not yet attempted by the background "
                "campaign):"
            ),
            mo.ui.table(
                not_solved[
                    ["scenario", "factor", "year", "status", "termination_condition"]
                ]
            ),
        ]
    )
    return


@app.cell
def _(ROOT, SECTOR_MAP, bsh, force_recompute):
    metrics_df = bsh.load_all_metrics(ROOT, SECTOR_MAP, force_recompute.value)
    return (metrics_df,)


@app.cell(hide_code=True)
def _(bsh, metrics_df, region_selector):
    # Every metric is extracted per `location` (AT12, DE1, FR, ...), so switching the
    # region scope is a row filter on the cached frame - no network is re-read.
    metrics_scoped = bsh.select_region(metrics_df, region_selector.value)
    return (metrics_scoped,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Q2 — Which sectors use biomass, and how does that shift with availability?

    Withdrawal from the primary biomass buses (`solid biomass`, `biogas`),
    bucketed by the sector mapping above and summed across the regions in scope.
    `municipal solid waste` is excluded throughout this notebook: it is mixed waste
    incinerated as waste-to-energy, not a biomass feedstock, and is deliberately not
    scaled by `mods.biomass_potential_scaling` either (see `config/config.at.yaml`).

    **Under the "Austria" scope** this is biomass *consumed* in Austria, which
    includes biomass railed in from abroad over `solid biomass transport` links.
    Q4 below counts biomass *supplied* at Austrian buses instead, i.e. what Austrian
    potential actually yields — so the two need not match, and the gap is the net
    import.
    """)
    return


@app.cell
def _(bsh, metrics_scoped, plot_title, px):
    sector_use = metrics_scoped[metrics_scoped["metric"] == "sector_use"].copy()
    sector_use["TWh"] = sector_use["value"] / 1e6
    sector_totals = sector_use.groupby(["factor", "year", "sector"], as_index=False)[
        "TWh"
    ].sum()

    fig_sector_use = px.bar(
        sector_totals.sort_values("factor"),
        x="factor",
        y="TWh",
        color="sector",
        facet_col="year",
        category_orders={"year": bsh.PLANNING_HORIZONS},
        barmode="stack",
        title=plot_title("Biomass use by sector vs. availability factor"),
        labels={"factor": "Biomass availability factor"},
    )
    fig_sector_use
    return (sector_totals,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Per-sector response

    Same data as the stacked bars above, but one sector at a time: availability
    factor on the x-axis, that sector's biomass withdrawal on the y-axis, one line
    per planning horizon. Reading a single sector on its own y-scale makes it
    visible whether it keeps absorbing extra biomass as availability grows or
    saturates early — which the stacked view hides for the smaller sectors.
    """)
    return


@app.cell(hide_code=True)
def _(mo, sector_totals):
    _sector_options = sorted(sector_totals["sector"].unique())
    _default = (
        "Heat" if "Heat" in _sector_options else next(iter(_sector_options), None)
    )
    sector_selector = mo.ui.dropdown(
        options=_sector_options,
        value=_default,
        label="Sector",
    )
    sector_selector
    return (sector_selector,)


@app.cell
def _(bsh, plot_title, px, sector_selector, sector_totals):
    selected_sector = sector_totals[
        sector_totals["sector"] == sector_selector.value
    ].sort_values(["year", "factor"])

    fig_sector_detail = px.line(
        selected_sector,
        x="factor",
        y="TWh",
        color="year",
        category_orders={"year": bsh.PLANNING_HORIZONS},
        markers=True,
        title=plot_title(
            f"{sector_selector.value}: biomass use vs. availability factor"
        ),
        labels={"factor": "Biomass availability factor", "year": "Planning horizon"},
    )
    fig_sector_detail
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Q3 — How does the biomass price change with availability?

    Withdrawal-weighted average marginal price at buses of each primary biomass
    carrier (time-weighted per bus with snapshot weightings, then weighted across
    the regions in scope by each region's annual withdrawal).

    **Caveat:** forced consumption in 2025 and 2030 by model design!
    """)
    return


@app.cell
def _(bsh, metrics_scoped, plot_title, px):
    price = bsh.weighted_price(
        metrics_scoped[metrics_scoped["metric"] == "biomass_price"],
        ["factor", "year", "carrier"],
    )

    fig_price = px.line(
        price.sort_values("factor"),
        x="factor",
        y="value",
        color="carrier",
        facet_col="year",
        category_orders={"year": bsh.PLANNING_HORIZONS},
        markers=True,
        title=plot_title("Biomass shadow price vs. availability factor"),
        labels={"factor": "Biomass availability factor", "value": "EUR/MWh"},
    )
    fig_price
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Q4 — How much biomass is used, and is there a saturation point?

    Total dispatched biomass (all primary carriers, all regions in scope) per
    year, one line per availability factor. Inter-regional `solid biomass transport`
    links are excluded, so under the "Austria" scope this is biomass *supplied* from
    Austrian potential rather than biomass consumed in Austria (see Q2).
    """)
    return


@app.cell
def _(bsh, metrics_scoped, plot_title, px):
    total_use = metrics_scoped[metrics_scoped["metric"] == "total_biomass_use"].copy()
    total_use_by_year = total_use.groupby(["factor", "year"], as_index=False)[
        "value"
    ].sum()
    total_use_by_year["TWh"] = total_use_by_year["value"] / 1e6
    # discrete, factor-ordered legend (one line per factor) rather than a continuous
    # colorbar, since there are only ~15 distinct factors and each is a meaningful
    # scenario, not a smooth continuum
    factor_order = sorted(total_use_by_year["factor"].unique())
    total_use_by_year["factor_label"] = total_use_by_year["factor"].map(
        lambda f: f"{f:.2f}"
    )

    fig_total_use = px.line(
        total_use_by_year.sort_values(["year", "factor"]),
        x="year",
        y="TWh",
        color="factor_label",
        category_orders={
            "factor_label": [f"{f:.2f}" for f in factor_order],
            "year": bsh.PLANNING_HORIZONS,
        },
        markers=True,
        title=plot_title(
            "Total biomass use per year, one line per availability factor"
        ),
        labels={"factor_label": "Biomass availability factor"},
    )
    fig_total_use
    return (total_use_by_year,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### All years combined

    Same data as above, replotted with availability factor on the x-axis and every
    year on one shared scale, colored by year. This makes it easier to compare the
    *shape* of the response across years directly (e.g. whether later years flatten
    out sooner than earlier ones).
    """)
    return


@app.cell
def _(bsh, plot_title, px, total_use_by_year):
    fig_total_use_scatter = px.scatter(
        total_use_by_year.sort_values(["year", "factor"]),
        x="factor",
        y="TWh",
        color="year",
        category_orders={"year": bsh.PLANNING_HORIZONS},
        title=plot_title(
            "Total biomass use vs. availability factor, all years combined"
        ),
        labels={"factor": "Biomass availability factor"},
    )
    fig_total_use_scatter
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ### Marginal response

    measures how much of every *additional* unit of biomass availability
    actually gets burned, rather than sitting unused because the system has no more
    economic use for it.

    `marginal_TWh_per_factor` is the slope between two adjacent solved factors of availability
    for the same year: `(TWh at factor B − TWh at factor A) / (factor B − factor A)` for `A < B`


    > **How to read it:** For example, if usage is 100 TWh at factor 1.0 and 110 TWh at factor 1.25, the
    marginal response between those two points is `(110 − 100) / (1.25 − 1.0) = 40`
    TWh per unit factor. at low factors, biomass is scarce and effectively all of the
    available potential gets dispatched, so the marginal response tracks close to
    the added potential itself. As the factor keeps rising, once biomass has already
    displaced the cheapest alternatives in every sector from Q2, further
    availability stops being worth dispatching — it's cheaper to leave it unused
    than to substitute away from an already-cheap alternative. The marginal response
    then drops toward (or to) zero — the smallest factor from which point on adding
    more availability no longer increases how much biomass is actually used in that
    year is the saturation point.
    """)
    return


@app.cell
def _(total_use_by_year):
    marginal_response = total_use_by_year.sort_values(["year", "factor"]).copy()
    marginal_response["marginal_TWh_per_factor"] = (
        marginal_response.groupby("year")["TWh"].diff()
        / marginal_response.groupby("year")["factor"].diff()
    )
    marginal_response
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Q5 — Which other carriers are used more or less as biomass availability changes?

    Supply of every carrier across the regions in scope, correlated
    against the biomass factor per year via a linear slope
    (`ΔTWh / Δfactor`, at least 3 solved factors required). Negative slope =
    crowded out by more biomass; positive slope = grows alongside it.

    **Caveats:** this uses raw `n.statistics.supply()` per carrier, so
    transmission/pipeline-type carriers reflect throughput rather than final
    consumption. Under the "Austria" scope, carriers that sit on copper-plated `EU`
    buses (e.g. oil, some shipping fuels) have no Austrian location and therefore
    drop out of the table entirely rather than showing up with a zero slope.
    """)
    return


@app.cell
def _(metrics_scoped, np, pd):
    carrier_supply = (
        metrics_scoped[metrics_scoped["metric"] == "carrier_supply"]
        .groupby(["factor", "year", "carrier", "bus_carrier"], as_index=False)["value"]
        .sum()
    )
    carrier_supply["TWh"] = carrier_supply["value"] / 1e6

    def _slope(group):
        if group["factor"].nunique() < 3:
            return np.nan
        return np.polyfit(group["factor"], group["TWh"], 1)[0]

    slopes = (
        carrier_supply.groupby(["year", "carrier", "bus_carrier"])
        .apply(
            lambda g: pd.Series(
                {
                    "slope_TWh_per_factor": _slope(g),
                    "n_factors": g["factor"].nunique(),
                }
            ),
            include_groups=False,
        )
        .reset_index()
        .dropna(subset=["slope_TWh_per_factor"])
    )
    return carrier_supply, slopes


@app.cell
def _(mo, slopes):
    top_negative = slopes.sort_values("slope_TWh_per_factor").head(10)
    top_positive = slopes.sort_values("slope_TWh_per_factor", ascending=False).head(10)

    mo.vstack(
        [
            mo.md("**Crowded out most as biomass availability increases:**"),
            mo.ui.table(top_negative),
            mo.md("**Grow most alongside biomass availability (complements):**"),
            mo.ui.table(top_positive),
        ]
    )
    return top_negative, top_positive


@app.cell
def _(bsh, carrier_supply, pd, plot_title, px, top_negative, top_positive):
    movers = pd.concat([top_negative.head(5), top_positive.head(5)])
    mover_keys = set(zip(movers["carrier"], movers["bus_carrier"], strict=True))
    mover_data = carrier_supply[
        carrier_supply.apply(
            lambda row: (row["carrier"], row["bus_carrier"]) in mover_keys, axis=1
        )
    ]

    fig_movers = px.line(
        mover_data.sort_values("factor"),
        x="factor",
        y="TWh",
        color="carrier",
        facet_col="year",
        category_orders={"year": bsh.PLANNING_HORIZONS},
        markers=True,
        title=plot_title(
            "Top carriers most correlated (+/-) with biomass availability"
        ),
    )
    fig_movers
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
