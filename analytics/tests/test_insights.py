from typing import Any, cast

import pytest

from cdlhub_analytics.insights import (
    Atom,
    _ordinal,
    best_per_season,
    cap_per_subject,
    mode_null,
    model_null,
    what_wins,
)


def test_ordinal() -> None:
    assert _ordinal(1) == "1st"
    assert _ordinal(2) == "2nd"
    assert _ordinal(3) == "3rd"
    assert _ordinal(4) == "4th"
    assert _ordinal(11) == "11th"
    assert _ordinal(12) == "12th"
    assert _ordinal(13) == "13th"
    assert _ordinal(21) == "21st"
    assert _ordinal(91) == "91st"
    assert _ordinal(100) == "100th"


def atom(
    kind: str,
    subject_id: int,
    score: float,
    headline: str = "h",
    subject_type: str = "player",
    **detail: Any,
) -> Atom:
    return Atom(subject_type, subject_id, kind, headline, dict(detail), score)


# ---------- best_per_season ----------


def test_best_per_season_keeps_only_the_strongest_mode_slice() -> None:
    """One season's all-modes row plus its per-mode rows are one finding."""
    atoms = [
        atom("outlier", 1, 0.60, "2019 all modes", season_year=2019, mode=None),
        atom("outlier", 1, 0.80, "2019 Hardpoint", season_year=2019, mode="Hardpoint"),
        atom("outlier", 1, 0.55, "2019 S&D", season_year=2019, mode="Search & Destroy"),
    ]
    kept = best_per_season(atoms)
    assert len(kept) == 1
    assert kept[0].headline == "2019 Hardpoint"


def test_best_per_season_keeps_separate_seasons() -> None:
    """Two strong seasons are two findings, not one."""
    atoms = [
        atom("outlier", 1, 0.8, "2018", season_year=2018),
        atom("outlier", 1, 0.7, "2019", season_year=2019),
    ]
    assert len(best_per_season(atoms)) == 2


def test_best_per_season_keeps_separate_players() -> None:
    atoms = [
        atom("outlier", 1, 0.8, "a", season_year=2019),
        atom("outlier", 2, 0.7, "b", season_year=2019),
    ]
    assert len(best_per_season(atoms)) == 2


def test_best_per_season_passes_through_seasonless_atoms() -> None:
    """Career milestones and team peaks carry no season and must survive."""
    atoms = [
        atom("milestone", 1, 0.5, "career maps"),
        atom("outlier", 1, 0.8, "2019", season_year=2019),
    ]
    kept = best_per_season(atoms)
    assert {a.headline for a in kept} == {"career maps", "2019"}


def test_best_per_season_is_order_independent() -> None:
    a = atom("outlier", 1, 0.8, "high", season_year=2019)
    b = atom("outlier", 1, 0.8, "also", season_year=2019)
    assert best_per_season([a, b])[0].headline == best_per_season([b, a])[0].headline


# ---------- cap_per_subject ----------


def test_cap_per_subject_limits_one_subject_to_the_best_two() -> None:
    atoms = [atom("profile_extreme", 1, s, f"h{s}") for s in (0.9, 0.8, 0.7, 0.6)]
    kept = cap_per_subject(atoms, limit=2)
    assert [a.score for a in kept] == [0.9, 0.8]


def test_cap_per_subject_is_per_kind_and_per_subject() -> None:
    atoms = [
        atom("profile_extreme", 1, 0.9),
        atom("profile_extreme", 1, 0.8),
        atom("profile_extreme", 1, 0.7),  # dropped: third of this kind
        atom("outlier", 1, 0.7),  # kept: different kind
        atom("profile_extreme", 2, 0.7),  # kept: different subject
    ]
    kept = cap_per_subject(atoms, limit=2)
    assert len(kept) == 4
    assert sum(1 for a in kept if a.kind == "profile_extreme" and a.subject_id == 1) == 2


