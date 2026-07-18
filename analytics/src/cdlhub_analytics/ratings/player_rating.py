"""player_rating_v1: the open composite player rating. Spec: /methodology#player-rating.

The pipeline, in order:

1. **Learn what wins maps.** For every (season × mode), each map becomes one
   observation: the difference between the two teams' per-10-minute stat
   profiles (kills, deaths, assists, mode objective), standardized, regressed
   against which team won the map (L2 logistic, fit in regress.py). The
   coefficients are data-derived answers to "how much is a 1 SD edge in hill
   time worth vs a 1 SD edge in kills?" — per title, per mode.

2. **Score players with those weights.** Each player-season-mode aggregate is
   z-scored against its qualified cohort (>= MIN_MAPS maps, as in era.py) and
   dotted with the mode's learned weights, then standardized so modes are on
   a common scale.

3. **Shrink small samples.** Scores are pulled toward the league mean by
   m / (m + SHRINK_MAPS) where m is maps played — empirical-Bayes partial
   pooling, so a hot 12-map season cannot outrank a great 200-map one.

4. **Normalize.** Season rating = 1.0 + RATING_SCALE × (maps-weighted blend
   of mode scores); the qualified cohort averages 1.0 by construction.
   rating_sd is a map-resampling bootstrap (B=200, fixed seed).

Validation is walk-forward: within each (season × mode), each event's maps
are predicted using weights trained only on earlier events. That backtest
ships with the model, per the publishing rule.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

import numpy as np
import psycopg

from ..backtest import Prediction
from ..era import MIN_MAPS
from ..regress import FloatArray, LogisticFit, fit_logistic_l2

FEATURES = ("kills_p10", "deaths_p10", "assists_p10", "obj_p10")
N_FEATURES = len(FEATURES)

L2 = 1.0  # ridge strength on standardized map diffs
SHRINK_MAPS = 15.0  # prior strength: maps at which a season keeps half its signal... see spec
RATING_SCALE = 0.15  # rating = 1.0 + 0.15 × blended score (≈ league SD)
MIN_TRAIN_GAMES = 40  # walk-forward: skip events until this much history exists
BOOTSTRAP_B = 200
BOOTSTRAP_SEED = 20170812  # CWL Champs 2017 finals date; any fixed seed works

_MAP_SQL = """
SELECT gps.player_id, gps.team_id, g.id, e.season_id, g.mode_id, gm.slug,
       g.duration_s, (gps.team_id = g.winner_team_id)::int AS won,
       s.event_id, s.played_at,
       gps.kills, gps.deaths, gps.assists,
       COALESCE(gps.hill_time, 0) AS hill_time,
       COALESCE(gps.first_bloods, 0) + COALESCE(gps.plants, 0)
         + COALESCE(gps.defuses, 0) AS snd_obj,
       COALESCE((gps.extras->>'ctrl_captures')::float, 0) AS ctrl_obj,
       COALESCE((gps.extras->>'ctf_captures')::float, 0)
         + COALESCE((gps.extras->>'ctf_returns')::float, 0) AS ctf_obj,
       COALESCE((gps.extras->>'uplink_points')::float, 0) AS uplink_obj
FROM game_player_stats gps
JOIN games g  ON g.id = gps.game_id
JOIN game_modes gm ON gm.id = g.mode_id
JOIN series s ON s.id = g.series_id
JOIN events e ON e.id = s.event_id
WHERE g.winner_team_id IS NOT NULL AND g.duration_s IS NOT NULL
  AND gps.kills IS NOT NULL AND gps.deaths IS NOT NULL
