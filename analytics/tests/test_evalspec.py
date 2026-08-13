"""The declaration, and the two things that make it one.

A manifest is only a commitment if editing it is visible, and a scope rule is
only enforcement if breaking it raises. Everything else in the harness rests on
these two.
"""

from __future__ import annotations

import pytest

from cdlhub_analytics.ratings import evalspec, statespace


def test_the_manifest_matches_the_hash_it_was_pinned_at() -> None:
    """Editing the declaration after the fact fails here and in the release gate."""
    assert evalspec.sha256() == evalspec.PINNED_SHA256


def test_the_hash_moves_when_the_declaration_does() -> None:
    before = evalspec.sha256()
    original = evalspec.PRIMARY
    try:
        evalspec.PRIMARY = evalspec.Test(
            **{**original.__dict__, "target": "something the model happens to predict"}
        )
        assert evalspec.sha256() != before
    finally:
        evalspec.PRIMARY = original
    assert evalspec.sha256() == before


def test_the_invariants_match_the_hash_they_were_pinned_at() -> None:
    """The half that may never move, whatever any later phase needs."""
    assert evalspec.invariants_sha256() == evalspec.PINNED_INVARIANTS_SHA256


def test_adding_a_predictor_moves_the_manifest_and_not_the_invariants() -> None:
    """The exact door P5 used, and the reason it is a door rather than a hole."""
    manifest_before, fixed_before = evalspec.sha256(), evalspec.invariants_sha256()
    original = evalspec.PRIMARY
    try:
        evalspec.PRIMARY = evalspec.Test(
            **{**original.__dict__, "predictors": (*original.predictors, "something_new")}
        )
        assert evalspec.sha256() != manifest_before
        assert evalspec.invariants_sha256() == fixed_before
    finally:
        evalspec.PRIMARY = original


def test_swapping_the_baseline_moves_the_invariants() -> None:
    """What the door is not for: a comparison changed into one the model wins."""
    fixed_before = evalspec.invariants_sha256()
    original = evalspec.PRIMARY
    try:
        evalspec.PRIMARY = evalspec.Test(
            **{**original.__dict__, "baselines": ("something_easier",)}
        )
        assert evalspec.invariants_sha256() != fixed_before
    finally:
        evalspec.PRIMARY = original
    assert evalspec.invariants_sha256() == fixed_before


def test_every_superseded_predictor_survives_into_the_current_declaration() -> None:
    """Append-only, checked here as well as in the gate."""
    for entry in evalspec.PIN_HISTORY:
        for predictor in entry["predictors"]:
            assert predictor in evalspec.PRIMARY.predictors, entry["version"]


def test_the_history_holds_superseded_versions_and_not_this_one() -> None:
    versions = [entry["version"] for entry in evalspec.PIN_HISTORY]
    assert evalspec.VERSION not in versions
    assert len(versions) == len(set(versions))
    assert evalspec.PINNED_SHA256 not in [entry["sha256"] for entry in evalspec.PIN_HISTORY]


def test_a_forward_test_may_not_read_a_smoothed_coefficient() -> None:
    """The single most important line in the harness: it raises, it does not warn."""
    with pytest.raises(statespace.ScopeError):
        evalspec.assert_forward(statespace.SMOOTHED)
    with pytest.raises(statespace.ScopeError):
        evalspec.assert_forward(statespace.CAREER)
    evalspec.assert_forward(statespace.FILTERED)


def test_the_permitted_scope_is_one_of_the_families_the_estimator_stores() -> None:
    assert evalspec.SCOPE in statespace.SCOPES


def test_exactly_one_test_is_primary_and_no_secondary_claims_significance() -> None:
    """A suite this size with no declared primary is a licence to pick a winner."""
    assert evalspec.PRIMARY.role == "primary"
    assert evalspec.PRIMARY.significance_claimed
    assert [t.role for t in evalspec.SECONDARY] == ["secondary"] * len(evalspec.SECONDARY)
    assert not any(t.significance_claimed for t in evalspec.SECONDARY)


def test_every_declared_test_names_a_resampling_unit_the_manifest_explains() -> None:
    explained = set(evalspec.manifest()["resampling"])
    for test in (evalspec.PRIMARY, *evalspec.SECONDARY):
        assert test.unit in explained, test.name


def test_the_names_are_unique_so_an_artifact_cannot_overwrite_a_test() -> None:
    names = [t.name for t in (evalspec.PRIMARY, *evalspec.SECONDARY)]
    assert len(names) == len(set(names))
