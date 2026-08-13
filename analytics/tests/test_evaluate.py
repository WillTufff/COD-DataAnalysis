"""The harness, on leagues whose answer is known. No database required.

Three things are worth testing without a database. A cluster bootstrap has to
draw whole players, or the interval it publishes is the one the plan says it must
not. The placebos have to fail when they are shown real structure, or they are
decorations. And the reproduction comparison has to notice a number that moved,
or the gate it feeds passes on anything.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np

from cdlhub_analytics.maprows import MapRow
from cdlhub_analytics.ratings import evaluate, placebo, rapm, skillbase

DAY0 = date(2018, 1, 1)


def _observation(
    player_id: int,
    composite: float,
    kd: float,
    kd_next: float,
    *,
    openskill: float | None = 0.0,
    skill: float | None = None,
    season_a: int = 1,
) -> evaluate.Observation:
    return evaluate.Observation(
        player_id=player_id,
        season_a=season_a,
        season_b=season_a + 1,
        title_a="WWII",
        year_a=2018,
        composite=composite,
        kd=kd,
        openskill=openskill,
        skill=skill,
        kd_next=kd_next,
        composite_next=composite,
        moved_team=False,
        rookie=season_a == 1,
        events_a=frozenset({1}),
    )


def _panel(n: int = 240, seed: int = 3) -> list[evaluate.Observation]:
    """A league where K/D persists and the composite carries a weaker signal."""
    rng = np.random.default_rng(seed)
    # The SKILL column is drawn from its own generator so that adding a fourth
    # predictor to the fixture does not shift the stream the other three were
    # drawn from, and every number this panel already produced stays put.
    skills = np.random.default_rng(seed + 1).standard_normal(n)
    out = []
    for i in range(n):
        kd = float(rng.standard_normal())
        out.append(
            _observation(
                player_id=1 + i // 2,  # two transitions per player: real clusters
                composite=0.4 * kd + float(rng.standard_normal()),
                kd=kd,
                kd_next=0.6 * kd + 0.8 * float(rng.standard_normal()),
                openskill=0.2 * kd + float(rng.standard_normal()),
                skill=0.3 * kd + float(skills[i]),
            )
        )
    return out


# ------------------------------------------------------------ the resampling


def test_a_clustered_draw_takes_whole_players() -> None:
    """The unit is the cluster: a player is in a draw entirely or not at all."""
    panel = evaluate._ordered(_panel())
    rows_of: dict[int, set[int]] = {}
    for i, o in enumerate(panel):
        rows_of.setdefault(o.player_id, set()).add(i)
    for take in evaluate._draw(panel, 20, by_cluster=True)[:5]:
        drawn = list(take)
        for player_id, rows in rows_of.items():
            hits = sum(1 for r in drawn if r in rows)
            assert hits % len(rows) == 0, player_id


def test_the_clustered_interval_is_the_wider_one() -> None:
    """Observations inside a player are not independent, and the interval says so."""
    panel = evaluate._ordered(_panel())
    clustered = evaluate._persistence_stats(panel, by_cluster=True)
    naive = evaluate._persistence_stats(panel, by_cluster=False)
    assert clustered["gaps"]["composite"]["se"] > naive["gaps"]["composite"]["se"]


# --------------------------------------------------------------- the primary


def test_the_primary_test_declares_a_threshold_before_it_reports_a_gap() -> None:
    out = evaluate.primary(_panel())
    assert out["available"]
    for name, block in out["power"]["by_predictor"].items():
        assert block["mde80_clustered"] is not None, name
        assert block["mde80_clustered"] >= block["mde80_independent"]
    for gap in out["gaps"].values():
        assert gap["mde80"] is not None
        assert gap["clears_mde"] is not None


def test_a_predictor_that_is_the_baseline_plus_noise_does_not_beat_it() -> None:
    out = evaluate.primary(_panel())
    assert out["gaps"]["composite"]["beats_baseline"] is False


def test_the_primary_test_publishes_what_it_dropped() -> None:
    panel = _panel()
    blinded = [
        evaluate.Observation(**{**o.__dict__, "openskill": None}) if i % 5 == 0 else o
        for i, o in enumerate(panel)
    ]
    out = evaluate.primary(blinded)
    assert out["n_dropped_missing_a_predictor"] == len(panel) - out["n"]
    assert out["dropped_by_predictor"]["openskill"] == len(panel) - out["n"]


# ------------------------------------------------------------- the P5 floor


def _resolutions(
    panel: list[evaluate.Observation], seasonal: int, era: int = 0
) -> dict[tuple[int, int], str]:
    """Season resolution on the first `seasonal` rows, era on the `era` after them."""
    out = {(o.player_id, o.season_a): "season" for o in panel[:seasonal]}
    out.update({(o.player_id, o.season_a): "era" for o in panel[seasonal : seasonal + era]})
    return out


def _coefficients(panel: list[evaluate.Observation], covered: int) -> dict[tuple[int, int], float]:
    """A filtered coefficient for the first `covered` rows and nothing else."""
    return {(o.player_id, o.season_a): 0.01 * i for i, o in enumerate(panel[:covered])}


def test_the_floor_is_computed_on_the_panel_a_skill_rating_could_reach() -> None:
    """Not the published panel: only where a filtered coefficient exists."""
    panel = _panel()
    out = evaluate.skill_power(panel, _resolutions(panel, 120))
    assert out["available"]
    assert out["n_panel"] == len(panel)
    assert out["n_eligible"] == 120
    assert out["dropped"]["no_filtered_coefficient"] == len(panel) - 120


def test_an_era_coefficient_does_not_widen_the_panel_it_cannot_carry() -> None:
    """One estimate filed against three seasons cannot move across a transition."""
    panel = _panel()
    out = evaluate.skill_power(panel, _resolutions(panel, 120, era=80))
    assert out["n_eligible"] == 120
    assert out["dropped"]["era_resolution_only"] == 80
    assert out["dropped"]["no_filtered_coefficient"] == len(panel) - 200
    assert out["wider_panel"]["n"] == 200
    assert (
        out["wider_panel"]["floor"]["mde80_independent"]
        < out["floors"]["composite_measured"]["mde80_independent"]
    )


def test_a_narrower_panel_raises_the_floor() -> None:
    """The whole reason the floor is recomputed rather than inherited from PE."""
    panel = _panel()
    wide = evaluate.skill_power(panel, _resolutions(panel, len(panel)))
    narrow = evaluate.skill_power(panel, _resolutions(panel, 120))
    anchor = "composite_measured"
    assert (
        narrow["floors"][anchor]["mde80_independent"] > wide["floors"][anchor]["mde80_independent"]
    )


def test_the_floor_says_how_far_a_rating_has_to_travel() -> None:
    panel = _panel()
    out = evaluate.skill_power(panel, _resolutions(panel, 160))
    anchor = out["floors"]["composite_measured"]["mde80_clustered"]
    assert out["distance_to_clear"] == round(anchor - out["composite_gap_here"], 4)
    assert str(out["distance_to_clear"]) in out["statement"]


def test_a_panel_too_thin_to_score_says_so_rather_than_publishing_a_floor() -> None:
    panel = _panel()
    out = evaluate.skill_power(panel, _resolutions(panel, 10))
    assert not out["available"]
    assert out["n_eligible"] == 10


def test_the_plusminus_secondary_separates_a_season_estimate_from_an_era_one() -> None:
    """The defect this found in PE: 553 cells, 122 of them one number repeated."""
    panel = _panel()
    out = evaluate.season_plusminus_persistence(
        panel,
        _coefficients(panel, 200),
        "filtered",
        _resolutions(panel, 120, era=80),
    )
    assert out["resolution_read"] == "season"
    assert out["n"] == 120
    assert out["pooled_over_resolutions"]["n"] == 200
    assert out["by_resolution"]["era"]["n"] == 80


# ---------------------------------------------------------- the reproduction


def _stored(panel: list[evaluate.Observation]) -> dict[str, Any]:
    recomputed = evaluate._published_persistence(evaluate._ordered(panel))
    return {
        "n_pairs": recomputed["n_pairs"],
        "cells": recomputed["cells"],
        "contrasts": recomputed["contrasts"],
    }


def test_the_reproduction_passes_when_the_numbers_agree() -> None:
    panel = _panel()
    checks = evaluate._compare(
        evaluate._published_persistence(evaluate._ordered(panel)), dict(_stored(panel))
    )
    assert all(c["matches"] for c in checks)


def test_the_reproduction_notices_a_cell_that_moved() -> None:
    """A harness that cannot tell a moved number from an unmoved one is not a gate."""
    panel = _panel()
    stored: dict[str, Any] = dict(_stored(panel))
    cells = dict(stored["cells"])
    cells["kd->kd"] = {**cells["kd->kd"], "r": cells["kd->kd"]["r"] + 0.01}
    stored["cells"] = cells
    checks = evaluate._compare(evaluate._published_persistence(evaluate._ordered(panel)), stored)
    off = [c for c in checks if not c["matches"]]
    assert [c["what"] for c in off] == ["cells.kd->kd.r"]


def test_a_missing_published_number_is_a_mismatch_rather_than_a_pass() -> None:
    panel = _panel()
    checks = evaluate._compare(
        evaluate._published_persistence(evaluate._ordered(panel)), {"cells": {}, "contrasts": {}}
    )
    assert not any(c["matches"] for c in checks)


# ------------------------------------------------------------- the placebos


def _league(n_maps: int = 400, seed: int = 5) -> list[rapm.AdmittedMap]:
    """Eight players, sides drawn at random, and player 1 always on the winner."""
    rng = np.random.default_rng(seed)
    games = []
    for g in range(n_maps):
        pool = [int(p) for p in rng.permutation(list(range(2, 13)))]
        home = tuple(sorted([1, *pool[:3]]))
        away = tuple(sorted(pool[3:7]))
        games.append(
            rapm.AdmittedMap(
                game_id=g + 1,
                series_id=g + 1,
                season_id=1,
                title="WWII",
                mode_slug="hardpoint",
                played_at=DAY0 + timedelta(days=g),
                home_team_id=100,
                away_team_id=200,
                home_players=home,
                away_players=away,
                home_won=True,
                home_margin=None,
            )
        )
    return games


def test_shuffling_the_sides_leaves_intervals_covering_zero() -> None:
    out = placebo.shuffled_sides(_league(), replicates=3)
    assert out["available"]
    assert out["coverage_mean"] >= 0.90
    assert out["passes"]


def test_a_duplicated_player_adds_a_column_and_no_rank() -> None:
    out = placebo.duplicated_player(_league())
    assert out["columns_after"] == out["columns_before"] + 1
    assert out["rank_after"] == out["rank_before"]
    assert out["passes"]


def test_the_permutation_placebo_fails_when_it_is_shown_real_structure() -> None:
    """A placebo that passes on paired data is not testing anything."""
    x = list(np.linspace(-2.0, 2.0, 200))
    paired = placebo.permuted_seasons(x, x)
    assert paired["passes"]
    assert abs(paired["observed_r"] - 1.0) < 1e-9
    # And the same call on a target that is already noise cannot separate them.
    noise = list(np.random.default_rng(2).standard_normal(200))
    assert not placebo.permuted_seasons(x, noise)["passes"]


def test_the_venue_placebo_is_declared_rather_than_quietly_missing() -> None:
    assert "venue_permutation" in placebo.DEFERRED


# ------------------------------------------------------------- the baseline


def _maps(n: int = 200) -> list[MapRow]:
    rows: list[MapRow] = []
    for g in range(n):
        winner = 100 if g % 2 == 0 else 200
        for team_id, members in ((100, (1, 2, 3, 4)), (200, (5, 6, 7, 8))):
            for pid in members:
                rows.append(
                    MapRow(
                        player_id=pid,
                        team_id=team_id,
                        game_id=g + 1,
                        series_id=g + 1,
                        season_id=1,
                        mode_id=1,
                        mode_slug="hardpoint",
                        title="WWII",
                        event_id=1,
                        played_at=DAY0 + timedelta(days=g),
                        duration_s=600.0,
                        winner_team_id=winner,
                        values={"kills": 20.0, "deaths": 20.0},
                        team_kills=80.0,
                        team_hill_time=100.0,
                        map_key=f"s{g + 1}#1",
                    )
                )
    return rows


def test_the_baseline_predicts_before_it_updates() -> None:
    """The first map is seen by a model that knows nothing: it has to be a coin flip."""
    fit = skillbase.fit_walk_forward(_maps())
    first = min(fit.predictions)
    assert abs(fit.predictions[first].p - 0.5) < 1e-9
    assert fit.n_maps == 200
    assert fit.n_players == 8


def test_a_thin_player_season_is_withheld_rather_than_published() -> None:
    rows = [r for r in _maps() if r.game_id <= 4]
    fit = skillbase.fit_walk_forward(rows)
    assert fit.season_skill == {}
    assert max(fit.season_maps.values()) < skillbase.MIN_MAPS_SEASON