ORDER BY s.played_at, g.id
"""


@dataclass
class MapRow:
    player_id: int
    team_id: int
    game_id: int
    season_id: int
    mode_id: int
    duration_s: int
    won: bool
    event_id: int
    when: date
    raw: FloatArray  # (kills, deaths, assists, obj) — counts, not rates


def _obj(slug: str, r: tuple[object, ...]) -> float:
    match slug:
        case "hardpoint":
            return float(cast(int, r[13]))
        case "search-and-destroy":
            return float(cast(int, r[14]))
        case "control":
            return float(cast(float, r[15]))
        case "capture-the-flag":
            return float(cast(float, r[16]))
        case "uplink":
            return float(cast(float, r[17]))
    return 0.0


def load_map_rows(conn: psycopg.Connection[tuple[object, ...]]) -> list[MapRow]:
    out: list[MapRow] = []
    for r in conn.execute(_MAP_SQL).fetchall():
        out.append(
            MapRow(
                player_id=cast(int, r[0]),
                team_id=cast(int, r[1]),
                game_id=cast(int, r[2]),
                season_id=cast(int, r[3]),
                mode_id=cast(int, r[4]),
                duration_s=cast(int, r[6]),
                won=bool(cast(int, r[7])),
                event_id=cast(int, r[8]),
                when=cast(datetime, r[9]).date(),
                raw=np.array(
                    [
                        float(cast(int, r[10])),
                        float(cast(int, r[11])),
                        float(cast(int, r[12])),
                        _obj(cast(str, r[5]), r),
                    ]
                ),
            )
        )
    return out


# ---------------------------------------------------------------- map games


@dataclass
class GameDiff:
    """One map as a single observation: team A minus team B, per 10 minutes."""

    game_id: int
    event_id: int
    when: date
    diff: FloatArray  # (N_FEATURES,)
    a_won: bool


def build_game_diffs(rows: list[MapRow]) -> dict[tuple[int, int], list[GameDiff]]:
    """Per (season, mode): one differential observation per map, in played order."""
    per_game: dict[int, list[MapRow]] = defaultdict(list)
    for r in rows:
        per_game[r.game_id].append(r)

    out: dict[tuple[int, int], list[GameDiff]] = defaultdict(list)
    for game_id in sorted(per_game, key=lambda g: (per_game[g][0].when, g)):
        members = per_game[game_id]
        teams = sorted({m.team_id for m in members})
        if len(teams) != 2:
            continue
        a, b = teams
        per10 = 600.0 / members[0].duration_s
        mean_a = np.mean([m.raw for m in members if m.team_id == a], axis=0) * per10
        mean_b = np.mean([m.raw for m in members if m.team_id == b], axis=0) * per10
        first = members[0]
        out[(first.season_id, first.mode_id)].append(
            GameDiff(
                game_id=game_id,
                event_id=first.event_id,
                when=first.when,
                diff=np.asarray(mean_a - mean_b),
                a_won=next(m.won for m in members if m.team_id == a),
            )
        )
    return dict(out)


@dataclass
class ModeFit:
    n_games: int
    mu: FloatArray  # standardization of diffs
    sd: FloatArray
    fit: LogisticFit

    @property
    def weights(self) -> FloatArray:
        return self.fit.weights


def _standardize(diffs: list[GameDiff]) -> tuple[FloatArray, FloatArray, FloatArray]:
    x = np.array([g.diff for g in diffs])
    mu = x.mean(axis=0)
    sd = x.std(axis=0, ddof=1)
    sd[sd == 0.0] = 1.0
    return (x - mu) / sd, mu, sd


def fit_mode_weights(
    diffs_by_cohort: dict[tuple[int, int], list[GameDiff]],
) -> dict[tuple[int, int], ModeFit]:
    out: dict[tuple[int, int], ModeFit] = {}
    for key, diffs in diffs_by_cohort.items():
        if len(diffs) < MIN_TRAIN_GAMES:
            continue
        x, mu, sd = _standardize(diffs)
        y = np.array([1.0 if g.a_won else 0.0 for g in diffs])
        out[key] = ModeFit(n_games=len(diffs), mu=mu, sd=sd, fit=fit_logistic_l2(x, y, l2=L2))
    return out


def backtest_weights(
    diffs_by_cohort: dict[tuple[int, int], list[GameDiff]],
) -> list[Prediction]:
    """Walk-forward by event: predict each event's maps from weights trained
    only on earlier events in the same (season × mode)."""
    preds: list[Prediction] = []
    for diffs in diffs_by_cohort.values():
        event_order: list[int] = []
        for g in diffs:
            if g.event_id not in event_order:
                event_order.append(g.event_id)
        for i, event_id in enumerate(event_order):
            if i == 0:
                continue
            train = [g for g in diffs if g.event_id in event_order[:i]]
            test = [g for g in diffs if g.event_id == event_id]
            if len(train) < MIN_TRAIN_GAMES:
                continue
            x, mu, sd = _standardize(train)
            y = np.array([1.0 if g.a_won else 0.0 for g in train])
            fit = fit_logistic_l2(x, y, l2=L2)
            xt = (np.array([g.diff for g in test]) - mu) / sd
            for g, p in zip(test, fit.predict(np.asarray(xt)), strict=True):
                preds.append(Prediction(p=float(p), won=g.a_won, when=g.when))
    return preds


# ------------------------------------------------------------ player scores


@dataclass
class PlayerModeAgg:
    player_id: int
    season_id: int
    mode_id: int
    maps: int
    feats: FloatArray  # aggregate per-10-min profile
    per_map: FloatArray  # (maps × N_FEATURES) raw counts, for the bootstrap
    durations: FloatArray  # (maps,) seconds


def aggregate_players(rows: list[MapRow]) -> list[PlayerModeAgg]:
    grouped: dict[tuple[int, int, int], list[MapRow]] = defaultdict(list)
    for r in rows:
        grouped[(r.player_id, r.season_id, r.mode_id)].append(r)
    out: list[PlayerModeAgg] = []
    for (pid, season_id, mode_id), maps in grouped.items():
        raw = np.array([m.raw for m in maps])
        dur = np.array([float(m.duration_s) for m in maps])
        out.append(
            PlayerModeAgg(
                player_id=pid,
                season_id=season_id,
                mode_id=mode_id,
                maps=len(maps),
                feats=np.asarray(raw.sum(axis=0) / dur.sum() * 600.0),
                per_map=raw,
                durations=dur,
            )
        )
    return out


@dataclass
class CohortScale:
    """Frozen standardization for one (season × mode) qualified cohort."""

    feat_mu: FloatArray
    feat_sd: FloatArray
    score_mu: float
    score_sd: float


def _score(agg_feats: FloatArray, scale: CohortScale, weights: FloatArray) -> float:
    """Weights-dot-z, standardized to the qualified cohort's score scale."""
    z = (agg_feats - scale.feat_mu) / scale.feat_sd
    return (float(z @ weights) - scale.score_mu) / scale.score_sd


