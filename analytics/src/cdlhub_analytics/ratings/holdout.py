"""Two tests the player rating can actually fail.

The map backtest cannot fail badly, because several of its features are the win
condition (see `leakage.py`). Both tests here predict something that has not
happened yet, so no feature can contain its answer.

**Test A — does the rating persist?** For every player with two consecutive
seasons, use season *N* to predict season *N+1*. Two predictors are compared,
the composite rating and the era-adjusted K/D z-score, against two targets, the
same pair one season later. That 2×2 shape is deliberate: predicting next
season's rating flatters the rating, and predicting next season's K/D flatters
K/D, so only the off-diagonal cells are informative. If the composite beats raw
K/D at predicting *next season's K/D*, it has won on K/D's own ground.

**Test B — does a roster predict future map wins?** Walk-forward by event within
each season: at every event, the whole rating pipeline is refit on maps from
earlier events only, players are summed to a roster strength per team for the
map's mode, and the differential is turned into a win probability by a logistic
also fit on those earlier maps. Nothing from the event being predicted enters.

Test B's baselines matter more than its absolute score. Predicting a map winner
at 50% is the floor; the question is whether player-level information beats what
was already known, so the same harness scores a roster summed from era-adjusted
K/D z instead, and the published Glicko-2 team rating going into that series.
Glicko-2 is the honest bar: if knowing which four players are on the server adds
nothing over knowing which team it is, that is a result and it gets published.

One of Test B's arms is not a baseline but a predecessor: `rating_zshrink` is the
same rating estimated the old way, z-scored against the observed spread and shrunk
by m/(m+k), fitted on identical prefixes. Replacing an estimator because it is
better specified is a defensible move on its own, but whether it forecasts better
is a question with an answer, and this is where the answer comes from.

Test B also scores the two RAPM variants (`rapm.py`), which measure player value
in map wins without touching the box score. They are the natural answer to "does
the box-score rating actually track winning?", and putting them through this
harness rather than their own means the comparison happens on identical maps
with paired intervals instead of across two tables.

Both artifacts are stored with the published rating run.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, cast

import numpy as np
import psycopg

from ..backtest import Prediction, evaluate
from ..era import MIN_MAPS
from ..maprows import Coverage, MapRow
from ..regress import fit_logistic_l2
from . import hierarchical as hier
from . import player_rating as pr
from . import rapm as rapm_mod

# Test A: a player-season needs this many maps on both sides of a transition to
# enter. The rating's own qualification floor, so the test asks about seasons the
# site actually publishes rather than about noise.
MIN_MAPS_EACH_SIDE = MIN_MAPS

# Test A: bootstrap draws for the correlation intervals. Resampling players, not
# maps — the question is how well this generalizes to other players.
BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20250725

# Test B: an event is predicted only once its cohort has this much prior history.
# Same constant the published walk-forward uses, for the same reason.
MIN_TRAIN_MAPS = pr.MIN_TRAIN_GAMES

# Test B's roster-shaped predictors, in the order they are reported. Glicko-2 is
# not here because it needs no prefix fit — its pre-series state comes straight
# off the published run.
PREDICTORS = ("rating", "rating_zshrink", "kd", "rapm", "rapm_prior")

# Two-sided 5% at 80% power, the same convention `significance.py` uses.
POWER_FACTOR = 1.959964 + 0.841621


# ----------------------------------------------------------------- test A


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    x, y = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    if len(x) < 3 or x.std() == 0.0 or y.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def _ci(draws: Sequence[float]) -> tuple[float | None, float | None]:
    kept = [d for d in draws if not math.isnan(d)]
    if not kept:
        return (None, None)
    lo, hi = np.percentile(kept, [2.5, 97.5])
    return (round(float(lo), 4), round(float(hi), 4))


def persistence(
    conn: psycopg.Connection[tuple[object, ...]], rating_run_id: int, era_run_id: int
) -> dict[str, Any]:
    """Test A: season N predictors against season N+1 targets, as a 2×2."""
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

    seasons, _modes = pr.label_context(conn)
    order = sorted(seasons, key=lambda s: seasons[s]["year"])
    transitions = list(zip(order, order[1:], strict=False))

    # A player-season enters only if both quantities exist on both sides, so all
    # four cells are computed on exactly the same players. Kept as four aligned
    # columns rather than four pair lists, so the contrasts below can resample
    # players once and read every correlation off the same draw.
    cols: dict[str, list[float]] = defaultdict(list)
    per_transition = []
    for a, b in transitions:
        used = 0
        for pid, season in sorted(rating):
            if season != a:
                continue
            keys = [(pid, a), (pid, b)]
            if not all(k in rating and k in kd for k in keys):
                continue
            if any(min(rating[k][0], kd[k][0]) < MIN_MAPS_EACH_SIDE for k in keys):
                continue
            used += 1
            cols["rating_now"].append(rating[(pid, a)][1])
            cols["kd_now"].append(kd[(pid, a)][1])
            cols["rating_next"].append(rating[(pid, b)][1])
            cols["kd_next"].append(kd[(pid, b)][1])
        per_transition.append(
            {
                "from_year": seasons[a]["year"],
                "from_title": seasons[a]["title"],
                "to_year": seasons[b]["year"],
                "to_title": seasons[b]["title"],
                "n": used,
            }
        )

    n = len(cols["rating_now"])
    combos = [
        ("rating->rating", "rating_now", "rating_next"),
        ("rating->kd", "rating_now", "kd_next"),
        ("kd->rating", "kd_now", "rating_next"),
        ("kd->kd", "kd_now", "kd_next"),
    ]
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, n, size=(BOOTSTRAP_B, n)) if n >= 3 else np.zeros((0, 0), dtype=int)

    # One resample of players, every correlation recomputed on it. Paired by
    # construction, which is what makes the contrasts below legitimate: comparing
    # two independent CIs that happen to overlap answers nothing.
    draws: dict[str, list[float]] = {name: [] for name, _, _ in combos}
    for b in range(len(idx)):
        take = idx[b]
        for name, xk, yk in combos:
            draws[name].append(_pearson([cols[xk][i] for i in take], [cols[yk][i] for i in take]))

    cells = {}
    for name, xk, yk in combos:
        r = _pearson(cols[xk], cols[yk])
        lo, hi = _ci(draws[name])
        cells[name] = {
            "n": n,
            "r": None if math.isnan(r) else round(r, 4),
            "lo": lo,
            "hi": hi,
        }

    # The two comparisons that decide the test, each on its own target so the
    # predictors are judged against the same thing.
    contrasts = {}
    for label, target in (("predicting next rating", "rating"), ("predicting next K/D", "kd")):
        a_name, b_name = f"rating->{target}", f"kd->{target}"
        d = [x - y for x, y in zip(draws[a_name], draws[b_name], strict=True)]
        d = [v for v in d if not math.isnan(v)]
        point = _pearson(cols["rating_now"], cols[f"{target}_next"]) - _pearson(
            cols["kd_now"], cols[f"{target}_next"]
        )
        lo, hi = _ci(d)
        contrasts[target] = {
            "what": f"{label}: r(rating) − r(K/D z)",
            "delta_r": None if math.isnan(point) else round(point, 4),
            "lo": lo,
            "hi": hi,
            # A CI clear of zero is a real difference; one spanning zero is not.
            "excludes_zero": bool(lo is not None and hi is not None and (lo > 0.0 or hi < 0.0)),
        }

    return {
        "min_maps_each_side": MIN_MAPS_EACH_SIDE,
        "bootstrap_b": BOOTSTRAP_B,
        "n_pairs": n,
        "transitions": per_transition,
        "cells": cells,
        "contrasts": contrasts,
    }


# ----------------------------------------------------------------- test B


def _event_order(rows: Sequence[MapRow]) -> list[int]:
    first_seen: dict[int, Any] = {}
    for r in rows:
        prev = first_seen.get(r.event_id)
        if prev is None or r.played_at < prev:
            first_seen[r.event_id] = r.played_at
    return sorted(first_seen, key=lambda e: (first_seen[e], e))


def _roster_strength(
    members: Sequence[MapRow], strength: dict[tuple[int, int, int | None], float], mode_id: int
) -> float | None:
    """Mean prior strength of the players on this team for this map's mode.

    Mean, not sum: a map where one player's prior rating is missing would
    otherwise score the whole team low for a data gap. Falls back to the
    all-mode blend when a player has no history in this specific mode, and
    returns None when the team has no rated player at all.
    """
    vals = []
    for m in members:
        v = strength.get((m.player_id, m.season_id, mode_id))
        if v is None:
            v = strength.get((m.player_id, m.season_id, None))
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    return float(np.mean(vals))


def fit_prefix_arms(
    rows: Sequence[MapRow], coverage: Coverage, version: str
) -> dict[str, dict[tuple[int, int, int | None], float]]:
    """Both estimators' ratings from one shared fit of the prefix.

    The cohorts, the learned weights and the season profiles are identical for
    the two arms — only the step that turns a profile into a rating differs — so
    fitting them separately would double the work and invite them to drift.
    """
    fitted = pr.prepare(rows, coverage, version)
    if not fitted.fits:
        return dict.fromkeys(pr.ESTIMATORS, {})
    models = hier.build_models(fitted.aggs, fitted.fits, fitted.scales)
    arms = {
        "hierarchical": hier.compute_ratings(fitted.aggs, fitted.fits, fitted.scales, models),
        "z_shrink": pr.compute_ratings(fitted.aggs, fitted.fits, fitted.scales, bootstrap=False),
    }
    return {
        name: {(r.player_id, r.season_id, r.mode_id): r.rating for r in ratings}
        for name, ratings in arms.items()
    }


def fit_prefix(
    rows: Sequence[MapRow],
    coverage: Coverage,
    version: str,
    estimator: str = pr.PUBLISHED_ESTIMATOR,
) -> dict[tuple[int, int, int | None], float]:
    """Rate every player on the maps given, keyed (player, season, mode)."""
    return fit_prefix_arms(rows, coverage, version)[estimator]


def _kd_prefix(rows: Sequence[MapRow]) -> dict[tuple[int, int, int | None], float]:
    """The same shape from kills and deaths alone: the box-score comparison.

    Deliberately crude — a per-mode and all-mode K/D per player over the prefix,
    shrunk toward 1.0 in the rating's own m/(m+k) form so that small samples are
    handled the same way and the comparison is about *what* is measured, not how
    it is stabilized. It keeps the fixed constant rather than the rating's
    per-cohort estimate: that estimate is a property of the composite's score
    variance, and borrowing it would make the baseline depend on the model it
    exists to challenge.
    """
    tally: dict[tuple[int, int, int | None], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in rows:
        k, d = r.values.get("kills"), r.values.get("deaths")
        if k is None or d is None:
            continue
        keys: tuple[tuple[int, int, int | None], ...] = (
            (r.player_id, r.season_id, r.mode_id),
            (r.player_id, r.season_id, None),
        )
        for key in keys:
            t = tally[key]
            t[0] += k
            t[1] += d
            t[2] += 1
    out: dict[tuple[int, int, int | None], float] = {}
    for key, (kills, deaths, maps) in tally.items():
        if deaths <= 0:
            continue
        # Centre on 1.0 so it shares the rating's scale and shrinkage form.
        out[key] = 1.0 + (kills / deaths - 1.0) * maps / (maps + pr.SHRINK_FALLBACK)
    return out


def _glicko_pre(
    conn: psycopg.Connection[tuple[object, ...]], glicko_run_id: int
) -> dict[tuple[int, int], float]:
    """(series, team) -> Glicko-2 rating going into that series, walk-forward."""
    rows = conn.execute(
        "SELECT series_id, team_id, rating_pre FROM team_ratings WHERE run_id = %s",
        (glicko_run_id,),
    ).fetchall()
    return {(cast(int, r[0]), cast(int, r[1])): cast(float, r[2]) for r in rows}


def _series_of_games(
    conn: psycopg.Connection[tuple[object, ...]],
) -> dict[int, int]:
    rows = conn.execute("SELECT id, series_id FROM games").fetchall()
    return {cast(int, r[0]): cast(int, r[1]) for r in rows}


def _logistic_1d(x: Sequence[float], y: Sequence[bool]) -> tuple[float, float] | None:
    """Intercept and slope for one feature, by the project's own IRLS ridge."""
    if len(x) < 20 or len(set(y)) < 2:
        return None
    arr = np.asarray(x, dtype=float).reshape(-1, 1)
    sd = arr.std(ddof=1)
    if sd == 0.0:
        return None
    mu = float(arr.mean())
    fit = fit_logistic_l2((arr - mu) / sd, np.asarray([1.0 if v else 0.0 for v in y]), l2=1.0)
    # Fold the standardization back in so the caller can apply it to raw diffs.
    slope = float(fit.weights[0]) / float(sd)
    return (float(fit.intercept) - slope * mu, slope)


