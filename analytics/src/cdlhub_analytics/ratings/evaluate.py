"""The harness that scores the ratings, built before the model it will judge.

`evalspec.py` declares what is tested; this runs it. Four blocks, in the order
they have to be trusted:

**Reproduction, first.** A harness that cannot recover the numbers the pipeline
already publishes is measuring something else, so nothing else here is read until
an independent recomputation of the published persistence test matches the stored
artifact to rounding. The published forecast figures are checked against the
figures printed on /methodology in the same block — that is the check that would
have caught what it did catch: the page's validation section still quoted 541
transitions and 9,030 maps from a run before the identity merges, the lineup rule
and feature set 2.2.0, while its own pre-flight section quoted the current 561.

**The primary test.** Next-season persistence on the off-diagonal cell, against
era-adjusted K/D z and the `openskill` player baseline, paired, clustered on the
player. One test, declared in advance, with a threshold computed before the
comparison is read.

**The minimum detectable effect, computed.** The pre-flight's closed-form
dependent-correlation variance at this record's own sample size, baseline
correlation and measured predictor agreement — not the 0.7 that formula is
swept at — and then widened by the design effect the clustering actually costs,
measured against the same draw taken per observation. A gate declared below that
number cannot be met by a model that works.

**The secondary set, labelled.** Leave-one-title-out, leave-one-event-out,
rookie emergence, roster-move shock and calibration by bucket. Diagnostics, with
`significance_claimed: false` written next to every number, because a suite this
size with no declared primary is a licence to pick a winner afterwards.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import psycopg

from ..backtest import Prediction
from ..maprows import PUBLISHED_FROM_YEAR, MapRow
from ..resample import order as content_order
from ..resample import stream
from . import evalspec, holdout, placebo, rapm, simleague, skillbase, statespace
from . import player_rating as pr

Conn = psycopg.Connection[tuple[object, ...]]

# The predictors of the primary test, in the order they are reported. `kd_z` is
# the baseline every gap is taken against.
#
# `RETAINED` is the set the gate ran on before SKILL existed. A fourth predictor
# narrows the shared panel — a paired bootstrap has to be paired, so every arm is
# scored on the rows all of them cover — and the published three-way numbers
# would have moved for no reason but that. So both blocks are computed and both
# say what population they are on.
BASELINE = "kd_z"
PREDICTORS = ("composite", "openskill", "skill", BASELINE)
RETAINED = ("composite", "openskill", BASELINE)

# The last season of the CWL era. Only the calibration buckets read it.
CWL_LAST_YEAR = 2019


@dataclass(frozen=True)
class Observation:
    """One player's season-to-season transition, with every predictor aligned."""

    player_id: int
    season_a: int
    season_b: int
    title_a: str
    year_a: int
    composite: float
    kd: float
    # The online baseline, and the box-score prior's posterior. Both may be
    # missing on a row the published test still counts, and neither is allowed
    # to shrink the population the reproduction runs on.
    openskill: float | None
    skill: float | None
    kd_next: float
    composite_next: float
    moved_team: bool
    rookie: bool
    events_a: frozenset[int]


def _pearson(x: Sequence[float] | np.ndarray[Any, Any], y: Sequence[float] | Any) -> float:
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(a) < 3 or a.std() == 0.0 or b.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _ci(draws: Sequence[float]) -> tuple[float | None, float | None]:
    kept = [d for d in draws if not math.isnan(d)]
    if not kept:
        return (None, None)
    lo, hi = np.percentile(kept, [2.5, 97.5])
    return (round(float(lo), 4), round(float(hi), 4))


# ------------------------------------------------------------------ the panel


def _season_tables(
    conn: Conn, rating_run_id: int, era_run_id: int
) -> tuple[dict[tuple[int, int], tuple[int, float]], dict[tuple[int, int], tuple[int, float]]]:
    """The two published season quantities, keyed (player, season) as holdout reads them."""
    rows = conn.execute(
        "SELECT player_id, season_id, maps_played, rating FROM player_season_adjusted"
        " WHERE run_id = %s AND mode_id IS NULL AND rating IS NOT NULL",
        (rating_run_id,),
    ).fetchall()
    rating = {
        (cast(int, r[0]), cast(int, r[1])): (cast(int, r[2]), cast(float, r[3])) for r in rows
    }
    rows = conn.execute(
        "SELECT player_id, season_id, maps_played, kd_z FROM player_season_adjusted"
        " WHERE run_id = %s AND mode_id IS NULL AND kd_z IS NOT NULL",
        (era_run_id,),
    ).fetchall()
    kd = {(cast(int, r[0]), cast(int, r[1])): (cast(int, r[2]), cast(float, r[3])) for r in rows}
    return rating, kd


def _context(
    rows: Sequence[MapRow],
) -> tuple[dict[tuple[int, int], int], dict[tuple[int, int], frozenset[int]], dict[int, str]]:
    """Per player-season the modal team and the events played; per season the title."""
    teams: dict[tuple[int, int], Counter[int]] = defaultdict(Counter)
    events: dict[tuple[int, int], set[int]] = defaultdict(set)
    title: dict[int, str] = {}
    for r in rows:
        teams[(r.player_id, r.season_id)][r.team_id] += 1
        events[(r.player_id, r.season_id)].add(r.event_id)
        title[r.season_id] = r.title
    return (
        {k: c.most_common(1)[0][0] for k, c in teams.items()},
        {k: frozenset(v) for k, v in events.items()},
        title,
    )


def venue_flags(conn: Conn) -> dict[int, bool | None]:
    """Per event, the resolved LAN verdict. Three events are still undecided."""
    rows = conn.execute("SELECT id, is_lan FROM events").fetchall()
    return {cast(int, r[0]): (None if r[1] is None else bool(r[1])) for r in rows}


def eras(conn: Conn) -> dict[int, str]:
    """Per season, which era it belongs to. The CWL/CDL seam is 2019/2020."""
    seasons, _modes = pr.label_context(conn)
    return {
        sid: ("CWL" if int(cast(int, info["year"])) <= CWL_LAST_YEAR else "CDL")
        for sid, info in seasons.items()
    }