def _shrink(score: float, maps: int) -> float:
    return score * maps / (maps + SHRINK_MAPS)


def build_cohort_scales(
    aggs: list[PlayerModeAgg], fits: dict[tuple[int, int], ModeFit]
) -> dict[tuple[int, int], CohortScale]:
    by_cohort: dict[tuple[int, int], list[PlayerModeAgg]] = defaultdict(list)
    for a in aggs:
        by_cohort[(a.season_id, a.mode_id)].append(a)
    out: dict[tuple[int, int], CohortScale] = {}
    for key, members in by_cohort.items():
        fit = fits.get(key)
        qualified = [a for a in members if a.maps >= MIN_MAPS]
        if fit is None or len(qualified) < 2:
            continue
        feats = np.array([a.feats for a in qualified])
        mu, sd = feats.mean(axis=0), feats.std(axis=0, ddof=1)
        sd[sd == 0.0] = 1.0
        scores = np.array([float(((a.feats - mu) / sd) @ fit.weights) for a in qualified])
        score_sd = float(scores.std(ddof=1))
        out[key] = CohortScale(
            feat_mu=np.asarray(mu),
            feat_sd=np.asarray(sd),
            score_mu=float(scores.mean()),
            score_sd=score_sd if score_sd > 0.0 else 1.0,
        )
    return out


@dataclass
class SeasonRating:
    player_id: int
    season_id: int
    mode_id: int | None  # None = all-mode blend
    maps: int
    rating: float
    rating_sd: float | None


