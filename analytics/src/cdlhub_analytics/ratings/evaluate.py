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
from ..maprows import MapRow
from ..resample import order as content_order
from ..resample import stream
from . import evalspec, holdout, placebo, rapm, simleague, skillbase
from . import player_rating as pr

Conn = psycopg.Connection[tuple[object, ...]]

# The predictors of the primary test, in the order they are reported. `kd_z` is
# the baseline every gap is taken against.
BASELINE = "kd_z"
PREDICTORS = ("composite", "openskill", BASELINE)

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
    skill: dict[tuple[int, int], float],
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
    ordered = sorted(seasons, key=lambda s: seasons[s]["year"])
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
                    skill=skill.get((pid, a)),
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
        "openskill": np.array([o.skill if o.skill is not None else np.nan for o in panel]),
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


def _persistence_stats(panel: Sequence[Observation], by_cluster: bool) -> dict[str, Any]:
    """Every predictor's correlation with the target, and its gap to the baseline."""
    cols = _columns(panel)
    draws: dict[str, list[float]] = {name: [] for name in PREDICTORS}
    for take in _draw(panel, evalspec.BOOTSTRAP_B, by_cluster):
        for name in PREDICTORS:
            draws[name].append(_pearson(cols[name][take], cols["target"][take]))

    per: dict[str, Any] = {}
    for name in PREDICTORS:
        r = _pearson(cols[name], cols["target"])
        lo, hi = _ci(draws[name])
        per[name] = {"r": None if math.isnan(r) else round(r, 4), "lo": lo, "hi": hi}

    gaps: dict[str, Any] = {}
    base_r = _pearson(cols[BASELINE], cols["target"])
    for name in PREDICTORS:
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


def primary(panel: Sequence[Observation]) -> dict[str, Any]:
    """The one test the plan says a rating lives or dies on."""
    scored = _ordered([o for o in panel if o.skill is not None])
    if len(scored) < 50:
        return {"available": False, "reason": "too few transitions carry every predictor"}

    clustered = _persistence_stats(scored, by_cluster=True)
    naive = _persistence_stats(scored, by_cluster=False)
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
        for name in PREDICTORS
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
        "available": True,
        "declared": evalspec.PRIMARY.payload(),
        "n": len(scored),
        "n_dropped_no_openskill": len(panel) - len(scored),
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
    scored = _ordered([o for o in panel if o.skill is not None])

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


def season_plusminus_persistence(
    panel: Sequence[Observation], coefficients: dict[tuple[int, int], float], scope: str
) -> dict[str, Any]:
    """The filtered plus-minus read forward, and the scope it was read at."""
    paired = [
        (coefficients[(o.player_id, o.season_a)], o.kd_next, o.kd)
        for o in panel
        if (o.player_id, o.season_a) in coefficients
    ]
    if len(paired) < 20:
        return {
            "significance_claimed": False,
            "scope_read": scope,
            "n": len(paired),
            "reason": "too few cells carry a filtered coefficient",
        }
    return {
        "significance_claimed": False,
        "scope_read": scope,
        "n": len(paired),
        "rapm_filtered": round(_pearson([p[0] for p in paired], [p[1] for p in paired]), 4),
        BASELINE: round(_pearson([p[2] for p in paired], [p[1] for p in paired]), 4),
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
) -> dict[str, dict[str, Any]]:
    """Every payload the harness publishes, in the order they have to be trusted."""
    panel = build_panel(conn, rating_run_id, era_run_id, rows, fit.season_skill)
    ordered = _ordered(panel)

    secondaries = secondary(panel, fit, rows, venue_flags(conn), eras(conn))
    secondaries["season_plusminus_persistence"] = season_plusminus_persistence(
        panel, forward_coefficients(conn, season_rapm_run_id), evalspec.SCOPE
    )

    return {
        "evaluation_manifest": {
            **evalspec.manifest(),
            "sha256": evalspec.sha256(),
            "pinned_sha256": evalspec.PINNED_SHA256,
            "matches_pin": evalspec.sha256() == evalspec.PINNED_SHA256,
        },
        "evaluation_reproduction": reproduction(ordered, stored_persistence, stored_forecast),
        "evaluation_primary": primary(panel),
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