def test_cap_per_subject_separates_players_from_teams_with_the_same_id() -> None:
    """subject_id is only unique within subject_type."""
    atoms = [
        atom("team_style", 1, 0.9, subject_type="team"),
        atom("team_style", 1, 0.8, subject_type="team"),
        atom("team_style", 1, 0.7, subject_type="player"),
    ]
    assert len(cap_per_subject(atoms, limit=2)) == 3


def test_cap_per_subject_leaves_uncapped_kinds_alone() -> None:
    """Per-cohort model summaries are one fact each, however many share a subject."""
    atoms = [atom("what_wins", 1, s) for s in (0.9, 0.8, 0.7, 0.6)]
    assert len(cap_per_subject(atoms, limit=2)) == 4


def test_cap_per_subject_is_deterministic_across_orderings() -> None:
    atoms = [atom("outlier", 1, 0.5, f"h{i}") for i in range(5)]
    assert [a.headline for a in cap_per_subject(atoms, limit=2)] == [
        a.headline for a in cap_per_subject(list(reversed(atoms)), limit=2)
    ]


# ---------- what_wins ----------
#
# These pin the shape of the mode_weights artifact that what_wins reads. The
# feature keys differ per cohort and per feature-set version, so any consumer
# that hardcodes a key list reads zero the moment the published version moves —
# which is exactly how the site's copy of this ratio came to render an empty
# chart. web/lib/analytics.ts:getModeWeights must agree with what_wins.


class _ArtifactConn:
    """Minimal stand-in for the one query what_wins makes."""

    def __init__(self, payload: dict[str, Any] | None) -> None:
        self._payload = payload

    def execute(self, *_args: object, **_kwargs: object) -> "_ArtifactConn":
        return self

    def fetchone(self) -> tuple[object, ...] | None:
        return None if self._payload is None else (self._payload,)


def cohort(**over: Any) -> dict[str, Any]:
    """A 2.1.0-shaped Search & Destroy cohort: per-round slaying, no obj_p10."""
    base = {
        "season_id": 2,
        "year": 2018,
        "title": "WWII",
        "mode_id": 2,
        "mode": "Search & Destroy",
        "n_maps": 843,
        "slaying_features": ["snd_kpr", "snd_dpr"],
        "weights": {
            "snd_kpr": 0.9,
            "snd_dpr": -0.7,
            "snd_fb_rate": 0.3,
            "snd_fd_rate": -0.2,
            "snd_survival_rate": 0.5,
            "snd_bomb_pr": 0.6,
            "untraded_death_rate": -0.4,
            "trade_kills_pr": 0.2,
            "thrown_deaths_pr": -0.1,
        },
    }
    return {**base, **over}


def test_what_wins_reads_the_slaying_pair_off_the_artifact() -> None:
    """A cohort with no v1 keys at all still yields a ratio."""
    conn = _ArtifactConn({"cohorts": [cohort()]})
    (found,) = what_wins(cast(Any, conn), pr_run=7)
    # slaying = (|0.9| + |0.7|) / 2 = 0.8; rest = 2.3 / 7 = 0.3286
    assert found.detail["rest_vs_slay"] == 0.41
    assert found.kind == "what_wins"


def test_what_wins_falls_back_to_v1_keys_when_the_artifact_omits_them() -> None:
    payload = cohort(
        slaying_features=None,
        weights={"kills_p10": 0.6, "deaths_p10": -0.4, "obj_p10": 1.0},
    )
    del payload["slaying_features"]
    conn = _ArtifactConn({"cohorts": [payload]})
    (found,) = what_wins(cast(Any, conn), pr_run=7)
    assert found.detail["rest_vs_slay"] == 2.0


def test_what_wins_does_not_call_the_remainder_objective_play() -> None:
    """The remainder mixes survival and trade economy, so the published
    sentence names the gunfight — the only boundary the model defines."""
    conn = _ArtifactConn({"cohorts": [cohort()]})
    (found,) = what_wins(cast(Any, conn), pr_run=7)
    assert "objective play" not in found.headline
    assert "kills and deaths" in found.headline


def test_what_wins_skips_a_cohort_with_no_features_beyond_slaying() -> None:
    payload = cohort(weights={"snd_kpr": 0.9, "snd_dpr": -0.7})
    assert what_wins(cast(Any, _ArtifactConn({"cohorts": [payload]})), pr_run=7) == []