def compute_ratings(
    aggs: list[PlayerModeAgg],
    fits: dict[tuple[int, int], ModeFit],
    scales: dict[tuple[int, int], CohortScale],
) -> list[SeasonRating]:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    by_player_season: dict[tuple[int, int], list[PlayerModeAgg]] = defaultdict(list)
    for a in aggs:
        if (a.season_id, a.mode_id) in scales:
            by_player_season[(a.player_id, a.season_id)].append(a)

    out: list[SeasonRating] = []
    for (pid, season_id), modes in sorted(by_player_season.items()):
        shrunk: list[float] = []
        weights_m: list[int] = []
        boot: FloatArray = np.zeros((BOOTSTRAP_B, len(modes)))
        for j, a in enumerate(modes):
            key = (a.season_id, a.mode_id)
            scale, fit = scales[key], fits[key]
            s = _shrink(_score(a.feats, scale, fit.weights), a.maps)
            out.append(
                SeasonRating(
                    player_id=pid,
                    season_id=season_id,
                    mode_id=a.mode_id,
                    maps=a.maps,
                    rating=1.0 + RATING_SCALE * s,
                    rating_sd=None,  # per-mode sd folds into the blended row
                )
            )
            shrunk.append(s)
            weights_m.append(a.maps)
            idx = rng.integers(0, a.maps, size=(BOOTSTRAP_B, a.maps))
            for b in range(BOOTSTRAP_B):
                feats = np.asarray(
                    a.per_map[idx[b]].sum(axis=0) / a.durations[idx[b]].sum() * 600.0
                )
                boot[b, j] = _shrink(_score(feats, scale, fit.weights), a.maps)

        total_maps = sum(weights_m)
        blend = float(np.average(shrunk, weights=weights_m))
        boot_blend = np.average(boot, axis=1, weights=weights_m)
        out.append(
            SeasonRating(
                player_id=pid,
                season_id=season_id,
                mode_id=None,
                maps=total_maps,
                rating=1.0 + RATING_SCALE * blend,
                rating_sd=RATING_SCALE * float(np.asarray(boot_blend).std(ddof=1)),
            )
        )
    return out


# ------------------------------------------------------------------ orchestration


def weights_artifact(
    conn: psycopg.Connection[tuple[object, ...]], fits: dict[tuple[int, int], ModeFit]
) -> dict[str, Any]:
    """The learned weights, labeled for /methodology and the findings layer."""
    seasons = {
        cast(int, r[0]): {"year": cast(int, r[1]), "title": cast(str, r[2])}
        for r in conn.execute(
            "SELECT se.id, se.year, t.short_name FROM seasons se"
            " JOIN titles t ON t.id = se.title_id"
        ).fetchall()
    }
    modes = {
        cast(int, r[0]): cast(str, r[1])
        for r in conn.execute("SELECT id, name FROM game_modes").fetchall()
    }
    entries = []
    for (season_id, mode_id), fit in sorted(fits.items()):
        named = list(zip(FEATURES, fit.weights, strict=True))
        entries.append(
            {
                "season_id": season_id,
                "year": seasons[season_id]["year"],
                "title": seasons[season_id]["title"],
                "mode_id": mode_id,
                "mode": modes[mode_id],
                "n_maps": fit.n_games,
                "weights": {f: round(float(w), 4) for f, w in named},
                "odds_per_sd": {f: round(float(np.exp(w)), 3) for f, w in named},
            }
        )
    return {"features": list(FEATURES), "l2": L2, "cohorts": entries}


def compute_and_write(
    conn: psycopg.Connection[tuple[object, ...]], run_id: int
) -> tuple[int, list[Prediction], dict[str, Any]]:
    """Fit weights, rate players, write rows + artifact. Returns
    (n_rating_rows, walk-forward predictions, weights artifact)."""
    rows = load_map_rows(conn)
    diffs = build_game_diffs(rows)
    fits = fit_mode_weights(diffs)
    preds = backtest_weights(diffs)
    aggs = aggregate_players(rows)
    scales = build_cohort_scales(aggs, fits)
    ratings = compute_ratings(aggs, fits, scales)

    conn.cursor().executemany(
        "INSERT INTO player_season_adjusted (run_id, player_id, season_id, mode_id,"
        " maps_played, rating, rating_sd, completeness)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 1.0)",
        [
            (run_id, r.player_id, r.season_id, r.mode_id, r.maps, r.rating, r.rating_sd)
            for r in ratings
        ],
    )
    artifact = weights_artifact(conn, fits)
    conn.execute(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        (run_id, "mode_weights", json.dumps(artifact)),
    )
    return len(ratings), preds, artifact
