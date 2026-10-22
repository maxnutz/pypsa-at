# SPDX-FileCopyrightText: 2023-2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Unit tests for mods/network/renewables.py."""

from types import SimpleNamespace

import pandas as pd
import pypsa
import pytest

from mods.network.renewables import (
    redistribute_cohort,
    regional_shares_from_powerplants,
    relocate_existing_onwind,
)


@pytest.fixture
def powerplants_csv(tmp_path):
    """AT onshore wind in AT11 and AT12 (1:3), plus rows that must be ignored."""
    df = pd.DataFrame(
        {
            "Fueltype": ["Wind", "Wind", "Wind", "Hydro", "Wind"],
            "Technology": ["Onshore", "Onshore", "Offshore", "Reservoir", "Onshore"],
            "Country": ["AT", "AT", "AT", "AT", "DE"],
            "Capacity": [100.0, 300.0, 50.0, 500.0, 1000.0],
            "bus": ["AT11", "AT12", "AT11", "AT13", "DE1"],
        }
    )
    path = tmp_path / "powerplants.csv"
    df.to_csv(path)
    return path


@pytest.fixture
def network():
    """Existing onwind spread evenly over three AT regions, plus one DE region."""
    n = pypsa.Network()
    n.set_snapshots(range(2))
    for bus, country in [("AT11", "AT"), ("AT12", "AT"), ("AT13", "AT"), ("DE1", "DE")]:
        n.add("Bus", bus, country=country)
        n.add(
            "Generator",
            f"{bus} 0 onwind-2015",
            bus=bus,
            carrier="onwind",
            p_nom=100.0,
            build_year=2015,
        )
        n.add(
            "Generator",
            f"{bus} 0 onwind-2025",
            bus=bus,
            carrier="onwind",
            p_nom=20.0,
            p_nom_min=20.0,
            p_nom_max=30.0,
            p_nom_extendable=True,
            build_year=2025,
            p_max_pu=[0.2, 0.4],
        )
    n.add("Generator", "AT11 solar-2015", bus="AT11", carrier="solar", p_nom=50.0)
    return n


def make_snakemake(powerplants_csv, enable=True, year=2025):
    return SimpleNamespace(
        params=SimpleNamespace(
            relocate_existing_onwind={"enable": enable, "countries": ["AT"]},
            planning_horizons=[2025, 2030],
        ),
        input=SimpleNamespace(powerplants_at=str(powerplants_csv)),
        wildcards=SimpleNamespace(planning_horizons=str(year)),
    )


def test_shares_only_onshore_wind_of_country(powerplants_csv):
    shares = regional_shares_from_powerplants(
        pd.read_csv(powerplants_csv, index_col=0), "AT"
    )
    expected = pd.Series({"AT11": 0.25, "AT12": 0.75}, name="Capacity")
    pd.testing.assert_series_equal(shares, expected, check_names=False)


def test_shares_raise_without_plants(powerplants_csv):
    with pytest.raises(ValueError, match="No onshore wind plants"):
        regional_shares_from_powerplants(
            pd.read_csv(powerplants_csv, index_col=0), "CH"
        )


def test_redistribute_cohort_preserves_total():
    cohort = pd.Series({"a": 10.0, "b": 30.0, "c": 60.0})
    buses = pd.Series({"a": "X", "b": "Y", "c": "Z"})
    new = redistribute_cohort(cohort, buses, pd.Series({"X": 0.5, "Y": 0.5}))
    assert new.sum() == pytest.approx(100.0)
    assert new.to_dict() == pytest.approx({"a": 50.0, "b": 50.0, "c": 0.0})


def test_redistribute_cohort_splits_within_bus():
    cohort = pd.Series({"a0": 10.0, "a1": 30.0, "b0": 60.0})
    buses = pd.Series({"a0": "X", "a1": "X", "b0": "Y"})
    new = redistribute_cohort(cohort, buses, pd.Series({"X": 1.0}))
    assert new.to_dict() == pytest.approx({"a0": 25.0, "a1": 75.0, "b0": 0.0})


def test_redistribute_cohort_raises_for_missing_bus():
    cohort = pd.Series({"a": 10.0})
    buses = pd.Series({"a": "X"})
    with pytest.raises(ValueError, match="no onwind generator"):
        redistribute_cohort(cohort, buses, pd.Series({"X": 0.5, "Y": 0.5}))


class TestRelocateExistingOnwind:
    def test_adds_missing_cohort_generator(self, network, powerplants_csv):
        network.remove("Generator", "AT12 0 onwind-2015")
        relocate_existing_onwind(network, make_snakemake(powerplants_csv))
        gens = network.generators
        assert gens.at["AT12 0 onwind-2015", "p_nom"] == pytest.approx(150.0)
        assert gens.at["AT12 0 onwind-2015", "build_year"] == 2015
        assert not gens.at["AT12 0 onwind-2015", "p_nom_extendable"]
        assert network.generators_t.p_max_pu["AT12 0 onwind-2015"].tolist() == [
            0.2,
            0.4,
        ]

    def test_relocates_fixed_cohort(self, network, powerplants_csv):
        relocate_existing_onwind(network, make_snakemake(powerplants_csv))
        p_nom = network.generators.p_nom
        assert p_nom["AT11 0 onwind-2015"] == pytest.approx(75.0)
        assert p_nom["AT12 0 onwind-2015"] == pytest.approx(225.0)
        assert p_nom["AT13 0 onwind-2015"] == pytest.approx(0.0)

    def test_relocates_base_year_cohort(self, network, powerplants_csv):
        relocate_existing_onwind(network, make_snakemake(powerplants_csv))
        gens = network.generators
        assert gens.at["AT12 0 onwind-2025", "p_nom_min"] == pytest.approx(45.0)
        assert gens.at["AT12 0 onwind-2025", "p_nom"] == pytest.approx(45.0)
        # p_nom_max raised to the relocated minimum
        assert gens.at["AT12 0 onwind-2025", "p_nom_max"] == pytest.approx(45.0)
        assert gens.at["AT13 0 onwind-2025", "p_nom_min"] == pytest.approx(0.0)
        assert gens.at["AT13 0 onwind-2025", "p_nom_max"] == pytest.approx(30.0)

    def test_other_countries_and_carriers_untouched(self, network, powerplants_csv):
        before = network.generators.copy()
        relocate_existing_onwind(network, make_snakemake(powerplants_csv))
        untouched = ["DE1 0 onwind-2015", "DE1 0 onwind-2025", "AT11 solar-2015"]
        assert network.generators.loc[untouched].compare(before.loc[untouched]).empty

    @pytest.mark.parametrize(
        "kwargs", [{"enable": False}, {"year": 2030}], ids=["disabled", "myopic"]
    )
    def test_noop(self, network, powerplants_csv, kwargs):
        before = network.generators.copy()
        relocate_existing_onwind(network, make_snakemake(powerplants_csv, **kwargs))
        assert network.generators.compare(before).empty