def test_what_wins_is_empty_without_an_artifact() -> None:
    assert what_wins(cast(Any, _ArtifactConn(None)), pr_run=7) == []


def test_what_wins_prefers_the_ratio_the_fit_published() -> None:
    """The fit computes the ratio from unrounded weights; recomputing from the
    rounded ones published alongside is the fallback, not the primary path."""
    conn = _ArtifactConn({"cohorts": [cohort(rest_vs_slay=0.55)]})
    (found,) = what_wins(cast(Any, conn), pr_run=7)
    assert found.detail["rest_vs_slay"] == 0.55


def test_what_wins_suppresses_a_cohort_whose_interval_covers_one() -> None:
    """Every reading is a claim about which side of 1.0 the ratio sits on. An
    interval spanning 1.0 does not support any of them."""
    payload = cohort(rest_vs_slay=0.41, rest_vs_slay_ci=[0.28, 1.4])
    assert what_wins(cast(Any, _ArtifactConn({"cohorts": [payload]})), pr_run=7) == []


def test_what_wins_publishes_a_resolved_interval_with_the_finding() -> None:
    payload = cohort(rest_vs_slay=0.41, rest_vs_slay_ci=[0.28, 0.62])
    (found,) = what_wins(cast(Any, _ArtifactConn({"cohorts": [payload]})), pr_run=7)
    assert "95% CI 0.3–0.6x" in found.headline
    assert found.detail["rest_vs_slay_ci"] == [0.28, 0.62]
    # Confidence follows the interval's nearer end (1 - 0.62), not the point.
    assert found.score == pytest.approx(0.45 + 0.38 * 0.3)


def test_what_wins_still_fires_when_no_interval_was_published() -> None:
    """Runs written before the bootstrap existed keep their findings."""
    (found,) = what_wins(cast(Any, _ArtifactConn({"cohorts": [cohort()]})), pr_run=7)
    assert "95% CI" not in found.headline
    assert "rest_vs_slay_ci" not in found.detail


# ---------- model_null ----------


class _NullConn:
    """Answers the four lookups model_null makes, keyed on the query text."""

    def __init__(self, gaps: dict[str, Any] | None) -> None:
        self._gaps = gaps
        self._row: tuple[object, ...] | None = None

    def execute(self, sql: str, params: tuple[object, ...]) -> "_NullConn":
        if "FROM backtests" in sql:
            # (brier, accuracy, n): winprob is run 1, glicko2 run 2.
            self._row = (0.2273, 0.618, 1310) if params[0] == 1 else (0.2287, 0.634, 1310)
        elif "'coefficients'" in sql:
            self._row = ({"final_weights": {"form_diff": -0.16}},)
        else:
            self._row = None if self._gaps is None else (self._gaps,)
        return self

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


def gaps_payload(a: str = "glicko2", b: str = "winprob_v1", flip: bool = False) -> dict[str, Any]:
    lo, hi, delta = -0.00294, 0.00588, 0.00141
    if flip:
        lo, hi, delta = -hi, -lo, -delta
    return {
        "available": True,
        "pairs": [
            {
                "a": a,
                "b": b,
                "delta": delta,
                "lo": lo,
                "hi": hi,
                "dm_p": 0.517,
                "mde80": 0.00609,
            }
        ],
        "form_power": {"beta_detectable": 1.65, "swing_pp": 33.89},
    }


def test_model_null_publishes_the_interval_and_the_power_statement() -> None:
    (found,) = model_null(cast(Any, _NullConn(gaps_payload())), wp_run=1, glicko_run=2)
    assert "95% CI -0.0029 to +0.0059" in found.headline
    assert "34 points of win probability" in found.headline
    assert found.detail["brier_gap_lo"] == -0.00294
    assert found.detail["brier_gap_hi"] == 0.00588
    assert found.detail["detectable_form_beta"] == 1.65