def build_panel(
    conn: Conn,
    rating_run_id: int,
    era_run_id: int,
    rows: Sequence[MapRow],
    openskill: dict[tuple[int, int], float],
    skill: dict[tuple[int, int], float] | None = None,
) -> list[Observation]:
    """Every consecutive qualified transition, with all four columns aligned.

    The inclusion rule is the published test's, exactly: both quantities on both
    sides of the transition, and at least the qualification floor of maps on
    each. The `openskill` column is allowed to be missing rather than shrinking
    the population — the reproduction block has to run on the same rows the
    published artifact was computed on, and the primary test takes the
    intersection itself and publishes what it dropped.
    """
    rating, kd = _season_tables(conn, rating_run_id, era_run_id)
    modal_team, events, titles = _context(rows)
    seasons, _modes = pr.label_context(conn)
    # The panel is the published population. A season the site withholds is not
    # scored here either, or the harness reports a correlation over numbers no
    # reader can look up.
    ordered = [
        s
        for s in sorted(seasons, key=lambda s: seasons[s]["year"])
        if seasons[s]["year"] >= PUBLISHED_FROM_YEAR
    ]
    transitions = list(zip(ordered, ordered[1:], strict=False))
    qualified_first: dict[int, int] = {}
    for pid, season in sorted(rating):
        if (pid, season) in kd and min(rating[(pid, season)][0], kd[(pid, season)][0]) >= (
            holdout.MIN_MAPS_EACH_SIDE
        ):
            qualified_first.setdefault(pid, season)

    out: list[Observation] = []
    for a, b in transitions:
        for pid, season in sorted(rating):
            if season != a:
                continue
            keys = [(pid, a), (pid, b)]
            if not all(k in rating and k in kd for k in keys):
                continue
            if any(min(rating[k][0], kd[k][0]) < holdout.MIN_MAPS_EACH_SIDE for k in keys):
                continue
            out.append(
                Observation(
                    player_id=pid,
                    season_a=a,
                    season_b=b,
                    title_a=titles.get(a, str(seasons[a]["title"])),
                    year_a=int(cast(int, seasons[a]["year"])),
                    composite=rating[(pid, a)][1],
                    kd=kd[(pid, a)][1],
                    openskill=openskill.get((pid, a)),
                    skill=None if skill is None else skill.get((pid, a)),
                    kd_next=kd[(pid, b)][1],
                    composite_next=rating[(pid, b)][1],
                    moved_team=modal_team.get((pid, a)) != modal_team.get((pid, b)),
                    rookie=qualified_first.get(pid) == a,
                    events_a=events.get((pid, a), frozenset()),
                )
            )
    return out


# ------------------------------------------------------- the paired bootstrap


def _columns(panel: Sequence[Observation]) -> dict[str, np.ndarray[Any, Any]]:
    return {
        "composite": np.array([o.composite for o in panel], dtype=float),
        "openskill": np.array([o.openskill if o.openskill is not None else np.nan for o in panel]),
        "skill": np.array([o.skill if o.skill is not None else np.nan for o in panel]),
        BASELINE: np.array([o.kd for o in panel], dtype=float),
        "target": np.array([o.kd_next for o in panel], dtype=float),
    }


def _ordered(panel: Sequence[Observation]) -> list[Observation]:
    """The panel in an order fixed by its contents rather than by a player id.

    A bootstrap draws positions, so whatever sits at position zero decides which
    observations share a draw. Collected in `player_id` order, that decision
    belongs to the loader's numbering, and a reload that renumbers the table
    moves every interval while no correlation moves.
    """
    cols = _columns(panel)
    take = content_order(
        [
            cols["composite"],
            cols[BASELINE],
            cols["target"],
            np.nan_to_num(cols["openskill"], nan=-1e9),
        ]
    )
    return [panel[i] for i in take]


def _draw(panel: Sequence[Observation], b: int, by_cluster: bool) -> list[np.ndarray[Any, Any]]:
    """`b` resamples of the panel, as row-index arrays.

    Clustered, the unit is the player: a persistence observation is assembled
    from tens of series, so no series contains a whole one and the player is the
    smallest cluster that does. The generator is seeded from the panel's own
    contents, so the draw does not depend on how the rows were keyed or on which
    groups were reached before this one, and the clusters are ordered by what
    they contain for the same reason the rows are.
    """
    cols = _columns(panel)
    rng = stream(
        evalspec.BOOTSTRAP_SEED + (1 if by_cluster else 0),
        cols["composite"],
        cols["target"],
    )
    n = len(panel)
    if not by_cluster:
        return [np.asarray(row, dtype=int) for row in rng.integers(0, n, size=(b, n))]

    groups: dict[int, list[int]] = defaultdict(list)
    for i, o in enumerate(panel):
        groups[o.player_id].append(i)
    keys = sorted(
        groups,
        key=lambda pid: (
            tuple(float(cols["target"][i]) for i in groups[pid]),
            tuple(float(cols[BASELINE][i]) for i in groups[pid]),
            len(groups[pid]),
        ),
    )
    members = [np.asarray(groups[k], dtype=int) for k in keys]
    picks = rng.integers(0, len(members), size=(b, len(members)))
    return [np.concatenate([members[j] for j in row]) for row in picks]