def roster_forecast(
    conn: psycopg.Connection[tuple[object, ...]],
    version: str,
    glicko_run_id: int | None,
    rows: Sequence[MapRow] | None = None,
    coverage: Coverage | None = None,
) -> dict[str, Any]:
    """Test B: walk-forward by event, roster strength against the map winner."""
    if rows is None or coverage is None:
        rows, coverage = pr.load(conn)
    usable = [r for r in rows if pr.usable(r)]

    games_series = _series_of_games(conn)
    glicko = _glicko_pre(conn, glicko_run_id) if glicko_run_id else {}

    by_season: dict[int, list[MapRow]] = defaultdict(list)
    for r in usable:
        by_season[r.season_id].append(r)

    # Keyed by map id so the common-map intersection below is exact.
    preds: dict[str, dict[int, Prediction]] = defaultdict(dict)
    skipped_no_history = 0
    skipped_no_roster = 0
    per_season = []

    for season_id, season_rows in sorted(by_season.items()):
        events = _event_order(season_rows)
        season_counts = dict.fromkeys(PREDICTORS, 0)
        for i, event_id in enumerate(events):
            train = [r for r in season_rows if r.event_id in events[:i]]
            test = [r for r in season_rows if r.event_id == event_id]
            if len({r.game_id for r in train}) < MIN_TRAIN_MAPS:
                skipped_no_history += len({r.game_id for r in test})
                continue

            arms = fit_prefix_arms(train, coverage, version)
            rating_table = arms[pr.PUBLISHED_ESTIMATOR]
            # RAPM is refit on the same prefix, so it is held to exactly the
            # rule the rating is: nothing from the event being scored, and no
            # ridge strength chosen by looking at it.
            strength = {
                "rating": rating_table,
                # The estimator the published one replaced, on the same maps and
                # the same fit. If posterior ratings forecast future map wins no
                # better than z-and-shrink did, that is the honest reading of the
                # change and it belongs in the artifact.
                "rating_zshrink": arms["z_shrink"],
                "kd": _kd_prefix(train),
                "rapm": rapm_mod.prefix(train),
                "rapm_prior": rapm_mod.prefix(
                    train, prior=rapm_mod.rating_prior(train, rating_table)
                ),
            }

            # The differential -> probability map is itself fit on the prefix,
            # so the calibration is never borrowed from the maps being scored.
            calib: dict[str, tuple[float, float] | None] = {}
            for name, table in strength.items():
                xs, ys = [], []
                for members in _group_maps(train):
                    d = _map_diff(members, table)
                    if d is not None:
                        xs.append(d[0])
                        ys.append(d[1])
                calib[name] = _logistic_1d(xs, ys)

            for members in _group_maps(test):
                game_id = members[0].game_id
                for name, table in strength.items():
                    ab = calib[name]
                    d = _map_diff(members, table)
                    if ab is None or d is None:
                        if name == "rating":
                            skipped_no_roster += 1
                        continue
                    diff, a_won = d
                    p = 1.0 / (1.0 + math.exp(-(ab[0] + ab[1] * diff)))
                    preds[name][game_id] = Prediction(p=p, won=a_won, when=members[0].played_at)
                    season_counts[name] += 1

                # Glicko-2 needs no prefix fit: rating_pre is already the state
                # before the series, from the published run.
                if glicko:
                    g = _glicko_map(members, games_series, glicko)
                    if g is not None:
                        preds["glicko"][game_id] = Prediction(
                            p=g[0], won=g[1], when=members[0].played_at
                        )
                        season_counts["glicko"] = season_counts.get("glicko", 0) + 1

        per_season.append(
            {
                "season_id": season_id,
                "n_events": len(events),
                "n_scored": season_counts["rating"],
            }
        )

    seasons, _modes = pr.label_context(conn)
    for s in per_season:
        s["year"] = seasons[s["season_id"]]["year"]
        s["title"] = seasons[s["season_id"]]["title"]

    return {
        "version": version,
        "min_train_maps": MIN_TRAIN_MAPS,
        "skipped_no_history": skipped_no_history,
        "skipped_no_roster": skipped_no_roster,
        "per_season": per_season,
        "predictors": {
            name: _report(list(preds[name].values()))
            for name in (*PREDICTORS, "glicko")
            if preds[name]
        },
        # A map winner guessed at 0.5 every time. Printed because a Brier score
        # near this number is the whole result, and a reader should not have to
        # remember that 0.25 is the floor.
        "coin_flip_brier": 0.25,
        # Scored on the maps every predictor reached, for the same reason the
        # feature-set comparison does it.
        "common": _common_scored(preds),
        "contrasts": _brier_contrasts(preds),
    }


