# SPDX-FileCopyrightText: 2026 Austrian Gas Grid Management AG
#
# SPDX-License-Identifier: MIT
# For license information, see the LICENSE.txt file in the project root.
"""Unit tests for mods/potentials/biomass.py."""

import logging

import pandas as pd
import pytest

from mods.potentials.biomass import (
    POTENTIAL_CARRIERS,
    resolve_factors,
    scale_biomass_potentials,
)

ALL_CARRIERS = list(POTENTIAL_CARRIERS)


@pytest.fixture
def potentials() -> pd.DataFrame:
    """Biomass potentials in MWh/a for two AT regions plus DE and CZ."""
    index = pd.Index(["AT111", "AT211", "DE1", "CZ0"], name="MWh/a")
    return pd.DataFrame(
        {
            "biogas": [100.0, 200.0, 300.0, 400.0],
            "municipal solid waste": [0.0, 10.0, 20.0, 30.0],
            "not included": [1000.0, 2000.0, 3000.0, 4000.0],
            "solid biomass": [500.0, 600.0, 700.0, 800.0],
            "unsustainable solid biomass": [50.0, 60.0, 70.0, 80.0],
            "unsustainable biogas": [5.0, 6.0, 7.0, 8.0],
            "unsustainable bioliquids": [1.0, 2.0, 3.0, 4.0],
        },
        index=index,
    )


def params(**overrides) -> dict:
    """Config section with all carriers enabled and a neutral default factor."""
    return {
        "enable": True,
        "carriers": ALL_CARRIERS,
        "factors": {"default": 1.0},
        **overrides,
    }


def test_default_factor_scales_every_country(potentials):
    result = scale_biomass_potentials(potentials, params(factors={"default": 2.0}))

    expected = potentials[ALL_CARRIERS] * 2.0
    assert result[ALL_CARRIERS].compare(expected).empty


def test_country_entry_overrides_default(potentials):
    result = scale_biomass_potentials(
        potentials, params(factors={"default": 1.0, "AT": 0.5})
    )

    assert result.loc["AT111", "solid biomass"] == 250.0
    assert result.loc["AT211", "solid biomass"] == 300.0
    assert result.loc["DE1", "solid biomass"] == 700.0
    assert result.loc["CZ0", "solid biomass"] == 800.0


def test_two_countries_scaled_independently(potentials):
    result = scale_biomass_potentials(
        potentials, params(factors={"default": 1.0, "AT": 4.0, "CZ": 0.5})
    )

    assert result.loc["AT111", "biogas"] == 400.0
    assert result.loc["CZ0", "biogas"] == 200.0
    assert result.loc["DE1", "biogas"] == 300.0


def test_not_included_column_never_scaled(potentials):
    result = scale_biomass_potentials(potentials, params(factors={"default": 3.0}))

    assert result["not included"].compare(potentials["not included"]).empty


def test_carriers_outside_the_list_are_untouched(potentials):
    result = scale_biomass_potentials(
        potentials,
        params(carriers=["solid biomass", "biogas"], factors={"default": 2.0}),
    )

    untouched = [c for c in ALL_CARRIERS if c not in ("solid biomass", "biogas")]
    assert result[untouched].compare(potentials[untouched]).empty
    assert result.loc["AT111", "solid biomass"] == 1000.0


def test_disabled_passes_through(potentials):
    result = scale_biomass_potentials(
        potentials, params(enable=False, factors={"default": 2.0})
    )

    assert result.compare(potentials).empty


def test_missing_enable_key_passes_through(potentials):
    result = scale_biomass_potentials(potentials, {"factors": {"default": 2.0}})

    assert result.compare(potentials).empty


def test_neutral_factors_pass_through(potentials):
    result = scale_biomass_potentials(potentials, params())

    assert result.compare(potentials).empty


def test_empty_carrier_list_passes_through(potentials):
    result = scale_biomass_potentials(
        potentials, params(carriers=[], factors={"default": 2.0})
    )

    assert result.compare(potentials).empty


def test_input_is_not_modified_in_place(potentials):
    original = potentials.copy()

    scale_biomass_potentials(potentials, params(factors={"default": 2.0}))

    assert potentials.compare(original).empty


def test_unknown_country_warns_and_changes_nothing(potentials, caplog):
    with caplog.at_level(logging.WARNING):
        result = scale_biomass_potentials(
            potentials, params(factors={"default": 1.0, "XX": 0.5})
        )

    assert result.compare(potentials).empty
    assert "XX" in caplog.text


def test_unknown_carrier_warns_and_scales_the_rest(potentials, caplog):
    with caplog.at_level(logging.WARNING):
        result = scale_biomass_potentials(
            potentials,
            params(
                carriers=["solid biomass", "wood pellets"], factors={"default": 2.0}
            ),
        )

    assert "wood pellets" in caplog.text
    assert result.loc["AT111", "solid biomass"] == 1000.0
    assert result.loc["AT111", "biogas"] == 100.0


def test_scaling_up_must_run_carriers_warns(potentials, caplog):
    with caplog.at_level(logging.WARNING):
        scale_biomass_potentials(potentials, params(factors={"default": 4.0}))

    assert "must-run" in caplog.text


def test_scaling_down_must_run_carriers_does_not_warn(potentials, caplog):
    with caplog.at_level(logging.WARNING):
        scale_biomass_potentials(potentials, params(factors={"default": 0.5}))

    assert "must-run" not in caplog.text


def test_sustainable_only_selection_does_not_warn(potentials, caplog):
    with caplog.at_level(logging.WARNING):
        scale_biomass_potentials(
            potentials,
            params(carriers=["solid biomass", "biogas"], factors={"default": 4.0}),
        )

    assert "must-run" not in caplog.text


class TestResolveFactors:
    """Country factor resolution against the ``default`` fallback."""

    countries = pd.Index(["AT", "DE", "CZ"])

    def test_default_applies_to_all(self):
        assert resolve_factors(self.countries, {"default": 2.0}) == {
            "AT": 2.0,
            "DE": 2.0,
            "CZ": 2.0,
        }

    def test_missing_default_is_neutral(self):
        assert resolve_factors(self.countries, {"AT": 0.5}) == {
            "AT": 0.5,
            "DE": 1.0,
            "CZ": 1.0,
        }

    def test_empty_config_is_neutral(self):
        assert resolve_factors(self.countries, {}) == {
            "AT": 1.0,
            "DE": 1.0,
            "CZ": 1.0,
        }

    def test_integer_factors_are_cast_to_float(self):
        resolved = resolve_factors(self.countries, {"default": 2})

        assert resolved["AT"] == 2.0
        assert isinstance(resolved["AT"], float)


def test_uniform_factor_is_logged_compactly(potentials, caplog):
    with caplog.at_level(logging.INFO):
        scale_biomass_potentials(potentials, params(factors={"default": 0.5}))

    assert "a factor of 0.5 in all countries" in caplog.text


def test_mixed_factors_are_logged_per_country(potentials, caplog):
    with caplog.at_level(logging.INFO):
        scale_biomass_potentials(
            potentials, params(factors={"default": 1.0, "AT": 0.5})
        )

    assert "AT=0.5" in caplog.text