def _persistence_stats(
    panel: Sequence[Observation], by_cluster: bool, names: Sequence[str] = PREDICTORS
) -> dict[str, Any]:
    """Every predictor's correlation with the target, and its gap to the baseline."""
    cols = _columns(panel)
    draws: dict[str, list[float]] = {name: [] for name in names}
    for take in _draw(panel, evalspec.BOOTSTRAP_B, by_cluster):
        for name in names:
            draws[name].append(_pearson(cols[name][take], cols["target"][take]))

    per: dict[str, Any] = {}
    for name in names:
        r = _pearson(cols[name], cols["target"])
        lo, hi = _ci(draws[name])
        per[name] = {"r": None if math.isnan(r) else round(r, 4), "lo": lo, "hi": hi}

    gaps: dict[str, Any] = {}
    base_r = _pearson(cols[BASELINE], cols["target"])
    for name in names:
        if name == BASELINE:
            continue
        point = _pearson(cols[name], cols["target"]) - base_r
        d = [
            x - y
            for x, y in zip(draws[name], draws[BASELINE], strict=True)
            if not (math.isnan(x) or math.isnan(y))
        ]
        lo, hi = _ci(d)
        gaps[name] = {
            "what": f"predicting next K/D z: r({name}) − r({BASELINE})",
            "delta_r": None if math.isnan(point) else round(point, 4),
            "lo": lo,
            "hi": hi,
            "se": round(float(np.std(d, ddof=1)), 5) if len(d) > 1 else None,
            "excludes_zero": bool(lo is not None and hi is not None and (lo > 0.0 or hi < 0.0)),
        }
    return {"n": len(panel), "predictors": per, "gaps": gaps}


def _gate_block(scored: Sequence[Observation], names: Sequence[str]) -> dict[str, Any]:
    """The comparison itself, on whatever panel and predictor set it is handed.

    Parameterised because there are two of them: the four-way panel the gate
    runs on, and the three-way panel the published numbers were computed on
    before SKILL existed. One implementation, two populations, each labelled.
    """
    clustered = _persistence_stats(scored, by_cluster=True, names=names)
    naive = _persistence_stats(scored, by_cluster=False, names=names)
    cols = _columns(scored)
    baseline_r = float(_pearson(cols[BASELINE], cols["target"]))

    # How much the clustering costs, measured rather than assumed: the ratio of
    # the two bootstrap spreads on the same gap. Above 1 means observations
    # inside a player are not independent, which is the direction the pre-flight
    # predicted and the reason a floor computed for independent observations is
    # a floor.
    effects = []
    for name, gap in clustered["gaps"].items():
        base = naive["gaps"][name]
        if gap["se"] and base["se"]:
            effects.append(gap["se"] / base["se"])
    design_effect = float(np.median(effects)) if effects else 1.0

    # The predictors' agreement with the baseline, measured. Two correlations of
    # the same target are dependent, and how dependent decides the variance of
    # their difference — so each predictor gets its own threshold rather than one
    # global number, and each is computed from its own agreement rather than from
    # the 0.7 the pre-flight sweeps that formula at.
    agreement = {
        name: round(float(_pearson(cols[name], cols[BASELINE])), 4)
        for name in names
        if name != BASELINE
    }
    power: dict[str, Any] = {
        "what": (
            "per predictor, the smallest persistence gap this record could detect at 80% "
            "power — from the sample size, the baseline correlation and that predictor's "
            "measured agreement with the baseline — widened by the design effect the "
            "clustering costs"
        ),
        "design_effect": round(design_effect, 3),
        "baseline_r": round(baseline_r, 4),
        "by_predictor": {},
    }
    thresholds: dict[str, float | None] = {}
    for name, corr in agreement.items():
        computed = simleague.persistence_mde(len(scored), round(baseline_r, 4), corr)
        floor = computed.get("mde80")
        threshold = None if floor is None else round(float(floor) * design_effect, 4)
        thresholds[name] = threshold
        power["by_predictor"][name] = {
            "predictor_agreement": corr,
            "mde80_independent": floor,
            "mde80_clustered": threshold,
        }

    verdicts = {}
    for name, gap in clustered["gaps"].items():
        delta, threshold = gap["delta_r"], thresholds.get(name)
        verdicts[name] = {
            **gap,
            "mde80": threshold,
            "clears_mde": (
                None
                if threshold is None or delta is None
                else bool(abs(delta) >= threshold and gap["excludes_zero"])
            ),
            "beats_baseline": (
                None
                if threshold is None or delta is None
                else bool(delta >= threshold and gap["excludes_zero"])
            ),
        }

    return {
        "n": len(scored),
        "scored_predictors": [p for p in names if p != BASELINE],
        "predictors": clustered["predictors"],
        "gaps": verdicts,
        "baseline_r": round(baseline_r, 4),
        "predictor_agreement": agreement,
        "power": power,
        "resampling": {
            "unit": evalspec.PRIMARY.unit,
            "clusters": len({o.player_id for o in scored}),
            "b": evalspec.BOOTSTRAP_B,
            "naive_gaps": naive["gaps"],
            "why": (
                "no series contains a whole player-season transition, so the player is the "
                "smallest cluster that does; the per-observation draw is published beside it"
            ),
        },
    }


