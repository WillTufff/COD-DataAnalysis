"""Round win probability from the kill feed. Spec: /methodology#round-win-probability.

The box-score tier can say a player got twenty kills. It cannot say what those
kills were worth. This module builds the missing object: P(win this round | the
state of the round right now), fitted on the 2017-18 structured feed, and the
win probability added by each individual kill that follows from it.

**Scope is Search & Destroy, and that is not a simplification.** SnD is the only
mode in the archive with a round as a unit of play: one life each, a discrete
winner, ~9,300 of them. `game_rounds` does carry rows for the other feed modes,
but a Hardpoint "round" is the whole map and a CTF one is a half, so there is no
round-scale contest to model there. BO4 ships empty event lists, so 2019 is out
regardless. What remains — IW 2017 and WWII 2018 SnD — reconciles cleanly: 1,023
of 1,024 games resolve a round winner, every one of them 4v4.

**There is no bomb state in the feed.** The task this was built from asked for
one, and the events simply do not carry plant or defuse; `means_of_death` is
fifteen kinds of gunfire. It is not wholly unrecoverable — round durations pile
up at 90s and then tail off, which is the regulation timer expiring, so a round
still alive at 90s implies a plant. That indicator is *tested* below rather than
assumed away, and it adds nothing (see `_SPECS` / the comparison artifact). The
reason is structural: the feed never says which side planted, and a bomb
indicator that cannot be attributed to a team is symmetric under swapping the
two teams, while the thing being predicted is antisymmetric. A symmetric feature
cannot move an antisymmetric target, so it enters at a coefficient of zero.

**The model is a 16-cell table.** Every parametric form tried was beaten,
narrowly but repeatably, by simply counting outcomes per (own alive, opponent
alive) state. With 4v4 there are only sixteen non-terminal states and ~90,000
observations of them, so the table cannot meaningfully overfit and there is
nothing for a smooth function to buy. The logistic fit is still reported next to
it because it says something the table does not:

    logit P(win) ≈ 2.0 · [log(own) − log(opp)] + 0.42 · [own − opp]

round win odds go roughly as the *ratio* of survivors, not the difference — a
4v3 is worth much less than a 2v1 despite both being "up one".

Two nulls come out of the same backtest, both stated with an interval rather
than by eye, because a null this project publishes has to survive "you were
underpowered":

  1. Time elapsed in the round adds nothing once survivors are known.
  2. The post-regulation bomb proxy adds nothing, for the symmetry reason above.

The table answers "what is this state worth" but says nothing about *when* states
arrive, so a third artifact describes the round along its own clock:
`round_timeline` puts survivors, win probability and trade latency on one 5 s
grid. It is description, not a second model — the probabilities are the same
table read in sample, which is why the model-free "did the side ahead win"
series is published beside them.

And one null that matters more, in `wpa`: aggregated per player, round win
probability added is kill rate wearing a different unit (r = 0.93), and the part
of it that is *not* kill rate does not repeat across a player's own games. So
WPA is published as a description of what happened in a round, and deliberately
not promoted into the player rating — see the reliability block in the artifact.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import numpy as np
import psycopg

from .maprows import MODE_SND
from .metrics import FEED_TEAMS_SQL, TRADE_WINDOW_MS, resolve_round_winners
from .regress import FloatArray, fit_logistic_l2

MODEL = "round_wp"
VERSION = "1.1.0"

# --- the time-resolved description (round_timeline artifact) ---
# 5 s over two minutes: fine enough to see a round decided, coarse enough that
# every bin holds hundreds of rounds. Rounds past the last bin are the handful
# beyond 120 s and fold into it.
TIMELINE_BIN_MS = 5_000
TIMELINE_MAX_MS = 120_000
# Trade latency is drawn at 1 s out to 12 s — the window is 5 s, and the point of
# the figure is what sits either side of that line.
TRADE_LATENCY_BIN_MS = 1_000
TRADE_LATENCY_MAX_MS = 12_000
# The two instants the early/late comparison is made at: one while most rounds
# are still whole, one once half of them have finished. Single instants, so each
# round is counted once.
DRIFT_INSTANTS_S = (15, 60)

L2 = 1.0
# Add-half-a-round to each cell before dividing. The table is only ever asked
# about states it has thousands of observations of, so this is a formality that
# keeps a hypothetical empty cell at 0.5 rather than undefined.
LAPLACE = 0.5

# SnD regulation, in ms: rounds cluster at this length and then tail off, which
# is the clock expiring with no plant. Used only to build the bomb proxy that
# the comparison then rejects.
REGULATION_MS = 90_000

# A player needs this many rounds on each side of the split before their WPA
# rate is asked to repeat. Below it the rate is mostly sampling noise and the
# reliability test measures the threshold rather than the player.
MIN_SPLIT_ROUNDS = 75

BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20180818  # CWL Champs 2018 finals; any fixed seed works

# Two-sided 5% at 80% power, same convention as ratings.significance.
Z_ALPHA = 1.959964
Z_POWER = 0.841621
POWER_FACTOR = Z_ALPHA + Z_POWER


def params() -> dict[str, Any]:
    """Everything this model was configured with, for the model_runs row."""
    return {
        "mode": MODE_SND,
        "l2": L2,
        "laplace": LAPLACE,
        "regulation_ms": REGULATION_MS,
        "min_split_rounds": MIN_SPLIT_ROUNDS,
        "bootstrap_b": BOOTSTRAP_B,
        "timeline_bin_ms": TIMELINE_BIN_MS,
        "timeline_max_ms": TIMELINE_MAX_MS,
        "trade_latency_bin_ms": TRADE_LATENCY_BIN_MS,
        "trade_window_ms": TRADE_WINDOW_MS,
    }


_GAMES_SQL = f"""
SELECT g.id, s.team1_id, s.team2_id, g.team1_score, g.team2_score, g.winner_team_id,
       e.id, se.year, t.short_name