def test_model_null_re_signs_the_interval_to_match_the_gap() -> None:
    """The artifact's sign follows its pair order. A headline whose gap and
    interval point opposite ways is worse than one with no interval at all."""
    flipped = gaps_payload(a="winprob_v1", b="glicko2", flip=True)
    (found,) = model_null(cast(Any, _NullConn(flipped)), wp_run=1, glicko_run=2)
    assert "95% CI -0.0029 to +0.0059" in found.headline
    assert found.detail["brier_gap_lo"] < found.detail["brier_gap"] < found.detail["brier_gap_hi"]


def test_model_null_without_gaps_says_less_rather_than_more() -> None:
    (found,) = model_null(cast(Any, _NullConn(None)), wp_run=1, glicko_run=2)
    assert "95% CI" not in found.headline
    assert "could resolve is limited" not in found.headline
    assert "brier_gap_lo" not in found.detail


# ---------- mode_null ----------


class _ModeConn:
    """Answers the one artifact lookup mode_null makes."""

    def __init__(self, rows: list[tuple[str, dict[str, Any]]]) -> None:
        self._rows = rows

    def execute(self, sql: str, params: tuple[object, ...]) -> "_ModeConn":
        assert "model_artifacts" in sql
        return self

    def fetchall(self) -> list[tuple[str, dict[str, Any]]]:
        return self._rows


def _mode_artifacts(
    a: str = "global", b: str = "mode", flip: bool = False, exceeds: bool = False
) -> list[tuple[str, dict[str, Any]]]:
    delta, lo, hi = -0.00388, -0.00613, -0.00168
    if flip:
        delta, lo, hi = -delta, -hi, -lo
    return [
        (
            "map_backtest",
            {
                "n_maps": 5087,
                "arms": {
                    "global": {"brier": 0.23645},
                    "mode": {"brier": 0.24033},
                    "blend": {"brier": 0.23579},
                },
                "gaps": {
                    "available": True,
                    "pairs": [
                        {
                            "a": a,
                            "b": b,
                            "delta": delta,
                            "lo": lo,
                            "hi": hi,
                            "dm_p": 0.001,
                            "mde80": 0.00318,
                            "excludes_zero": True,
                        }
                    ],
                },
            },
        ),
        (
            "mode_specialization",
            {
                "available": True,
                "n_cells": 98,
                "observed_sd": 54.82,
                "null_mean_sd": 51.43,
                "null_lo": 47.41,
                "null_hi": 55.46,
                "p_value": 0.0598,
                "exceeds_null": exceeds,
            },
        ),
    ]


def test_mode_null_states_both_results_and_what_it_cannot_rule_out() -> None:
    (found,) = mode_null(cast(Any, _ModeConn(_mode_artifacts())), map_run=1)
    assert found.kind == "mode_null"
    assert "worse, not better" in found.headline
    # The permutation null, not just the Brier gap.
    assert "shuffled within each event" in found.headline
    assert "p=0.060" in found.headline
    # The qualifier that keeps the null honest.
    assert "rules out a large effect, not any effect" in found.headline
    assert found.detail["brier_gap"] == -0.00388
    assert found.detail["permutation_p"] == 0.0598


def test_mode_null_re_signs_the_gap_to_match_the_pair_order() -> None:
    """global − mode is the claim; the artifact may have stored mode − global."""
    (found,) = mode_null(cast(Any, _ModeConn(_mode_artifacts(a="mode", b="global", flip=True))), 1)
    assert found.detail["brier_gap"] == -0.00388
    assert found.detail["brier_gap_lo"] < found.detail["brier_gap"] < found.detail["brier_gap_hi"]
    assert "worse, not better" in found.headline


def test_mode_null_does_not_claim_a_null_when_the_spread_clears_it() -> None:
    arts = _mode_artifacts(flip=True, exceeds=True)
    (found,) = mode_null(cast(Any, _ModeConn(arts)), map_run=1)
    assert "worse, not better" not in found.headline
    assert "not measurable" not in found.headline


def test_mode_null_is_silent_without_the_artifacts() -> None:
    assert mode_null(cast(Any, _ModeConn([])), map_run=1) == []