def primary(panel: Sequence[Observation]) -> dict[str, Any]:
    """The one test the plan says a rating lives or dies on.

    Two blocks, because a fourth predictor cannot be added to a paired
    comparison without narrowing it. The gate runs on the rows that carry every
    predictor — a bootstrap that pairs nothing compares nothing — and SKILL
    exists only where a season-resolution filtered coefficient does, which is the
    CDL era. Scoring each predictor on its own maximal panel would let a
    predictor win by being measured somewhere easier.

    The three-way block beside it is the population the published figures were
    computed on. It is retained unchanged so that adding a predictor does not
    silently restate a number nobody re-derived, and each block reports its own
    *n* and cluster count.
    """
    four_way = _ordered([o for o in panel if o.openskill is not None and o.skill is not None])
    three_way = _ordered([o for o in panel if o.openskill is not None])
    # Not yet fitted is a legitimate state — the declaration is allowed to name a
    # predictor before the pipeline produces it — so the gate falls back to the
    # panel it can pair, and says which one it used.
    on_four_way = len(four_way) >= 50
    gated: Sequence[Observation] = four_way if on_four_way else three_way
    names: tuple[str, ...] = PREDICTORS if on_four_way else RETAINED
    if len(gated) < 50:
        return {"available": False, "reason": "too few transitions carry every predictor"}

    # A predictor the declaration names and the pipeline does not yet produce is
    # reported by name rather than by absence. The manifest may be extended ahead
    # of the model — that is the door P5 declared `skill` through — and the one
    # way that stops being honest is a declared predictor quietly never appearing
    # in a result. The gate reads this list.
    fitted = {name for name in names if name != BASELINE}
    not_yet_fitted = [p for p in evalspec.PRIMARY.predictors if p not in fitted]

    dropped = {
        "composite": 0,
        "openskill": sum(1 for o in panel if o.openskill is None),
        "skill": sum(1 for o in panel if o.skill is None),
    }
    return {
        "available": True,
        "declared": evalspec.PRIMARY.payload(),
        "not_yet_fitted": not_yet_fitted,
        "panel": (
            "every transition carrying all four predictors"
            if on_four_way
            else "every transition carrying the three predictors that exist"
        ),
        **_gate_block(gated, names),
        "n_panel": len(panel),
        "n_dropped_missing_a_predictor": len(panel) - len(gated),
        "dropped_by_predictor": {
            "what": "transitions the published test counts where this predictor has no value",
            **dropped,
        },
        "retained_three_way": {
            "what": (
                "the same comparison on the population the published figures were computed "
                "on, before a fourth predictor narrowed the shared panel. Retained so that "
                "adding SKILL does not restate a number nobody re-derived"
            ),
            "clusters": len({o.player_id for o in three_way}),
            **_gate_block(three_way, RETAINED),
        }
        if on_four_way and three_way
        else {"what": "not computed separately: the gate is already on this panel"},
    }


# ------------------------------------------------------------- reproduction


def _published_persistence(panel: Sequence[Observation]) -> dict[str, Any]:
    """The published 2x2, recomputed here from the same rows and the same seed.

    Deliberately a second implementation rather than a call into `holdout`: a
    reproduction that runs the code it is reproducing proves only that the code
    is deterministic.
    """
    cols = {
        "rating_now": [o.composite for o in panel],
        "kd_now": [o.kd for o in panel],
        "rating_next": [o.composite_next for o in panel],
        "kd_next": [o.kd_next for o in panel],
    }
    take = content_order([cols[k] for k in holdout.PERSISTENCE_COLUMNS])
    cols = {k: [v[i] for i in take] for k, v in cols.items()}

    n = len(panel)
    rng = np.random.default_rng(holdout.BOOTSTRAP_SEED)
    idx = rng.integers(0, n, size=(holdout.BOOTSTRAP_B, n)) if n >= 3 else np.zeros((0, 0), int)
    combos = (
        ("rating->rating", "rating_now", "rating_next"),
        ("rating->kd", "rating_now", "kd_next"),
        ("kd->rating", "kd_now", "rating_next"),
        ("kd->kd", "kd_now", "kd_next"),
    )
    draws: dict[str, list[float]] = {name: [] for name, _, _ in combos}
    for b in range(len(idx)):
        take_b = idx[b]
        for name, xk, yk in combos:
            draws[name].append(
                _pearson([cols[xk][i] for i in take_b], [cols[yk][i] for i in take_b])
            )
    cells = {name: {"n": n, "r": round(_pearson(cols[xk], cols[yk]), 4)} for name, xk, yk in combos}
    contrasts = {}
    for target in ("rating", "kd"):
        d = [
            x - y
            for x, y in zip(draws[f"rating->{target}"], draws[f"kd->{target}"], strict=True)
            if not (math.isnan(x) or math.isnan(y))
        ]
        lo, hi = _ci(d)
        contrasts[target] = {
            "delta_r": round(
                _pearson(cols["rating_now"], cols[f"{target}_next"])
                - _pearson(cols["kd_now"], cols[f"{target}_next"]),
                4,
            ),
            "lo": lo,
            "hi": hi,
        }
    return {"n_pairs": n, "cells": cells, "contrasts": contrasts}


def _compare(recomputed: dict[str, Any], stored: dict[str, Any]) -> list[dict[str, Any]]:
    """Every published cell of the persistence test, ours against theirs."""
    checks: list[dict[str, Any]] = []

    def check(what: str, got: Any, want: Any) -> None:
        ok = (
            got is not None
            and want is not None
            and abs(float(got) - float(want)) <= evalspec.REPRODUCTION_TOL
        )
        checks.append({"what": what, "harness": got, "published": want, "matches": bool(ok)})

    check("n_pairs", recomputed["n_pairs"], stored.get("n_pairs"))
    for name, cell in recomputed["cells"].items():
        check(f"cells.{name}.r", cell["r"], stored.get("cells", {}).get(name, {}).get("r"))
    for target, contrast in recomputed["contrasts"].items():
        published = stored.get("contrasts", {}).get(target, {})
        check(f"contrasts.{target}.delta_r", contrast["delta_r"], published.get("delta_r"))
        check(f"contrasts.{target}.lo", contrast["lo"], published.get("lo"))
        check(f"contrasts.{target}.hi", contrast["hi"], published.get("hi"))
    return checks