FROM games g
JOIN game_modes gm ON gm.id = g.mode_id
JOIN series s      ON s.id = g.series_id
JOIN events e      ON e.id = s.event_id
JOIN seasons se    ON se.id = e.season_id
JOIN titles t      ON t.id = se.title_id
WHERE gm.slug = '{MODE_SND}'
  AND EXISTS (SELECT 1 FROM kill_events k WHERE k.game_id = g.id)
"""

_EVENT_DATES_SQL = """
SELECT e.id, min(s.played_at)::date
FROM events e JOIN series s ON s.event_id = e.id
WHERE s.played_at IS NOT NULL
GROUP BY e.id
"""

_ROUNDS_SQL = f"""
SELECT gr.game_id, gr.round, gr.score1, gr.score2, gr.winner_side
FROM game_rounds gr
JOIN games g       ON g.id = gr.game_id
JOIN game_modes gm ON gm.id = g.mode_id
WHERE gm.slug = '{MODE_SND}'
ORDER BY gr.game_id, gr.round
"""

_SPANS_SQL = f"""
SELECT gr.game_id, gr.round, gr.end_time_ms - gr.start_time_ms
FROM game_rounds gr
JOIN games g       ON g.id = gr.game_id
JOIN game_modes gm ON gm.id = g.mode_id
WHERE gm.slug = '{MODE_SND}'
  AND gr.start_time_ms IS NOT NULL AND gr.end_time_ms IS NOT NULL
  AND gr.end_time_ms >= gr.start_time_ms