def _brier_contrasts(preds: dict[str, dict[int, Prediction]]) -> dict[str, Any]:
    """Paired bootstrap over maps for the Brier gaps that matter.

    The gaps here are small enough that reporting them without an interval would
    invite exactly the "you are reading noise" objection this project raises about
    other people's tables. Maps are resampled once per draw and every contrast is
    read off that same draw, so the pairing survives.
    """
    named = {k: v for k, v in preds.items() if v}
    if "rating" not in named or len(named) < 2:
        return {"available": False, "reason": "nothing to compare the rating against"}
    common = sorted(set.intersection(*(set(v) for v in named.values())))
    if len(common) < 50:
        return {"available": False, "reason": "too few commonly scored maps"}

    # Per-map squared error, aligned across predictors.
    err = {
        name: np.array(
            [(named[name][g].p - (1.0 if named[name][g].won else 0.0)) ** 2 for g in common]
        )
        for name in named
    }
    flip = np.array([(0.5 - (1.0 if named["rating"][g].won else 0.0)) ** 2 for g in common])
    err["coin_flip"] = flip

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, len(common), size=(BOOTSTRAP_B, len(common)))
    out: dict[str, Any] = {"available": True, "n_maps": len(common), "bootstrap_b": BOOTSTRAP_B}
    against = [
        n for n in ("rating_zshrink", "kd", "glicko", "rapm", "rapm_prior", "coin_flip") if n in err
    ]
    brier: dict[str, Any] = {}
    for name in against:
        d = err["rating"] - err[name]  # negative means the rating is better
        draws = [float(d[idx[b]].mean()) for b in range(BOOTSTRAP_B)]
        lo, hi = _ci(draws)
        se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else 0.0
        brier[name] = {
            "what": f"Brier(rating) − Brier({name})",
            "delta": round(float(d.mean()), 5),
            "lo": None if lo is None else round(lo, 5),
            "hi": None if hi is None else round(hi, 5),
            "excludes_zero": bool(lo is not None and hi is not None and (lo > 0.0 or hi < 0.0)),
            # As in the coin-flip block: what these maps could have resolved. A
            # gap can clear its interval and still sit under this, and the two
            # statements are different enough that publishing one alone misleads.
            "mde80": round(POWER_FACTOR * se, 5) if se > 0 else None,
        }
    out["brier"] = brier

    # The same paired interval for every predictor against the 0.5 floor. The
    # rating-anchored block above answers "is the composite better than its
    # baselines"; this one answers "is any of them better than nothing", which
    # is the question a predictor built to challenge the composite has to pass
    # before its ranking against the composite means anything.
    vs_flip: dict[str, Any] = {}
    for who in named:
        d = err[who] - err["coin_flip"]
        draws = [float(d[idx[b]].mean()) for b in range(BOOTSTRAP_B)]
        lo, hi = _ci(draws)
        se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else 0.0
        vs_flip[who] = {
            "what": f"Brier({who}) − Brier(coin flip)",
            "delta": round(float(d.mean()), 5),
            "lo": None if lo is None else round(lo, 5),
            "hi": None if hi is None else round(hi, 5),
            "excludes_zero": bool(lo is not None and hi is not None and (lo > 0.0 or hi < 0.0)),
            # What this many maps could have resolved, so a predictor that lands
            # short of the floor is not read as one that was never tested.
            "mde80": round(POWER_FACTOR * se, 5) if se > 0 else None,
        }
    out["vs_coin_flip"] = vs_flip

    # Brier and accuracy can disagree here, and reporting only one would mislead:
    # a predictor whose probabilities all sit near 0.5 scores ~0.25 however often
    # it points the right way. So the pick rate gets its own interval against the
    # 50% floor, on the same resampled maps.
    acc: dict[str, Any] = {}
    for who in list(named):
        hit = np.array(
            [1.0 if (named[who][g].p > 0.5) == named[who][g].won else 0.0 for g in common]
        )
        draws = [float(hit[idx[b]].mean()) for b in range(BOOTSTRAP_B)]
        lo, hi = _ci(draws)
        acc[who] = {
            "accuracy": round(float(hit.mean()), 4),
            "lo": lo,
            "hi": hi,
            "beats_coin_flip": bool(lo is not None and lo > 0.5),
        }
    out["accuracy"] = acc
    return out