def reproduction(
    panel: Sequence[Observation],
    stored_persistence: dict[str, Any],
    stored_forecast: dict[str, Any],
    power: dict[str, Any],
    plusminus: dict[str, Any],
    gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The gate: recover what is already published, or score nothing new."""
    recomputed = _published_persistence(panel)
    checks = _compare(recomputed, stored_persistence)

    printed = evalspec.PUBLISHED_FIGURES
    page: list[dict[str, Any]] = []
    common = stored_forecast.get("common", {})
    scored = common.get("predictors", {}) if common.get("available") else {}
    page.append(
        {
            "what": "roster forecast, maps scored by every predictor",
            "run": common.get("n_maps"),
            "page": printed["forecast_maps"],
            "matches": bool(common.get("n_maps") == printed["forecast_maps"]),
        }
    )
    for name, want in printed["forecast_brier"].items():
        got = scored.get(name, {}).get("brier")
        page.append(
            {
                "what": f"roster forecast Brier, {name}",
                "run": got,
                "page": want,
                "matches": bool(got is not None and abs(got - want) <= printed["brier_tol"]),
            }
        )
    page.append(
        {
            "what": "persistence transitions",
            "run": recomputed["n_pairs"],
            "page": printed["persistence_pairs"],
            "matches": bool(recomputed["n_pairs"] == printed["persistence_pairs"]),
        }
    )
    page.append(
        {
            "what": "persistence contrast predicting next K/D z",
            "run": recomputed["contrasts"]["kd"]["delta_r"],
            "page": printed["persistence_delta_r"],
            "matches": bool(
                abs(recomputed["contrasts"]["kd"]["delta_r"] - printed["persistence_delta_r"])
                <= printed["delta_r_tol"]
            ),
        }
    )

    # The figures the page prints about the panel the *next* rating is gated on.
    # They belong here rather than in the manifest for the same reason the rest
    # of this block does: a page figure nothing compares against drifts, and PE
    # shipped with two screens of the same document disagreeing about the same
    # population because nothing was comparing them.
    skill = printed["skill_panel"]
    # The floor and the distance to it are read against the dated
    # re-measurement, not against the value pinned before the prior existed.
    # The pinned value stays the threshold the gate tests; this comparison is
    # asking whether the page shows what the run computes today.
    remeasured = printed.get("skill_panel_remeasured", skill)
    for what, got, want in (
        ("SKILL panel transitions", power.get("n_eligible"), skill["n"]),
        ("SKILL panel players", power.get("clusters"), skill["clusters"]),
        (
            "SKILL panel detectable gap",
            power.get("floors", {}).get("composite_measured", {}).get("mde80_clustered"),
            remeasured["mde80_clustered"],
        ),
        (
            "SKILL panel distance to clear",
            power.get("distance_to_clear"),
            remeasured["distance_to_clear"],
        ),
    ):
        page.append(
            {
                "what": what,
                "run": got,
                "page": want,
                "matches": bool(got is not None and abs(float(got) - float(want)) <= 5e-4),
            }
        )
    forward = printed["plusminus_forward"]
    for what, got, want in (
        ("plus-minus read forward, season resolution", plusminus.get("n"), forward["n"]),
        ("plus-minus forward r, season resolution", plusminus.get("rapm_filtered"), forward["r"]),
        (
            "plus-minus forward r, pooled over resolutions",
            plusminus.get("pooled_over_resolutions", {}).get("rapm_filtered"),
            forward["pooled_r"],
        ),
        (
            "plus-minus forward r, era resolution",
            plusminus.get("by_resolution", {}).get(statespace.ERA, {}).get("rapm_filtered"),
            forward["era_r"],
        ),
    ):
        page.append(
            {
                "what": what,
                "run": got,
                "page": want,
                "matches": bool(got is not None and abs(float(got) - float(want)) <= 5e-4),
            }
        )

    # And what the gate returned once the fourth predictor existed. Pinned for
    # the same reason as the floor above it: a published verdict that nothing
    # compares against is a verdict that drifts, and this one is the phase's
    # headline. The panel is smaller than the floor's because SKILL is predicted
    # from the seasons before it and the earliest season has none.
    result = printed["skill_result"]
    gap = (gate or {}).get("gaps", {}).get("skill", {})
    for what, got, want in (
        ("SKILL gate panel transitions", (gate or {}).get("n"), result["n"]),
        (
            "SKILL gate panel players",
            (gate or {}).get("resampling", {}).get("clusters"),
            result["clusters"],
        ),
        ("SKILL persistence gap", gap.get("delta_r"), result["delta_r"]),
        ("SKILL detectable gap on its own panel", gap.get("mde80"), result["mde80"]),
    ):
        page.append(
            {
                "what": what,
                "run": got,
                "page": want,
                "matches": bool(got is not None and abs(float(got) - float(want)) <= 5e-4),
            }
        )

    # The three-way panel and the adversary's row beside it. Both were printed
    # for phases with a live artifact behind them and no comparison, which is
    # how they went stale unnoticed while every pinned figure failed loudly.
    three_way = (gate or {}).get("retained_three_way", {})
    retained = printed["retained_three_way"]
    for what, got, want in (
        ("three-way panel transitions", three_way.get("n"), retained["n"]),
        (
            "three-way panel players",
            three_way.get("clusters"),
            retained["clusters"],
        ),
        (
            "three-way composite persistence gap",
            three_way.get("gaps", {}).get("composite", {}).get("delta_r"),
            retained["composite_delta_r"],
        ),
        (
            "three-way design effect",
            three_way.get("power", {}).get("design_effect"),
            retained["design_effect"],
        ),
        (
            "openskill gate persistence gap",
            (gate or {}).get("gaps", {}).get("openskill", {}).get("delta_r"),
            retained["openskill_gate_delta_r"],
        ),
    ):
        page.append(
            {
                "what": what,
                "run": got,
                "page": want,
                "matches": bool(got is not None and abs(float(got) - float(want)) <= 5e-4),
            }
        )

    return {
        "what": (
            "an independent recomputation of the published persistence test, and the "
            "published figures against the ones printed on /methodology"
        ),
        "tolerance": evalspec.REPRODUCTION_TOL,
        "recomputed": checks,
        "n_mismatched": sum(1 for c in checks if not c["matches"]),
        "against_the_page": page,
        "n_page_mismatched": sum(1 for c in page if not c["matches"]),
        "reproduces": all(c["matches"] for c in checks),
    }


# --------------------------------------------------------------- secondaries


def _plain_gaps(panel: Sequence[Observation]) -> dict[str, Any]:
    """Point correlations only. Secondary tests get no intervals and no verdicts."""
    if len(panel) < 20:
        return {"n": len(panel), "composite": None, "openskill": None, BASELINE: None}
    cols = _columns(panel)
    out: dict[str, Any] = {"n": len(panel)}
    for name in PREDICTORS:
        r = _pearson(cols[name], cols["target"])
        out[name] = None if math.isnan(r) else round(r, 4)
    return out


def _calibration(
    preds: dict[int, Prediction],
    rows: Sequence[MapRow],
    venue: dict[int, bool | None],
    era: dict[int, str],
) -> dict[str, Any]:
    """Observed against predicted, by decile, by era and by venue."""
    era_of: dict[int, str] = {}
    event_of: dict[int, int] = {}
    for r in rows:
        era_of[r.game_id] = era.get(r.season_id, "unknown")
        event_of[r.game_id] = r.event_id

    def block(items: Sequence[tuple[float, bool]]) -> dict[str, Any]:
        if not items:
            return {"n": 0}
        p = np.array([i[0] for i in items], dtype=float)
        y = np.array([1.0 if i[1] else 0.0 for i in items], dtype=float)
        return {
            "n": len(items),
            "predicted": round(float(p.mean()), 4),
            "observed": round(float(y.mean()), 4),
            "gap": round(float(y.mean() - p.mean()), 4),
        }

    deciles: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    eras: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    venues: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for game_id, p in preds.items():
        item = (p.p, p.won)
        deciles[f"{int(min(p.p, 0.999) * 10) / 10:.1f}"].append(item)
        eras[era_of.get(game_id, "unknown")].append(item)
        flag = venue.get(event_of.get(game_id, -1))
        venues["lan" if flag else ("online" if flag is False else "undecided")].append(item)
    return {
        "significance_claimed": False,
        "by_decile": {k: block(v) for k, v in sorted(deciles.items())},
        "by_era": {k: block(v) for k, v in sorted(eras.items())},
        "by_venue": {k: block(v) for k, v in sorted(venues.items())},
    }


def secondary(
    panel: Sequence[Observation],
    fit: skillbase.Fit,
    rows: Sequence[MapRow],
    venue: dict[int, bool | None],
    era: dict[int, str],
) -> dict[str, Any]:
    """The diagnostics, every one of them labelled as one."""
    scored = _ordered([o for o in panel if o.openskill is not None])

    titles = sorted({o.title_a for o in scored})
    by_title = {title: _plain_gaps([o for o in scored if o.title_a != title]) for title in titles}

    # Leave one event out of the map-level score rather than out of a
    # transition: an event's maps are spread across many transitions and none of
    # them can be recomputed without refitting the pipeline, while the map score
    # drops them exactly.
    event_of = {r.game_id: r.event_id for r in rows}
    per_event: dict[int, list[float]] = defaultdict(list)
    for game_id, p in fit.predictions.items():
        per_event[event_of.get(game_id, -1)].append((p.p - (1.0 if p.won else 0.0)) ** 2)
    total = float(sum(sum(v) for v in per_event.values()))
    n_all = sum(len(v) for v in per_event.values())
    withheld = []
    for event_id, errs in sorted(per_event.items()):
        left = n_all - len(errs)
        if left < 200:
            continue
        withheld.append(
            {"event_id": event_id, "n_left": left, "brier": round((total - sum(errs)) / left, 5)}
        )
    loeo = {
        "significance_claimed": False,
        "unit": evalspec.SERIES,
        "full_brier": round(total / n_all, 5) if n_all else None,
        "n_withheld": len(withheld),
        "brier_min": min((w["brier"] for w in withheld), default=None),
        "brier_max": max((w["brier"] for w in withheld), default=None),
    }

    return {
        "leave_one_title_out": {"significance_claimed": False, "by_withheld_title": by_title},
        "leave_one_event_out": loeo,
        "rookie_emergence": {
            "significance_claimed": False,
            "first_qualified_season": _plain_gaps([o for o in scored if o.rookie]),
            "later_seasons": _plain_gaps([o for o in scored if not o.rookie]),
        },
        "roster_move_shock": {
            "significance_claimed": False,
            "changed_team": _plain_gaps([o for o in scored if o.moved_team]),
            "stayed": _plain_gaps([o for o in scored if not o.moved_team]),
        },
        "calibration_by_bucket": _calibration(fit.predictions, rows, venue, era),
    }


def prior_target_persistence(
    panel: Sequence[Observation],
    coefficients: dict[tuple[int, int], float],
    resolutions: dict[tuple[int, int], str],
) -> dict[str, Any]:
    """SKILL read forward against the quantity it was fitted to predict.

    The primary test scores every rating against next season's era-adjusted K/D
    z, which is the baseline's own ground: a rating built to predict plus-minus
    is being asked to beat K/D z at being K/D z. This asks the other question —
    how well season N's rating predicts season N+1's filtered plus-minus — and it
    was declared in the manifest before the rating existed, so it cannot be read
    as a test found after a loss.

    It does not soften the gate and claims no significance. Its use is diagnostic:
    if SKILL fails the primary and wins here, the failure has a reason attached.

    Season resolution on the *target* side, for the reason the resolution column
    exists: an era coefficient is one estimate filed against each season it
    covers, so reading it forward is reading a number that cannot move across the
    transition being tested.
    """
    rows = [
        (o.skill, o.composite, o.kd, coefficients[(o.player_id, o.season_b)])
        for o in panel
        if o.skill is not None
        and (o.player_id, o.season_b) in coefficients
        and resolutions.get((o.player_id, o.season_b)) == statespace.SEASON
    ]
    base = {
        "significance_claimed": False,
        "what": "season N's rating against season N+1's filtered plus-minus coefficient",
        "target": "filtered plus-minus coefficient, season N+1",
        "resolution_read": statespace.SEASON,
        "n": len(rows),
    }
    if len(rows) < 20:
        return {**base, "reason": "too few transitions carry a SKILL rating and a forward target"}
    target = np.array([r[3] for r in rows], dtype=float)
    scores = {}
    for i, name in enumerate(("skill", "composite", BASELINE)):
        r = _pearson(np.array([row[i] for row in rows], dtype=float), target)
        scores[name] = None if math.isnan(r) else round(float(r), 4)
    return {**base, "predictors": scores}


def _floor_over(
    eligible: Sequence[Observation], baseline_r: float, design_effect: float, agreement: float
) -> dict[str, Any]:
    computed = simleague.persistence_mde(len(eligible), round(baseline_r, 4), agreement)
    floor = computed.get("mde80")
    return {
        "predictor_agreement": agreement,
        "mde80_independent": floor,
        "mde80_clustered": None if floor is None else round(float(floor) * design_effect, 4),
    }


def skill_power(
    panel: Sequence[Observation], resolutions: dict[tuple[int, int], str]
) -> dict[str, Any]:
    """The floor P5's gate will be judged against, computed before P5 exists.

    A SKILL rating can only be scored where a filtered coefficient exists, and
    the resolution of that coefficient decides whether the row belongs in a
    forward test at all. **A season coefficient is a season's estimate; an era
    coefficient is one estimate filed against each of the three seasons it
    covers**, so for a CWL player it is the same number in 2017, 2018 and 2019.
    A rating built on it does not vary across the transition being read forward
    for any reason the estimate supplies, and counting those rows would widen the
    panel with observations that cannot carry the quantity under test.

    So the gate panel is the season-resolution one — the CDL era, which is the
    only era the identification pre-flight allowed a season on — and the wider,
    era-inclusive panel is published beside it rather than used. The difference
    is not small and it is the whole reason this is measured rather than
    inherited: 553 rows against 264, and a floor that moves with them.

    Everything here is computable without SKILL: the panel is identified by
    coverage, the design effect by the predictors already on it, and the
    detectable gap by the closed form at a stated agreement. Computing it now is
    the point. A threshold read off the same run that first reports a result is
    a threshold chosen after seeing one, and the gate that reads this artifact
    checks the run it was written in came first.
    """
    scorable = [o for o in panel if o.openskill is not None]
    eligible = _ordered(
        [o for o in scorable if resolutions.get((o.player_id, o.season_a)) == statespace.SEASON]
    )
    wider = _ordered([o for o in scorable if (o.player_id, o.season_a) in resolutions])
    dropped = {
        "no_openskill": sum(1 for o in panel if o.openskill is None),
        "no_filtered_coefficient": sum(
            1 for o in panel if (o.player_id, o.season_a) not in resolutions
        ),
        "era_resolution_only": len(wider) - len(eligible),
    }
    if len(eligible) < 50:
        return {
            "available": False,
            "reason": "too few transitions could carry a SKILL rating",
            "n_panel": len(panel),
            "n_eligible": len(eligible),
            "dropped": dropped,
        }

    cols = _columns(eligible)
    baseline_r = _pearson(cols[BASELINE], cols["target"])
    clustered = _persistence_stats(eligible, by_cluster=True)
    naive = _persistence_stats(eligible, by_cluster=False)
    effects = [
        clustered["gaps"][name]["se"] / naive["gaps"][name]["se"]
        for name in clustered["gaps"]
        if clustered["gaps"][name]["se"] and naive["gaps"][name]["se"]
    ]
    design_effect = float(np.median(effects)) if effects else 1.0

    # A curve rather than a point, because SKILL's agreement with the baseline
    # cannot be known before SKILL is fitted and the floor depends on it: a
    # predictor that closely tracks K/D z is compared against it far more
    # precisely than one that does not. The composite's measured agreement is
    # the anchor; the two hypotheticals bracket the range a new rating could
    # land in.
    agreements = {
        "composite_measured": round(float(_pearson(cols["composite"], cols[BASELINE])), 4),
        "hypothetical_loose": 0.3,
        "hypothetical_tight": 0.8,
    }
    floors = {
        label: _floor_over(eligible, baseline_r, design_effect, corr)
        for label, corr in agreements.items()
    }

    composite_gap = clustered["gaps"]["composite"]["delta_r"]
    anchor = floors["composite_measured"]["mde80_clustered"]
    distance = (
        None
        if composite_gap is None or anchor is None
        else round(float(anchor) - float(composite_gap), 4)
    )
    clusters = len({o.player_id for o in eligible})
    return {
        "available": True,
        "what": (
            "the smallest persistence gap the SKILL-eligible panel can resolve at 80% power, "
            "computed before any SKILL rating exists so the threshold cannot be chosen after "
            "the result"
        ),
        "n_panel": len(panel),
        "n_eligible": len(eligible),
        "dropped": dropped,
        "clusters": clusters,
        "unit": evalspec.PRIMARY.unit,
        "wider_panel": {
            "what": (
                "the same floor if era-resolution coefficients counted, which they do not: "
                "one estimate filed against each season it covers cannot vary across a "
                "transition, so these rows are reported and not used"
            ),
            "n": len(wider),
            "clusters": len({o.player_id for o in wider}),
            "floor": _floor_over(
                wider,
                _pearson(_columns(wider)[BASELINE], _columns(wider)["target"]),
                design_effect,
                agreements["composite_measured"],
            ),
        },
        "eligibility": (
            "carries every predictor of the published test and a filtered coefficient at "
            "season resolution on the season being read forward"
        ),
        "baseline_r": round(baseline_r, 4),
        "design_effect": round(design_effect, 3),
        "floors": floors,
        "composite_gap_here": composite_gap,
        "distance_to_clear": distance,
        "statement": (
            f"on {len(eligible)} transitions over {clusters} players, a gap smaller than "
            f"{anchor} is not resolvable; the composite sits at {composite_gap} here, so a "
            f"SKILL rating has to move {distance} to clear the gate"
        ),
    }


def forward_coefficients(
    conn: Conn, run_id: int, scope: str = evalspec.SCOPE
) -> dict[tuple[int, int], float]:
    """The one door a forward test reads plus-minus coefficients through.

    Every caller states the scope it wants and the manifest's rule decides
    whether it may have it. Asking for the smoothed family raises here rather
    than returning coefficients that have already seen the season being
    predicted — the contamination arrives through the random-walk penalty rather
    than through a column, so nothing downstream would catch it.
    """
    evalspec.assert_forward(scope)
    rows = conn.execute(
        "SELECT player_id, season_id, coef FROM player_rapm"
        " WHERE run_id = %s AND scope = %s AND season_id IS NOT NULL",
        (run_id, scope),
    ).fetchall()
    return {(cast(int, r[0]), cast(int, r[1])): cast(float, r[2]) for r in rows}


def forward_resolutions(
    conn: Conn, run_id: int, scope: str = evalspec.SCOPE
) -> dict[tuple[int, int], str]:
    """What each of those coefficients actually covers: a season, or a whole era.

    Read alongside the coefficients rather than folded into them, because the two
    answer different questions and only one of them is a number. A row filed
    against 2018 at era resolution is the same estimate as the rows filed against
    2017 and 2019, and a forward test that cannot tell them apart is reading one
    measurement three times.
    """
    evalspec.assert_forward(scope)
    rows = conn.execute(
        "SELECT player_id, season_id, resolution FROM player_rapm"
        " WHERE run_id = %s AND scope = %s AND season_id IS NOT NULL",
        (run_id, scope),
    ).fetchall()
    return {(cast(int, r[0]), cast(int, r[1])): str(r[2]) for r in rows}


def season_plusminus_persistence(
    panel: Sequence[Observation],
    coefficients: dict[tuple[int, int], float],
    scope: str,
    resolutions: dict[tuple[int, int], str],
) -> dict[str, Any]:
    """The filtered plus-minus read forward, and the scope it was read at.

    Split by resolution, and the split is a correction rather than a refinement.
    The first version of this pooled every stored coefficient and reported one
    correlation over 553 cells; 122 of those were era-resolution rows, where one
    estimate is filed against each of the three seasons it covers, so the same
    number entered the test up to three times per player and could not move
    across the transition it was being read forward through. The pooled figure is
    still reported — it is what a reader who joins the table naively will get —
    but the season-resolution figure is the one that answers the question.
    """
    rows = [
        (
            coefficients[(o.player_id, o.season_a)],
            o.kd_next,
            o.kd,
            resolutions.get((o.player_id, o.season_a), statespace.ERA),
        )
        for o in panel
        if (o.player_id, o.season_a) in coefficients
    ]
    if len(rows) < 20:
        return {
            "significance_claimed": False,
            "scope_read": scope,
            "n": len(rows),
            "reason": "too few cells carry a filtered coefficient",
        }

    def block(subset: list[tuple[float, float, float, str]]) -> dict[str, Any]:
        if len(subset) < 20:
            return {"n": len(subset), "rapm_filtered": None, BASELINE: None}
        return {
            "n": len(subset),
            "rapm_filtered": round(_pearson([r[0] for r in subset], [r[1] for r in subset]), 4),
            BASELINE: round(_pearson([r[2] for r in subset], [r[1] for r in subset]), 4),
        }

    seasonal = [r for r in rows if r[3] == statespace.SEASON]
    return {
        "significance_claimed": False,
        "scope_read": scope,
        "resolution_read": statespace.SEASON,
        **block(seasonal),
        "pooled_over_resolutions": {
            "what": (
                "every stored coefficient, era-resolution rows included. An era coefficient is "
                "one estimate filed against each season it covers, so these rows repeat a "
                "number rather than supplying one per season"
            ),
            **block(rows),
        },
        "by_resolution": {
            statespace.ERA: block([r for r in rows if r[3] == statespace.ERA]),
        },
    }


def artifacts(
    conn: Conn,
    rating_run_id: int,
    era_run_id: int,
    season_rapm_run_id: int,
    rows: Sequence[MapRow],
    fit: skillbase.Fit,
    stored_persistence: dict[str, Any],
    stored_forecast: dict[str, Any],
    games: Sequence[rapm.AdmittedMap],
    skill: dict[tuple[int, int], float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Every payload the harness publishes, in the order they have to be trusted."""
    panel = build_panel(conn, rating_run_id, era_run_id, rows, fit.season_skill, skill)
    ordered = _ordered(panel)
    forward = forward_coefficients(conn, season_rapm_run_id)
    resolutions = forward_resolutions(conn, season_rapm_run_id)

    secondaries = secondary(panel, fit, rows, venue_flags(conn), eras(conn))
    plusminus = season_plusminus_persistence(panel, forward, evalspec.SCOPE, resolutions)
    secondaries["season_plusminus_persistence"] = plusminus
    secondaries["prior_target_persistence"] = prior_target_persistence(panel, forward, resolutions)
    power = skill_power(panel, resolutions)

    gate = primary(panel)
    return {
        "evaluation_manifest": {
            **evalspec.manifest(),
            "sha256": evalspec.sha256(),
            "pinned_sha256": evalspec.PINNED_SHA256,
            "matches_pin": evalspec.sha256() == evalspec.PINNED_SHA256,
            "pinned_invariants_sha256": evalspec.PINNED_INVARIANTS_SHA256,
            "matches_invariants_pin": (
                evalspec.invariants_sha256() == evalspec.PINNED_INVARIANTS_SHA256
            ),
        },
        "evaluation_reproduction": reproduction(
            ordered, stored_persistence, stored_forecast, power, plusminus, gate
        ),
        "evaluation_primary": gate,
        "skill_power": power,
        "evaluation_secondary": secondaries,
        "evaluation_placebo": placebo.suite(
            games,
            [o.composite for o in ordered],
            [o.kd_next for o in ordered],
        ),
    }


def headline(payload: dict[str, Any]) -> str:
    """One line for the run log: what the primary test said."""
    block = payload["evaluation_primary"]
    if not block.get("available"):
        return f"primary test unavailable: {block.get('reason')}"
    parts = []
    for name, gap in block["gaps"].items():
        parts.append(f"{name} {gap['delta_r']:+.4f} [{gap['lo']:+.4f}, {gap['hi']:+.4f}]")
    return (
        f"persistence over {block['n']} transitions against {BASELINE} "
        f"(r={block['baseline_r']:.3f}): " + "; ".join(parts)
    )