"""

_DEATHS_SQL = f"""
SELECT ke.game_id, ke.round, ke.round_time_ms, ke.seq, ke.victim_id, ke.killer_id
FROM kill_events ke
JOIN games g       ON g.id = ke.game_id
JOIN game_modes gm ON gm.id = g.mode_id
WHERE gm.slug = '{MODE_SND}' AND ke.death_kind = 'normal'
ORDER BY ke.game_id, ke.round, ke.round_time_ms, ke.seq
"""


@dataclass(frozen=True)
class Step:
    """One instant in a round: the alive counts, and the death that produced them.

    `alive` is aligned with the round's `teams` pair. The opening step of every
    round carries the full rosters and no death.
    """

    t_ms: int
    alive: tuple[int, int]
    killer: int | None
    victim: int | None


@dataclass(frozen=True)
class RoundTimeline:
    game_id: int
    round: int
    event_id: int
    played_at: date
    teams: tuple[int, int]
    winner: int
    roster: dict[int, int]  # player_id -> team_id
    steps: tuple[Step, ...]
    # How long the round ran, from `game_rounds`. None when the row carries no
    # usable span or the feed contradicts it; only the timeline description needs
    # it, and it drops those rounds rather than guessing at an end.
    end_ms: int | None = None

    def side(self, team_id: int) -> int:
        return 0 if team_id == self.teams[0] else 1

    def alive_at(self, t_ms: int) -> tuple[int, int]:
        """Survivor counts at an arbitrary instant: the last step at or before it."""
        alive = self.steps[0].alive
        for step in self.steps:
            if step.t_ms > t_ms:
                break
            alive = step.alive
        return alive


# (own alive, opponent alive, ms elapsed, won) — one row per side per instant.
StateRow = tuple[int, int, int, float]


def timeline_steps(
    deaths: Sequence[tuple[int, int, int | None]],
    alive: tuple[int, int],
    side_of: dict[int, int],
) -> tuple[Step, ...] | None:
    """Walk a round's ordered deaths into alive-count states.

    `deaths` is (round_time_ms, victim, killer). Returns None when the feed
    disagrees with itself — a victim who is not on either roster, or one dying
    while their side is already empty. That happens in one round out of 9,302,
    and a round whose alive counts cannot be trusted is dropped rather than
    patched, matching how the reconciliation view treats a failing player-map.

    Pure, so tests can hand it synthetic rounds.
    """
    counts = list(alive)
    steps = [Step(t_ms=0, alive=(counts[0], counts[1]), killer=None, victim=None)]
    for t_ms, victim, killer in deaths:
        side = side_of.get(victim)
        if side is None or counts[side] <= 0:
            return None
        counts[side] -= 1
        steps.append(Step(t_ms=t_ms, alive=(counts[0], counts[1]), killer=killer, victim=victim))
    return tuple(steps)


def load_rounds(conn: psycopg.Connection[tuple[object, ...]]) -> list[RoundTimeline]:
    """Every SnD round with a resolvable winner and a coherent death timeline."""
    team_of: dict[int, dict[int, int]] = defaultdict(dict)
    for row in conn.execute(FEED_TEAMS_SQL):
        team_of[cast(int, row[0])][cast(int, row[1])] = cast(int, row[2])

    event_date: dict[int, date] = {
        cast(int, row[0]): cast(date, row[1]) for row in conn.execute(_EVENT_DATES_SQL)
    }

    games: dict[int, tuple[int, int, int | None, int | None, int | None, int]] = {}
    for row in conn.execute(_GAMES_SQL):
        games[cast(int, row[0])] = (
            cast(int, row[1]),
            cast(int, row[2]),
            cast("int | None", row[3]),
            cast("int | None", row[4]),
            cast("int | None", row[5]),
            cast(int, row[6]),
        )

    rounds_by_game: dict[int, list[tuple[int, int | None, int | None, int | None]]] = defaultdict(
        list
    )
    for row in conn.execute(_ROUNDS_SQL):
        rounds_by_game[cast(int, row[0])].append(
            (
                cast(int, row[1]),
                cast("int | None", row[2]),
                cast("int | None", row[3]),
                cast("int | None", row[4]),
            )
        )

    span_of: dict[tuple[int, int], int] = {
        (cast(int, row[0]), cast(int, row[1])): cast(int, row[2])
        for row in conn.execute(_SPANS_SQL)
    }

    deaths_by_round: dict[tuple[int, int], list[tuple[int, int, int | None]]] = defaultdict(list)
    for row in conn.execute(_DEATHS_SQL):
        deaths_by_round[(cast(int, row[0]), cast(int, row[1]))].append(
            (
                cast(int, row[2] or 0),
                cast(int, row[4]),
                cast("int | None", row[5]),
            )
        )

    out: list[RoundTimeline] = []
    for game_id, (t1, t2, t1s, t2s, gw, event_id) in games.items():
        played = event_date.get(event_id)
        if played is None:
            continue
        winners = resolve_round_winners(rounds_by_game.get(game_id, []), t1, t2, t1s, t2s, gw)
        if not winners:
            continue
        roster = team_of.get(game_id, {})
        by_team: dict[int, int] = defaultdict(int)
        for tid in roster.values():
            by_team[tid] += 1
        if len(by_team) != 2:
            continue
        ordered = sorted(by_team)
        pair = (t1, t2) if set(by_team) == {t1, t2} else (ordered[0], ordered[1])
        side_of = {pid: (0 if tid == pair[0] else 1) for pid, tid in roster.items()}
        opening = (by_team[pair[0]], by_team[pair[1]])

        for rnd in sorted(winners):
            deaths = deaths_by_round.get((game_id, rnd))
            if not deaths:
                continue
            steps = timeline_steps(deaths, opening, side_of)
            if steps is None:
                continue
            # A span that ends before the feed's last death describes a different
            # round than the feed does — 1.9% of them, by 38 s at the median, so
            # not rounding. Those keep end_ms=None and the timeline drops them.
            span = span_of.get((game_id, rnd))
            if span is not None and span < steps[-1].t_ms:
                span = None
            out.append(
                RoundTimeline(
                    game_id=game_id,
                    round=rnd,
                    event_id=event_id,
                    played_at=played,
                    teams=pair,
                    winner=winners[rnd],
                    roster=dict(roster),
                    steps=steps,
                    end_ms=span,
                )
            )
    out.sort(key=lambda r: (r.played_at, r.event_id, r.game_id, r.round))
    return out


def state_rows(rounds: Iterable[RoundTimeline]) -> list[StateRow]:
    """Training rows: both sides of every non-terminal instant.

    Recording both perspectives is what makes the target antisymmetric by
    construction — P(a beats b) and P(b beats a) are the same observation seen
    twice — and it is why the fitted intercept comes out at zero and every
    (n, n) state at exactly 0.500. Terminal instants (a side wiped out) are
    excluded: there is nothing left to predict.
    """
    out: list[StateRow] = []
    for r in rounds:
        won = (1.0 if r.winner == r.teams[0] else 0.0, 1.0 if r.winner == r.teams[1] else 0.0)
        for step in r.steps:
            a, b = step.alive
            if a <= 0 or b <= 0:
                continue
            out.append((a, b, step.t_ms, won[0]))
            out.append((b, a, step.t_ms, won[1]))
    return out


# Every parametric form the comparison puts up against the table. `survivors` is
# the one quoted in the docs; the rest exist to be rejected, and are named for
# what they add rather than for a version number.
_SPECS: tuple[str, ...] = (
    "diff_only",
    "log_ratio_only",
    "survivors",
    "survivors_time",
    "survivors_bomb",
)


def features(rows: Sequence[StateRow], spec: str) -> FloatArray:
    a = np.array([r[0] for r in rows], dtype=float)
    b = np.array([r[1] for r in rows], dtype=float)
    t = np.array([r[2] for r in rows], dtype=float)
    log_ratio = np.log(a) - np.log(b)
    diff = a - b
    # Elapsed in units of a regulation round, capped so a long post-plant round
    # cannot become an outlier with leverage.
    elapsed = np.clip(t / REGULATION_MS, 0.0, 2.0)
    bomb = (t > REGULATION_MS).astype(float)
    cols = {
        "diff_only": [diff],
        "log_ratio_only": [log_ratio],
        "survivors": [log_ratio, diff],
        "survivors_time": [log_ratio, diff, log_ratio * elapsed, diff * elapsed],
        "survivors_bomb": [log_ratio, diff, log_ratio * bomb, diff * bomb],
    }[spec]
    return np.column_stack(cols)


def feature_names(spec: str) -> list[str]:
    return {
        "diff_only": ["diff"],
        "log_ratio_only": ["log_ratio"],
        "survivors": ["log_ratio", "diff"],
        "survivors_time": ["log_ratio", "diff", "log_ratio_x_elapsed", "diff_x_elapsed"],
        "survivors_bomb": ["log_ratio", "diff", "log_ratio_x_bomb", "diff_x_bomb"],
    }[spec]


class StateTable:
    """Counted win rate per (own alive, opponent alive). The published model."""

    def __init__(self, rows: Iterable[StateRow], laplace: float = LAPLACE) -> None:
        cells: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for a, b, _t, won in rows:
            cell = cells[(a, b)]
            cell[0] += 1.0
            cell[1] += won
        self.cells = {k: (n, w) for k, (n, w) in cells.items()}
        self._laplace = laplace

    def p(self, own: int, opp: int) -> float:
        """P(the side with `own` survivors wins), 0 and 1 at the terminal states."""
        if own <= 0:
            return 0.0
        if opp <= 0:
            return 1.0
        n, w = self.cells.get((own, opp), (0.0, 0.0))
        return (w + self._laplace) / (n + 2.0 * self._laplace)

    def predict(self, rows: Sequence[StateRow]) -> FloatArray:
        return np.array([self.p(a, b) for a, b, _t, _y in rows], dtype=float)


def _brier(p: FloatArray, y: FloatArray) -> float:
    return float(np.mean((p - y) ** 2))


def walk_forward(rounds: Sequence[RoundTimeline]) -> dict[str, Any]:
    """Fit on every earlier event, score the next one. Never on its own rounds.

    Events are the split rather than individual rounds because that is the unit
    a real forecaster would have: the model that scores CWL Anaheim was fitted
    on everything through CWL Seattle and nothing after. The first event in the
    archive has no history and is therefore trained on but never scored.

    Losses are accumulated *per round*, not per state row, so the bootstrap can
    resample rounds. The twenty-odd rows a round contributes are the same round
    seen at successive instants and from both sides; treating them as
    independent observations would shrink every interval by roughly the square
    root of that count and manufacture significance.
    """
    events: list[tuple[date, int]] = sorted({(r.played_at, r.event_id) for r in rounds})
    by_event: dict[int, list[RoundTimeline]] = defaultdict(list)
    for r in rounds:
        by_event[r.event_id].append(r)

    names = [*_SPECS, "state_table", "coin_flip"]
    losses: dict[str, list[float]] = {n: [] for n in names}
    scored: list[RoundTimeline] = []
    calibration_rows: list[tuple[float, float]] = []

    for i, (_when, event_id) in enumerate(events):
        if i == 0:
            continue
        train = [r for _w, e in events[:i] for r in by_event[e]]
        train_rows = state_rows(train)
        if not train_rows:
            continue
        y_train = np.array([r[3] for r in train_rows], dtype=float)
        fits = {
            spec: fit_logistic_l2(features(train_rows, spec), y_train, l2=L2) for spec in _SPECS
        }
        table = StateTable(train_rows)

        for rnd in by_event[event_id]:
            rows = state_rows([rnd])
            if not rows:
                continue
            y = np.array([r[3] for r in rows], dtype=float)
            scored.append(rnd)
            for spec in _SPECS:
                losses[spec].append(_brier(fits[spec].predict(features(rows, spec)), y))
            p_table = table.predict(rows)
            losses["state_table"].append(_brier(p_table, y))
            losses["coin_flip"].append(_brier(np.full(len(y), 0.5), y))
            calibration_rows.extend(zip(p_table.tolist(), y.tolist(), strict=True))

    if not scored:
        return {"available": False, "reason": "no event has a prior event to train on"}

    n_rounds = len(scored)
    arrays = {k: np.array(v, dtype=float) for k, v in losses.items()}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, n_rounds, size=(BOOTSTRAP_B, n_rounds))

    models: list[dict[str, Any]] = []
    for name in names:
        draws = np.array([arrays[name][row].mean() for row in idx])
        lo, hi = np.percentile(draws, [2.5, 97.5])
        models.append(
            {
                "model": name,
                "brier": round(float(arrays[name].mean()), 6),
                "brier_lo": round(float(lo), 6),
                "brier_hi": round(float(hi), 6),
            }
        )

    baseline = "state_table"

    def pair(a: str, b: str) -> dict[str, Any]:
        d = arrays[a] - arrays[b]
        draws = np.array([d[row].mean() for row in idx])
        lo, hi = np.percentile(draws, [2.5, 97.5])
        se = float(d.std(ddof=1) / math.sqrt(n_rounds))
        mean = float(d.mean())
        return {
            "a": a,
            "b": b,
            "what": f"Brier({a}) − Brier({b})",
            "delta": round(mean, 6),
            "lo": round(float(lo), 6),
            "hi": round(float(hi), 6),
            "excludes_zero": bool(lo > 0.0 or hi < 0.0),
            "dm_t": round(mean / se, 3) if se > 0 else None,
            "dm_p": round(2.0 * (1.0 - _phi(abs(mean / se))), 4) if se > 0 else None,
            "mde80": round(POWER_FACTOR * se, 6),
        }

    pairs = [pair(name, baseline) for name in names if name != baseline]
    # The two nulls are add-a-feature questions, so they are asked against the
    # model the feature was added to, not against the published table. Comparing
    # `survivors_time` to the table would confound "time adds nothing" with the
    # separate, real gap between any smooth fit and counting.
    nested = [pair("survivors_time", "survivors"), pair("survivors_bomb", "survivors")]

    return {
        "available": True,
        "n_rounds": n_rounds,
        "n_events_scored": len(events) - 1,
        "n_events_total": len(events),
        "bootstrap_b": BOOTSTRAP_B,
        "baseline": baseline,
        "method": "walk-forward by event; per-round mean squared error, resampled by round"
        " (states within a round are one dependent cluster, not independent draws)",
        "models": models,
        "pairs": pairs,
        "nested": nested,
        "calibration": _calibration(calibration_rows),
    }


def _calibration(rows: Sequence[tuple[float, float]], n_bins: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        inbin = [(p, y) for p, y in rows if lo <= p < hi or (i == n_bins - 1 and p == 1.0)]
        if not inbin:
            out.append({"lo": lo, "hi": hi, "n": 0})
            continue
        out.append(
            {
                "lo": lo,
                "hi": hi,
                "n": len(inbin),
                "mean_pred": round(sum(p for p, _ in inbin) / len(inbin), 4),
                "frac_won": round(sum(y for _, y in inbin) / len(inbin), 4),
            }
        )
    return out


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def state_table_artifact(rounds: Sequence[RoundTimeline]) -> dict[str, Any]:
    """The published table, with the interpretable logistic fit beside it."""
    rows = state_rows(rounds)
    table = StateTable(rows)
    cells = []
    for (own, opp), (n, w) in sorted(table.cells.items()):
        p = w / n
        se = math.sqrt(max(p * (1.0 - p), 0.0) / n)
        cells.append(
            {
                "own": own,
                "opp": opp,
                "n": int(n),
                "p": round(p, 4),
                # Rounds are counted from both sides, so each cell holds each
                # round twice; the naive binomial SE is optimistic by ~sqrt(2)
                # and is widened to say so rather than quietly understating it.
                "se": round(se * math.sqrt(2.0), 4),
            }
        )

    fit = fit_logistic_l2(
        features(rows, "survivors"), np.array([r[3] for r in rows], dtype=float), l2=L2
    )
    return {
        "n_rounds": len(rounds),
        "n_states": len(rows),
        "laplace": LAPLACE,
        "cells": cells,
        "parametric": {
            "spec": "survivors",
            "l2": L2,
            "intercept": round(fit.intercept, 4),
            "weights": {
                k: round(float(v), 4)
                for k, v in zip(feature_names("survivors"), fit.weights, strict=True)
            },
            "note": "reported for interpretation only; the table is the published model",
        },
    }


def trade_latencies(rounds: Iterable[RoundTimeline]) -> list[int | None]:
    """How long each death waited to be answered by the victim's own side.

    One entry per death that had a killer: the gap in ms until a teammate of the
    victim kills that killer, later in the same round, or None when nobody on
    that side ever does. This is `metrics.compute_map_trades` with the 5 s cutoff
    taken off — the window is a convention inherited from the archive's own
    `kills_stayed_alive` column, and the only way to show what it costs is to
    measure the latency it truncates.
    """
    out: list[int | None] = []
    for r in rounds:
        for i, step in enumerate(r.steps):
            if step.victim is None or step.killer is None:
                continue
            victim_team = r.roster.get(step.victim)
            latency: int | None = None
            for later in r.steps[i + 1 :]:
                if later.victim != step.killer:
                    continue
                # The killer dies here. It is a trade only if the answer came
                # from the victim's side; either way the killer cannot be killed
                # twice, so this is the only chance.
                if later.killer is not None and r.roster.get(later.killer) == victim_team:
                    latency = later.t_ms - step.t_ms
                break
            out.append(latency)
    return out


def _wilson_se(k: int, n: int) -> float:
    """Binomial SE of k/n, 0 when there is nothing to divide by."""
    if n <= 0:
        return 0.0
    p = k / n
    return math.sqrt(max(p * (1.0 - p), 0.0) / n)


def timeline_artifact(rounds: Sequence[RoundTimeline], table: StateTable) -> dict[str, Any]:
    """The round as a function of elapsed time, rather than as a state table.

    Three time-resolved views, all on the same 5 s grid:

    * **Survivors.** Mean players still alive on the eventual winner's and
      loser's side, over the rounds still undecided at that instant, beside the
      share of rounds that have got that far at all.
    * **Win probability.** The published table read at each instant for the side
      that goes on to win. In-sample by construction — every round here helped
      fit the table — so this is a description of the archive and not a forecast
      claim; the out-of-sample claim is `walk_forward`'s. Next to it sits the
      same question asked without any model, on the same subset: what the table
      says the side merely *ahead* on survivors is worth, and how often that side
      actually won. Those two are comparable; `p_winner` is not comparable to
      either, because it averages the even states in at 0.5.
    * **Trades.** Latency to the avenging kill, unwindowed, so the 5 s
      convention can be seen against the distribution it cuts.

    Rounds whose recorded span contradicts their feed are excluded from the two
    state views, which need to know when a round stopped; the trade view does
    not, so it keeps them.
    """
    total = len(rounds)
    spanned = [r for r in rounds if r.end_ms is not None]
    n_bins = TIMELINE_MAX_MS // TIMELINE_BIN_MS

    deaths_in_bin = [0] * n_bins
    traded_in_bin = [0] * n_bins
    for r in rounds:
        for i, step in enumerate(r.steps):
            if step.victim is None or step.killer is None:
                continue
            k = min(step.t_ms // TIMELINE_BIN_MS, n_bins - 1)
            deaths_in_bin[k] += 1
            victim_team = r.roster.get(step.victim)
            for later in r.steps[i + 1 :]:
                if later.victim != step.killer:
                    continue
                if (
                    later.t_ms - step.t_ms <= TRADE_WINDOW_MS
                    and later.killer is not None
                    and r.roster.get(later.killer) == victim_team
                ):
                    traded_in_bin[k] += 1
                break

    bins: list[dict[str, Any]] = []
    for k in range(n_bins):
        t_ms = k * TIMELINE_BIN_MS
        winner_alive: list[float] = []
        loser_alive: list[float] = []
        p_winner: list[float] = []
        p_leader: list[float] = []
        leader_n = 0
        leader_won = 0
        for r in spanned:
            end_ms = cast(int, r.end_ms)
            if t_ms > end_ms:
                continue
            a, b = r.alive_at(t_ms)
            if a <= 0 or b <= 0:
                continue  # already resolved by a wipe
            w = a if r.winner == r.teams[0] else b
            lose = b if r.winner == r.teams[0] else a
            winner_alive.append(float(w))
            loser_alive.append(float(lose))
            p_winner.append(table.p(w, lose))
            if w != lose:
                # Same subset, model and outcome side by side: what the table
                # says the side *ahead* is worth, and how often it actually won.
                # p_winner cannot be read against leader_wins — it averages the
                # even states in at 0.5 — so the comparable quantity is published
                # rather than left to the reader to construct wrongly.
                leader_n += 1
                p_leader.append(table.p(max(w, lose), min(w, lose)))
                if w > lose:
                    leader_won += 1
        n_live = len(p_winner)
        bins.append(
            {
                "t_s": t_ms // 1000,
                "n_live": n_live,
                "live_share": round(n_live / len(spanned), 4) if spanned else 0.0,
                "winner_alive": round(float(np.mean(winner_alive)), 4) if n_live else None,
                "loser_alive": round(float(np.mean(loser_alive)), 4) if n_live else None,
                "p_winner": round(float(np.mean(p_winner)), 4) if n_live else None,
                # The spread of an in-sample mean over rounds, so the curve
                # carries the same kind of band every other chart here does.
                "p_winner_se": (
                    round(float(np.std(p_winner, ddof=1) / math.sqrt(n_live)), 4)
                    if n_live > 1
                    else None
                ),
                "leader_n": leader_n,
                "p_leader": round(float(np.mean(p_leader)), 4) if leader_n else None,
                "leader_wins": round(leader_won / leader_n, 4) if leader_n else None,
                "leader_wins_se": round(_wilson_se(leader_won, leader_n), 4) if leader_n else None,
                "n_deaths": deaths_in_bin[k],
                "traded_share": (
                    round(traded_in_bin[k] / deaths_in_bin[k], 4) if deaths_in_bin[k] else None
                ),
                "traded_se": (
                    round(_wilson_se(traded_in_bin[k], deaths_in_bin[k]), 4)
                    if deaths_in_bin[k]
                    else None
                ),
            }
        )

    # Whether the man advantage is worth the same early and late. The table has
    # no time term — adding one did not improve out-of-sample Brier, see the
    # nulls in `walk_forward` — so any drift between what it says the side ahead
    # is worth and what that side actually did is a residual worth stating.
    #
    # Two single instants rather than pooled ranges: a round appears in twenty
    # bins, and pooling them would treat one round as twenty observations and
    # report an interval several times too tight.
    by_t = {b["t_s"]: b for b in bins}
    drift: list[dict[str, Any]] = []
    for t_s in DRIFT_INSTANTS_S:
        cell = by_t.get(t_s)
        if cell is None or not cell["leader_n"] or cell["p_leader"] is None:
            continue
        n = int(cell["leader_n"])
        observed = float(cell["leader_wins"])
        model = float(cell["p_leader"])
        se = _wilson_se(round(observed * n), n)
        drift.append(
            {
                "t_s": t_s,
                "n": n,
                "model": round(model, 4),
                "observed": round(observed, 4),
                "gap": round(observed - model, 4),
                "se": round(se, 4),
                "excludes_zero": bool(abs(observed - model) > Z_ALPHA * se),
            }
        )

    latencies = trade_latencies(rounds)
    n_answerable = len(latencies)
    answered = [ms for ms in latencies if ms is not None]
    lat_bins: list[dict[str, Any]] = []
    n_lat = TRADE_LATENCY_MAX_MS // TRADE_LATENCY_BIN_MS
    for k in range(n_lat):
        lo = k * TRADE_LATENCY_BIN_MS
        n = sum(1 for ms in answered if lo <= ms < lo + TRADE_LATENCY_BIN_MS)
        lat_bins.append(
            {
                "lo_s": lo / 1000,
                "hi_s": (lo + TRADE_LATENCY_BIN_MS) / 1000,
                "n": n,
                "share": round(n / n_answerable, 5) if n_answerable else 0.0,
                "in_window": lo + TRADE_LATENCY_BIN_MS <= TRADE_WINDOW_MS,
            }
        )
    beyond = sum(1 for ms in answered if ms >= TRADE_LATENCY_MAX_MS)
    within = sum(1 for ms in answered if ms <= TRADE_WINDOW_MS)

    return {
        "bin_ms": TIMELINE_BIN_MS,
        "max_ms": TIMELINE_MAX_MS,
        "n_rounds": total,
        "n_rounds_spanned": len(spanned),
        "n_rounds_span_conflict": total - len(spanned),
        "in_sample": (
            "the win-probability curve reads the published table on the rounds that fitted"
            " it; it describes this archive rather than forecasting a new one"
        ),
        "bins": bins,
        "leader_drift": drift,
        "trade_latency": {
            "window_ms": TRADE_WINDOW_MS,
            "bin_ms": TRADE_LATENCY_BIN_MS,
            "max_ms": TRADE_LATENCY_MAX_MS,
            "n_deaths": n_answerable,
            "n_answered": len(answered),
            "n_within_window": within,
            "within_of_answered": round(within / len(answered), 4) if answered else None,
            "median_ms": int(np.median(answered)) if answered else None,
            "bins": lat_bins,
            "beyond": {
                "n": beyond,
                "share": round(beyond / n_answerable, 5) if n_answerable else 0.0,
            },
            "never": {
                "n": n_answerable - len(answered),
                "share": (
                    round((n_answerable - len(answered)) / n_answerable, 5) if n_answerable else 0.0
                ),
            },
        },
    }


def wpa(rounds: Sequence[RoundTimeline], table: StateTable) -> dict[str, Any]:
    """Win probability added per kill, aggregated per player — and the test of
    whether that aggregate is anything more than a kill count.

    Each kill moves the round from one state to the next; the killer is credited
    with the change in their own side's win probability. Summed over a career it
    is a real description of what a player did. Whether it is a *measurement* of
    a player is a separate question, and the answer here is no: WPA per round
    correlates 0.93 with kills per round, and when a player's own games are split
    in two, the part of their WPA that kill rate does not explain fails to
    reproduce itself. Both numbers are reported so nobody reads the leaderboard
    as a rating.

    The split is by game, not by alternating rounds within a game: rounds of the
    same map share opponent, map and side, and splitting inside one would let
    that shared context masquerade as a stable player trait.
    """
    total_wpa: dict[int, float] = defaultdict(float)
    total_kills: dict[int, float] = defaultdict(float)
    total_rounds: dict[int, float] = defaultdict(float)
    half_wpa: tuple[dict[int, float], dict[int, float]] = (defaultdict(float), defaultdict(float))
    half_kills: tuple[dict[int, float], dict[int, float]] = (defaultdict(float), defaultdict(float))
    half_rounds: tuple[dict[int, float], dict[int, float]] = (
        defaultdict(float),
        defaultdict(float),
    )

    game_half: dict[int, int] = {}
    for r in rounds:
        if r.game_id not in game_half:
            game_half[r.game_id] = len(game_half) % 2

    for r in rounds:
        h = game_half[r.game_id]
        for pid in r.roster:
            total_rounds[pid] += 1
            half_rounds[h][pid] += 1
        for before, after in zip(r.steps, r.steps[1:], strict=False):
            killer = after.killer
            if killer is None:
                continue
            killer_team = r.roster.get(killer)
            if killer_team is None:
                continue
            s = r.side(killer_team)
            o = 1 - s
            delta = table.p(after.alive[s], after.alive[o]) - table.p(
                before.alive[s], before.alive[o]
            )
            total_wpa[killer] += delta
            total_kills[killer] += 1
            half_wpa[h][killer] += delta
            half_kills[h][killer] += 1

    leaders = sorted(
        (
            {
                "player_id": pid,
                "rounds": int(total_rounds[pid]),
                "kills": int(total_kills.get(pid, 0.0)),
                "wpa": round(total_wpa.get(pid, 0.0), 3),
                "wpa_per_round": round(total_wpa.get(pid, 0.0) / total_rounds[pid], 4),
                "kills_per_round": round(total_kills.get(pid, 0.0) / total_rounds[pid], 4),
            }
            for pid in total_rounds
            if total_rounds[pid] >= MIN_SPLIT_ROUNDS
        ),
        key=lambda row: row["wpa_per_round"],
        reverse=True,
    )

    return {
        "n_rounds": len(rounds),
        "n_players": len(leaders),
        "min_rounds": MIN_SPLIT_ROUNDS,
        "leaders": leaders,
        "reliability": _reliability(half_wpa, half_kills, half_rounds),
    }


def _reliability(
    half_wpa: tuple[dict[int, float], dict[int, float]],
    half_kills: tuple[dict[int, float], dict[int, float]],
    half_rounds: tuple[dict[int, float], dict[int, float]],
) -> dict[str, Any]:
    """Split-half: does a rate measured on half a player's games predict the
    other half? Reported for kill rate, for WPA, and for WPA with kill rate
    regressed out — the last being the only one that asks whether WPA carries
    information kills do not.

    Raw split-half correlations understate a full-length metric, so each is also
    reported Spearman-Brown corrected. And because a null here is a claim, the
    correlation this many players could have detected is reported next to it.
    """
    pids = [
        p
        for p in half_rounds[0]
        if half_rounds[0].get(p, 0.0) >= MIN_SPLIT_ROUNDS
        and half_rounds[1].get(p, 0.0) >= MIN_SPLIT_ROUNDS
    ]
    n = len(pids)
    if n < 30:
        return {"available": False, "reason": "too few players with both halves", "n_players": n}

    def rate(source: dict[int, float], h: int) -> FloatArray:
        return np.array([source.get(p, 0.0) / half_rounds[h][p] for p in pids], dtype=float)

    k0, k1 = rate(half_kills[0], 0), rate(half_kills[1], 1)
    w0, w1 = rate(half_wpa[0], 0), rate(half_wpa[1], 1)

    def residual(w: FloatArray, k: FloatArray) -> FloatArray:
        slope, intercept = np.polyfit(k, w, 1)
        return np.asarray(w - (slope * k + intercept))

    r0, r1 = residual(w0, k0), residual(w1, k1)

    # Ordered by what each player measured, never by who they are. A seeded
    # bootstrap draws *positions*, so an interval computed over players in the
    # order they happened to appear in the archive moves whenever a reload
    # renumbers the rows underneath — the correlations do not move, and only the
    # bounds do, which is how this was found. Ties are two players carrying the
    # same numbers, and those are interchangeable to any resample.
    order = np.lexsort((r1, r0, w1, w0, k1, k0))
    k0, k1, w0, w1, r0, r1 = (arr[order] for arr in (k0, k1, w0, w1, r0, r1))

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, n, size=(BOOTSTRAP_B, n))

    def block(x: FloatArray, y: FloatArray, key: str, label: str) -> dict[str, Any]:
        r = float(np.corrcoef(x, y)[0, 1])
        draws = np.array([float(np.corrcoef(x[row], y[row])[0, 1]) for row in idx])
        lo, hi = np.percentile(draws, [2.5, 97.5])
        return {
            "key": key,
            "what": label,
            "r": round(r, 3),
            "lo": round(float(lo), 3),
            "hi": round(float(hi), 3),
            # Spearman-Brown extrapolates a half-length reliability to full
            # length. It is only meaningful for a metric that reproduces itself
            # at all; on a negative correlation it returns a number that looks
            # like a reliability and is not one, so it is withheld.
            "spearman_brown": round(2.0 * r / (1.0 + r), 3) if r > 0.0 else None,
            "excludes_zero": bool(lo > 0.0 or hi < 0.0),
        }

    # Fisher-z: SE of a correlation is 1/sqrt(n-3), so the smallest r reaching
    # 80% power at a two-sided 5% level is tanh of that many standard errors.
    detectable = float(np.tanh(POWER_FACTOR / math.sqrt(n - 3)))

    return {
        "available": True,
        "n_players": n,
        "min_rounds": MIN_SPLIT_ROUNDS,
        "split": "by game — alternating games, so no round is split from its own map",
        "bootstrap_b": BOOTSTRAP_B,
        "cells": [
            block(k0, k1, "kills", "kills per round, half vs half"),
            block(w0, w1, "wpa", "WPA per round, half vs half"),
            block(r0, r1, "wpa_resid", "WPA per round with kill rate removed, half vs half"),
        ],
        "corr_wpa_kills": round(
            float(np.corrcoef(np.r_[k0, k1], np.r_[w0, w1])[0, 1]),
            4,
        ),
        "r_detectable": round(detectable, 3),
        "criterion": "smallest split-half correlation reaching 80% power at a two-sided 5% level",
    }


def build_artifacts(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, dict[str, Any]]:
    """Everything this model publishes, keyed by artifact name."""
    rounds = load_rounds(conn)
    if not rounds:
        return {}
    table = StateTable(state_rows(rounds))
    return {
        "round_win_prob": {
            "mode": MODE_SND,
            "scope": "IW 2017 and WWII 2018 — the only titles with a kill feed, and SnD is"
            " the only mode in them whose rounds are a unit of play",
            "bomb_state": "not in the feed: no plant or defuse events exist. A round still"
            f" alive at {REGULATION_MS // 1000}s implies a plant, but the feed never says"
            " which side planted, and a bomb indicator that cannot be attributed to a team"
            " is symmetric under swapping the teams while the target is antisymmetric — so"
            " it enters at zero. Measured, not assumed: see the survivors_bomb row.",
            "table": state_table_artifact(rounds),
            "backtest": walk_forward(rounds),
        },
        "round_wpa": wpa(rounds, table),
        "round_timeline": timeline_artifact(rounds, table),
    }