def _group_maps(rows: Sequence[MapRow]) -> list[list[MapRow]]:
    per_game: dict[int, list[MapRow]] = defaultdict(list)
    for r in rows:
        per_game[r.game_id].append(r)
    out = []
    for game_id in sorted(per_game, key=lambda g: (per_game[g][0].played_at, g)):
        members = per_game[game_id]
        if len({m.team_id for m in members}) == 2:
            out.append(members)
    return out


def _map_diff(
    members: Sequence[MapRow], table: dict[tuple[int, int, int | None], float]
) -> tuple[float, bool] | None:
    """(team A minus team B strength, did A win) for one map."""
    a, b = sorted({m.team_id for m in members})
    sa = _roster_strength([m for m in members if m.team_id == a], table, members[0].mode_id)
    sb = _roster_strength([m for m in members if m.team_id == b], table, members[0].mode_id)
    if sa is None or sb is None:
        return None
    a_won = next(m.won is True for m in members if m.team_id == a)
    return (sa - sb, a_won)


def _glicko_map(
    members: Sequence[MapRow],
    games_series: dict[int, int],
    glicko: dict[tuple[int, int], float],
) -> tuple[float, bool] | None:
    series_id = games_series.get(members[0].game_id)
    if series_id is None:
        return None
    a, b = sorted({m.team_id for m in members})
    ra, rb = glicko.get((series_id, a)), glicko.get((series_id, b))
    if ra is None or rb is None:
        return None
    a_won = next(m.won is True for m in members if m.team_id == a)
    # The Elo/Glicko logistic, on the 400-point scale the ratings are stored in.
    return (1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0)), a_won)


def _common_scored(preds: dict[str, dict[int, Prediction]]) -> dict[str, Any]:
    """Every predictor re-scored on the maps all of them reached.

    A roster predictor drops a map where nobody has prior history; Glicko-2 never
    does. Comparing raw totals would let the fussier predictor look better on an
    easier subset, so the intersection is taken by map id — the same rule the
    feature-set comparison uses.
    """
    named = {k: v for k, v in preds.items() if v}
    if len(named) < 2:
        return {"available": False, "reason": "fewer than two predictors scored"}
    common = sorted(set.intersection(*(set(v) for v in named.values())))
    if not common:
        return {"available": False, "reason": "no map was scored by every predictor"}
    return {
        "available": True,
        "n_maps": len(common),
        "predictors": {name: _report([v[g] for g in common]) for name, v in named.items()},
    }


def _report(ps: Sequence[Prediction]) -> dict[str, Any]:
    rep = evaluate(list(ps))
    return {
        "n": rep.n,
        "brier": round(rep.brier, 5),
        "log_loss": round(rep.log_loss, 5),
        "accuracy": round(rep.accuracy, 4),
    }
