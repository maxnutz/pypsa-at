import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium")


@app.cell
def _():
    import sys
    from pathlib import Path

    import marimo as mo
    import numpy as np
    import pandas as pd
    import plotly.express as px

    # cwd is the repo root (`pixi run marimo edit results/biomass-sensitivities/...`),
    # matching the existing .marimo/*.py notebooks, which also use repo-root-relative
    # paths rather than paths relative to the notebook file itself.
    sys.path.insert(0, "results/biomass-sensitivities")
    import biomass_sensitivity_helpers as bsh

    ROOT = Path("results/biomass-sensitivities")
    return ROOT, bsh, mo, np, pd, px


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # Biomass availability sensitivity

    Compares the `results/biomass-sensitivities/BIO_*` runs. Each scenario scales
    every biomass potential column (`biomass_potentials_s_*.csv`) by one factor,
    applied to **all** modelled countries
    (`config/config.sensitivities-biomass.yaml`, `mods.biomass_potential_scaling`).
    `BIO_100` is the unscaled reference (factor 1.0).
    """)
    return


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
        "electrobiofuels": "Transport fuels",  # double counting?!
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
def _(mo, status_pivot):
    mo.ui.table(status_pivot.reset_index(), label="Solve status by scenario and year")
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
def _(mo):
    mo.md("""
    ## Q2 — Which sectors use biomass, and how does that shift with availability?

    Withdrawal from the primary biomass buses (`solid biomass`, `biogas`),
    bucketed by the sector mapping above and summed across all modelled countries.
    `municipal solid waste` is excluded throughout this notebook: it is mixed waste
    incinerated as waste-to-energy, not a biomass feedstock, and is deliberately not
    scaled by `mods.biomass_potential_scaling` either (see `config/config.at.yaml`).
    """)
    return


@app.cell
def _(bsh, metrics_df, px):
    sector_use = metrics_df[metrics_df["metric"] == "sector_use"].copy()
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
        title="Biomass use by sector vs. availability factor",
        labels={"factor": "Biomass availability factor"},
    )
    fig_sector_use
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Q3 — How does the biomass price change with availability?

    Withdrawal-weighted average marginal price at buses of each primary biomass
    carrier (time-weighted per bus with snapshot weightings, then weighted across
    regions by each region's annual withdrawal).

    **Caveat:** forced consumption in 2025 and 2030 by model design!
    """)
    return


@app.cell
def _(bsh, metrics_df, px):
    price = metrics_df[metrics_df["metric"] == "biomass_price"].copy()

    fig_price = px.line(
        price.sort_values("factor"),
        x="factor",
        y="value",
        color="carrier",
        facet_col="year",
        category_orders={"year": bsh.PLANNING_HORIZONS},
        markers=True,
        title="Biomass shadow price vs. availability factor",
        labels={"factor": "Biomass availability factor", "value": "EUR/MWh"},
    )
    fig_price
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Q4 — How much biomass is used, and is there a saturation point?

    Total dispatched biomass (all primary carriers, all modelled countries) per
    year, one line per availability factor.
    """)
    return


@app.cell
def _(bsh, metrics_df, px):
    total_use = metrics_df[metrics_df["metric"] == "total_biomass_use"].copy()
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
        title="Total biomass use per year, one line per availability factor",
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
def _(bsh, px, total_use_by_year):
    fig_total_use_scatter = px.scatter(
        total_use_by_year.sort_values(["year", "factor"]),
        x="factor",
        y="TWh",
        color="year",
        category_orders={"year": bsh.PLANNING_HORIZONS},
        title="Total biomass use vs. availability factor, all years combined",
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

    System-wide supply of every carrier (all modelled countries), correlated
    against the biomass factor per year via a linear slope
    (`ΔTWh / Δfactor`, at least 3 solved factors required). Negative slope =
    crowded out by more biomass; positive slope = grows alongside it.

    **Caveat:** this uses raw `n.statistics.supply()` per carrier, so
    transmission/pipeline-type carriers reflect throughput rather than final
    consumption.
    """)
    return


@app.cell
def _(metrics_df, np, pd):
    carrier_supply = metrics_df[metrics_df["metric"] == "carrier_supply"].copy()
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
def _(bsh, carrier_supply, pd, px, top_negative, top_positive):
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
        title="Top carriers most correlated (+/-) with biomass availability",
    )
    fig_movers
    return


if __name__ == "__main__":
    app.run()
