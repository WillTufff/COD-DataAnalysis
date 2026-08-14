"""Segment win probability. Spec: /methodology#segment-win-probability.

`roundwp.py` predicts a Search & Destroy round from the survivors left standing
in it. This module goes one level up and predicts the *map* from the segments
inside it: P(win this map | the score state right now), for Hardpoint hills,
Control rounds and Search & Destroy rounds across the CDL era.

The input is `game_segments`, which migration 0016 parsed out of the stored Cito
responses. It cost no API calls — the bytes were already on disk and the
transform was discarding them.

**Search & Destroy is the reason this module exists in the shape it does.** SnD
is the one mode `roundwp.py` already models, for 2017-2018, from a completely
different source: a kill feed rather than a team round summary. Fitting the
modern rounds here puts the same quantity, for the same mode, on two independent
sources a league era apart. The two tables are published side by side and the
agreement between them is a finding either way.

**Resolution stops at the team, and that kills a feature this project wanted.**
`teamGameStats` reports one row per team per segment; Cito reports player box
scores per map. Nothing in the record locates a player action inside a segment.
A per-kill leverage weight is therefore not implementable from this data and is
not attempted. What survives is a **map-level** competitiveness weight — the
mean distance of the map's win probability from a coin flip — which removes
blowout maps rather than decided minutes inside close ones. It is published and
deliberately consumed by nothing here.

**The model is a counted table and the thing it has to beat is race arithmetic.**
Every state's win rate is counted rather than fitted, for the reason `roundwp.py`
gives: the state space is small, the observations are many, and no smooth
function has anything to buy. The baseline is the same race played forward with
no memory — each remaining round an independent coin, each remaining hill an
independent draw from the league's own distribution of hill score gains, both
enumerated exactly rather than simulated. A lead is worth its arithmetic under
that baseline. The table beats it only if being ahead says something the
arithmetic does not, which is the same null `seriesdyn.py` puts to a series.

**The coverage holes are holes and stay holes.** Seasons 2021, 2022 and 2023
carry no segments at all, 2026 Overload has no block, and Control exists for
2024 and 2025 only. Nothing here interpolates across any of them.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import numpy as np
import psycopg
from numpy.typing import NDArray

from . import resample

FloatArray = NDArray[np.float64]

MODEL = "segment_wp"
VERSION = "1.0.0"

KIND_HILL = "hill"
KIND_SND = "snd_round"
KIND_CONTROL = "control_round"
KINDS: tuple[str, ...] = (KIND_SND, KIND_HILL, KIND_CONTROL)

# How many rounds win the map, per round mode. Both are the league's published
# format and neither has varied across the seasons in the table.
RACE: dict[str, int] = {KIND_SND: 6, KIND_CONTROL: 3}

# Hardpoint is a race to 250 points rather than to a round count.
HILL_TARGET = 250

# The state buckets for Hardpoint, declared in ai/p3b-segment-winprob.md before
# the fit ran so that no width is chosen against the answer. A lead of 30 at
# 100-70 is not a lead of 30 at 240-210, so the state carries the team's own
# score as well as the gap between the two.
SCORE_BUCKET = 25
DIFF_BUCKET = 20
DIFF_CLIP = 100

# Hill score gains are binned to this before the baseline enumerates them. It
# turns a long tail of one-off gains into 82 distinct pairs, which is what makes
# the dynamic program exact and cheap instead of a simulation with a seed.
GAIN_BUCKET = 5

# Add half an observation to each cell before dividing, as roundwp does.
LAPLACE = 0.5

BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20240607  # Major III 2024; any fixed seed works.

# Two-sided 5% at 80% power, same convention as ratings.significance.
Z_ALPHA = 1.959964
Z_POWER = 0.841621
POWER_FACTOR = Z_ALPHA + Z_POWER


def params() -> dict[str, Any]:
    """Everything this model was configured with, for the model_runs row."""
    return {
        "kinds": list(KINDS),
        "race": dict(RACE),
        "hill_target": HILL_TARGET,
        "score_bucket": SCORE_BUCKET,
        "diff_bucket": DIFF_BUCKET,
        "diff_clip": DIFF_CLIP,
        "gain_bucket": GAIN_BUCKET,
        "laplace": LAPLACE,
        "bootstrap_b": BOOTSTRAP_B,
    }


_SEGMENTS_SQL = """
SELECT gs.game_id,
       gs.kind,
       gs.ordinal,
       gs.team_id,
       gs.score,
       gs.won,
       gs.win_type,
       g.winner_team_id,
       g.team1_score,
       g.team2_score,
       se.team1_id,
       se.team2_id,
       se.event_id,
       e.start_date,
       sn.year
  FROM game_segments gs
  JOIN games g   ON g.id = gs.game_id
  JOIN series se ON se.id = g.series_id
  JOIN events e  ON e.id = se.event_id
  JOIN seasons sn ON sn.id = e.season_id
 ORDER BY gs.game_id, gs.kind, gs.ordinal, gs.team_id
"""


@dataclass(frozen=True)
class SegmentMap:
    """One map's segment sequence, from the side of `teams[0]`.

    `steps` holds the state *after* each segment as a (own, opponent) pair: round
    counts on the round kinds, cumulative points on Hardpoint. `won` is whether
    `teams[0]` won the map. Recording one side here and both sides in
    `state_rows` is what keeps the target antisymmetric.
    """

    game_id: int
    kind: str
    year: int
    event_id: int
    played_at: date
    teams: tuple[int, int]
    steps: tuple[tuple[int, int], ...]
    win_types: tuple[str | None, ...]
    winners: tuple[int, ...]
    won: float


@dataclass(frozen=True)
class LoadReport:
    """What the anomaly rules threw away, counted so the write-up can print it.

    Every field here is a rule from ai/p3b-segment-winprob.md §P3b-2 doing its
    job. A silent drop is the failure mode this project has already been bitten
    by, so each one is counted rather than filtered.
    """

    kept: dict[str, int]
    dropped: dict[str, dict[str, int]]
    truncated: int
    segments_dropped: int


# A state row: the bucketed cell, the raw own/opponent state, and the outcome.
StateRow = tuple[tuple[int, int], int, int, float]


def cell(kind: str, own: int, opp: int) -> tuple[int, int]:
    """The table cell a raw state falls in.

    Round kinds are their own cell — a race to six has 21 non-terminal states and
    counting them directly is the whole model. Hardpoint has 251 x 251 raw states
    and ~17,000 observations, so it is bucketed to the widths declared before the
    fit.
    """
    if kind != KIND_HILL:
        return (own, opp)
    lead = min(own // SCORE_BUCKET, HILL_TARGET // SCORE_BUCKET)
    gap = max(-DIFF_CLIP, min(DIFF_CLIP, own - opp))
    return (lead, int(round(gap / DIFF_BUCKET)))


def is_terminal(kind: str, own: int, opp: int) -> bool:
    target = HILL_TARGET if kind == KIND_HILL else RACE[kind]
    return own >= target or opp >= target


def load_maps(
    conn: psycopg.Connection[tuple[object, ...]],
) -> tuple[list[SegmentMap], LoadReport]:
    """Every map whose segment sequence survives the declared anomaly rules.

    Each rejection is recorded under its own reason. Lumping them together is how
    a data defect hides behind a rule that was meant for a different one: the
    first draft of this loader counted 43 entirely one-sided Search & Destroy
    maps as incoherent Hardpoint scores, which is a sentence no reader could have
    caught in the artifact.
    """
    rows = conn.execute(_SEGMENTS_SQL).fetchall()
    per_map: dict[tuple[int, str], dict[int, list[tuple[int, int | None, bool | None, str | None]]]]
    per_map = defaultdict(lambda: defaultdict(list))
    meta: dict[int, tuple[int | None, dict[int, int | None], int, date, int]] = {}
    for row in rows:
        game_id = cast(int, row[0])
        per_map[(game_id, cast(str, row[1]))][cast(int, row[2])].append(
            (
                cast(int, row[3]),
                cast("int | None", row[4]),
                cast("bool | None", row[5]),
                cast("str | None", row[6]),
            )
        )
        meta[game_id] = (
            cast("int | None", row[7]),
            {
                cast(int, row[10]): cast("int | None", row[8]),
                cast(int, row[11]): cast("int | None", row[9]),
            },
            cast(int, row[12]),
            cast(date, row[13]),
            cast(int, row[14]),
        )

    out: list[SegmentMap] = []
    kept: Counter[str] = Counter()
    rejected: dict[str, Counter[str]] = defaultdict(Counter)
    truncated = segments_dropped = 0

    for (game_id, kind), ordinals in sorted(per_map.items()):
        winner_team, map_score, event_id, played_at, year = meta[game_id]
        if winner_team is None:
            rejected[kind]["no_map_winner"] += 1
            continue
        built = _build_hill(kind, ordinals) if kind == KIND_HILL else _build_rounds(kind, ordinals)
        if isinstance(built, str):
            rejected[kind][built] += 1
            continue
        teams, steps, win_types, winners, n_dropped = built
        if winner_team not in teams:
            rejected[kind]["winner_not_a_segment_team"] += 1
            continue
        if kind == KIND_HILL and _score_disagrees(steps[-1], teams, map_score):
            rejected[kind]["score_disagrees_with_the_map"] += 1
            continue
        if n_dropped:
            truncated += 1
            segments_dropped += n_dropped
        if not steps:
            rejected[kind]["nothing_scorable"] += 1
            continue
        out.append(
            SegmentMap(
                game_id=game_id,
                kind=kind,
                year=year,
                event_id=event_id,
                played_at=played_at,
                teams=teams,
                steps=steps,
                win_types=win_types,
                winners=winners,
                won=1.0 if winner_team == teams[0] else 0.0,
            )
        )
        kept[kind] += 1

    return out, LoadReport(
        kept=dict(kept),
        dropped={k: dict(v) for k, v in sorted(rejected.items())},
        truncated=truncated,
        segments_dropped=segments_dropped,
    )


def _score_disagrees(
    final: tuple[int, int], teams: tuple[int, int], map_score: dict[int, int | None]
) -> bool:
    """Whether a Hardpoint map's last cumulative score contradicts the map score.

    Two maps in the table do contradict it. Both are dropped rather than
    repaired. Eight more have a hill series and no recorded map score at all;
    those are not a contradiction and are kept. The segments could supply the
    missing score, and doing so is an ingestion change with its own check, not
    something a model quietly does on the way past.
    """
    for team, seen in zip(teams, final, strict=True):
        recorded = map_score.get(team)
        if recorded is not None and recorded != seen:
            return True
    return False


def _build_rounds(
    kind: str,
    ordinals: dict[int, list[tuple[int, int | None, bool | None, str | None]]],
) -> (
    tuple[
        tuple[int, int], tuple[tuple[int, int], ...], tuple[str | None, ...], tuple[int, ...], int
    ]
    | str
):
    """A round map's state sequence, truncated at the first unscorable round.

    A round counts only when both teams have a row and exactly one of them says
    it won. A round that fails is not skipped: the score after an unknown outcome
    is itself unknown, so everything from that round on is unknowable and the map
    keeps its prefix. 401 SnD rounds arrive one-sided and 3 more are claimed by
    neither side; this is the rule that handles them.

    Rejections come back as a reason rather than as None. 43 Search & Destroy
    maps are one-sided from the first round to the last, so the source never
    names the second team at all and there is no prefix to keep. That is a
    different failure from a round going missing in the middle of a map, and the
    artifact says which is which.
    """
    teams = sorted({t for side in ordinals.values() for t, _s, _w, _wt in side})
    if len(teams) != 2:
        return "only_one_team_in_the_source"
    pair = (teams[0], teams[1])
    n = max(ordinals)
    steps: list[tuple[int, int]] = []
    types: list[str | None] = []
    winners: list[int] = []
    score = [0, 0]
    good = 0
    for ordinal in range(1, n + 1):
        side = ordinals.get(ordinal, [])
        claims = [t for t, _s, won, _wt in side if won]
        if len(side) != 2 or len(claims) != 1:
            break
        idx = 0 if claims[0] == pair[0] else 1
        score[idx] += 1
        steps.append((score[0], score[1]))
        winners.append(idx)
        types.append(next((wt for t, _s, won, wt in side if won), None))
        good += 1
    return pair, tuple(steps), tuple(types), tuple(winners), n - good


def _build_hill(
    kind: str,
    ordinals: dict[int, list[tuple[int, int | None, bool | None, str | None]]],
) -> (
    tuple[
        tuple[int, int], tuple[tuple[int, int], ...], tuple[str | None, ...], tuple[int, ...], int
    ]
    | str
):
    """A Hardpoint map's cumulative score sequence, or the reason it is unusable.

    `score` is cumulative, so it cannot decrease and it cannot pass 250. One map
    in the table does decrease. It is dropped rather than repaired.
    """
    teams = sorted({t for side in ordinals.values() for t, _s, _w, _wt in side})
    if len(teams) != 2:
        return "only_one_team_in_the_source"
    pair = (teams[0], teams[1])
    n = max(ordinals)
    steps: list[tuple[int, int]] = []
    last = [0, 0]
    for ordinal in range(1, n + 1):
        side = ordinals.get(ordinal, [])
        if len(side) != 2:
            return "a_hill_missing_a_side"
        state = list(last)
        for team, score, _won, _wt in side:
            if score is None:
                return "a_hill_with_no_score"
            state[0 if team == pair[0] else 1] = score
        if state[0] < last[0] or state[1] < last[1]:
            return "the_cumulative_score_decreases"
        if state[0] > HILL_TARGET or state[1] > HILL_TARGET:
            return "the_cumulative_score_passes_250"
        last = state
        steps.append((state[0], state[1]))
    return pair, tuple(steps), (None,) * len(steps), (), 0


def state_rows(maps: Iterable[SegmentMap], kind: str) -> list[StateRow]:
    """Training rows: both sides of every non-terminal state of every map.

    Recording both perspectives makes the target antisymmetric by construction,
    for the reason roundwp gives — P(a beats b) and P(b beats a) are one
    observation seen twice — and it is why a tied state comes out at exactly
    0.500. Terminal states are excluded: the map is over and there is nothing
    left to predict.
    """
    out: list[StateRow] = []
    for m in maps:
        if m.kind != kind:
            continue
        for own, opp in m.steps:
            if is_terminal(kind, own, opp):
                continue
            out.append((cell(kind, own, opp), own, opp, m.won))
            out.append((cell(kind, opp, own), opp, own, 1.0 - m.won))
    return out


class StateTable:
    """Counted win rate per state cell. The published model."""

    def __init__(self, kind: str, rows: Iterable[StateRow], laplace: float = LAPLACE) -> None:
        cells: dict[tuple[int, int], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for key, _own, _opp, won in rows:
            counts = cells[key]
            counts[0] += 1.0
            counts[1] += won
        self.kind = kind
        self.cells = {k: (n, w) for k, (n, w) in cells.items()}
        self._laplace = laplace

    def p(self, own: int, opp: int) -> float:
        """P(the side holding `own` wins the map), 1 and 0 at the terminals."""
        target = HILL_TARGET if self.kind == KIND_HILL else RACE[self.kind]
        if own >= target:
            return 1.0
        if opp >= target:
            return 0.0
        n, w = self.cells.get(cell(self.kind, own, opp), (0.0, 0.0))
        return (w + self._laplace) / (n + 2.0 * self._laplace)

    def predict(self, rows: Sequence[StateRow]) -> FloatArray:
        return np.array([self.p(own, opp) for _k, own, opp, _y in rows], dtype=float)


class RaceBaseline:
    """The same race played forward with no memory. What the table has to beat.

    For a round mode this is exact and needs nothing from the data: every
    remaining round is an independent coin, and because each map is recorded from
    both sides the pooled per-round rate is 0.500 by construction rather than by
    assumption. For Hardpoint it is a dynamic program over the league's own
    distribution of per-hill score gains, binned to `GAIN_BUCKET` and symmetrised
    across the two sides, run from the target backwards. Neither is simulated, so
    neither needs a seed.

    A baseline of this shape is what separates "a lead is worth something" from
    "a lead is worth *more* than the arithmetic says it is". Only the second is a
    finding.
    """

    def __init__(self, kind: str, maps: Sequence[SegmentMap]) -> None:
        self.kind = kind
        if kind == KIND_HILL:
            self._grid = _hill_grid(_gain_pairs(maps))
        else:
            self._grid = _race_grid(RACE[kind])

    def p(self, own: int, opp: int) -> float:
        target = HILL_TARGET if self.kind == KIND_HILL else RACE[self.kind]
        return float(self._grid[min(own, target), min(opp, target)])

    def predict(self, rows: Sequence[StateRow]) -> FloatArray:
        return np.array([self.p(own, opp) for _k, own, opp, _y in rows], dtype=float)


def _race_grid(target: int) -> FloatArray:
    """P(win a race to `target` from every state), each round a fair coin."""
    grid = np.zeros((target + 1, target + 1), dtype=float)
    grid[target, :] = 1.0
    grid[:, target] = 0.0
    grid[target, target] = 0.5  # unreachable; both sides cannot win the map
    for own in range(target - 1, -1, -1):
        for opp in range(target - 1, -1, -1):
            grid[own, opp] = 0.5 * grid[own + 1, opp] + 0.5 * grid[own, opp + 1]
    return grid


def _gain_pairs(maps: Sequence[SegmentMap]) -> list[tuple[int, int, float]]:
    """The league's per-hill score gains, binned and symmetrised, with weights.

    Symmetrising is not cosmetic: it is what makes the baseline antisymmetric, so
    that the baseline's own tied states sit at exactly 0.500 and the comparison
    against the table is not measuring a difference in that convention. Hills
    where neither side scored are dropped, because they move no state and would
    make the dynamic program cyclic.
    """
    counts: Counter[tuple[int, int]] = Counter()
    for m in maps:
        if m.kind != KIND_HILL:
            continue
        prev = (0, 0)
        for own, opp in m.steps:
            ga, gb = own - prev[0], opp - prev[1]
            prev = (own, opp)
            ba, bb = ga // GAIN_BUCKET * GAIN_BUCKET, gb // GAIN_BUCKET * GAIN_BUCKET
            if ba <= 0 and bb <= 0:
                continue
            counts[(ba, bb)] += 1
            counts[(bb, ba)] += 1
    total = float(sum(counts.values()))
    if not total:
        return []
    return [(a, b, n / total) for (a, b), n in sorted(counts.items())]


def _hill_grid(pairs: Sequence[tuple[int, int, float]]) -> FloatArray:
    """P(win the race to 250) from every score pair, hills drawn independently.

    Solved backwards over the total points on the board. Every transition adds at
    least one bucket of points to one side, so a state's successors always have a
    strictly larger total and are already known when it is reached.
    """
    n = HILL_TARGET + 1
    grid = np.full((n, n), 0.5, dtype=float)
    grid[HILL_TARGET, :] = 1.0
    grid[:, HILL_TARGET] = 0.0
    grid[HILL_TARGET, HILL_TARGET] = 0.5
    if not pairs:
        return grid

    gains = np.array([(a, b) for a, b, _w in pairs], dtype=np.int64)
    weights = np.array([w for _a, _b, w in pairs], dtype=float)
    for total in range(2 * HILL_TARGET - 1, -1, -1):
        lo = max(0, total - (HILL_TARGET - 1))
        hi = min(HILL_TARGET - 1, total)
        if lo > hi:
            continue
        own = np.arange(lo, hi + 1, dtype=np.int64)
        opp = total - own
        acc = np.zeros(own.shape, dtype=float)
        for (ga, gb), w in zip(gains, weights, strict=True):
            na = np.minimum(own + ga, HILL_TARGET)
            nb = np.minimum(opp + gb, HILL_TARGET)
            step = grid[na, nb]
            # Both sides crossing on one hill cannot happen in play — the map
            # ends the moment one of them does — but binning the gains can put a
            # state there. It is given a coin flip rather than a rule invented
            # for it, and the mass involved is a rounding error.
            both = (na >= HILL_TARGET) & (nb >= HILL_TARGET)
            step = np.where(both, 0.5, step)
            acc += w * step
        grid[own, opp] = acc
    return grid


def _brier(p: FloatArray, y: FloatArray) -> float:
    return float(np.mean((p - y) ** 2))


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


def walk_forward(maps: Sequence[SegmentMap], kind: str) -> dict[str, Any]:
    """Fit on every earlier event, score the next one. Never on its own maps.

    The event is the split rather than the map because that is the history a real
    forecaster would have had. The first event has nothing before it, so it is
    trained on and never scored.

    Losses accumulate per *map*, not per state row. A map contributes a dozen
    rows that are the same map seen at successive states and from both sides;
    counting them as independent would shrink every interval by roughly the
    square root of that count and manufacture a result.
    """
    pool = [m for m in maps if m.kind == kind]
    if not pool:
        return {"available": False, "reason": f"no {kind} maps"}

    events = sorted({(m.played_at, m.event_id) for m in pool})
    by_event: dict[int, list[SegmentMap]] = defaultdict(list)
    for m in pool:
        by_event[m.event_id].append(m)

    names = ("state_table", "race_baseline", "coin_flip")
    losses: dict[str, list[float]] = {n: [] for n in names}
    scored: list[SegmentMap] = []
    calibration_rows: list[tuple[float, float]] = []

    for i, (_when, event_id) in enumerate(events):
        if i == 0:
            continue
        train = [m for _w, e in events[:i] for m in by_event[e]]
        train_rows = state_rows(train, kind)
        if not train_rows:
            continue
        table = StateTable(kind, train_rows)
        baseline = RaceBaseline(kind, train)

        for m in by_event[event_id]:
            rows = state_rows([m], kind)
            if not rows:
                continue
            y = np.array([r[3] for r in rows], dtype=float)
            scored.append(m)
            p_table = table.predict(rows)
            losses["state_table"].append(_brier(p_table, y))
            losses["race_baseline"].append(_brier(baseline.predict(rows), y))
            losses["coin_flip"].append(_brier(np.full(len(y), 0.5), y))
            calibration_rows.extend(zip(p_table.tolist(), y.tolist(), strict=True))

    if not scored:
        return {"available": False, "reason": "no event has a prior event to train on"}

    n_maps = len(scored)
    arrays = {k: np.array(v, dtype=float) for k, v in losses.items()}
    # The bootstrap resamples maps, and which map sits at position zero must come
    # from the maps rather than from a surrogate key: `game_id` renumbers on any
    # reload, and an interval that moves with no data behind it is the defect
    # `resample` exists to stop.
    perm = resample.order([arrays[n] for n in names])
    arrays = {k: v[perm] for k, v in arrays.items()}
    rng = resample.stream(BOOTSTRAP_SEED, *(arrays[n] for n in names))
    idx = rng.integers(0, n_maps, size=(BOOTSTRAP_B, n_maps))

    models: list[dict[str, Any]] = []
    for name in names:
        draws = arrays[name][idx].mean(axis=1)
        lo, hi = np.percentile(draws, [2.5, 97.5])
        models.append(
            {
                "model": name,
                "brier": round(float(arrays[name].mean()), 6),
                "brier_lo": round(float(lo), 6),
                "brier_hi": round(float(hi), 6),
            }
        )

    def pair(a: str, b: str) -> dict[str, Any]:
        """`a` minus `b`, with the smallest gap this data could have resolved.

        A null published here has to survive "you were underpowered", so the
        detectable gap is reported next to the observed one rather than left for
        a reader to work out.
        """
        d = arrays[a] - arrays[b]
        draws = d[idx].mean(axis=1)
        lo, hi = np.percentile(draws, [2.5, 97.5])
        se = float(np.std(d, ddof=1)) / math.sqrt(len(d))
        return {
            "a": a,
            "b": b,
            "delta": round(float(d.mean()), 6),
            "lo": round(float(lo), 6),
            "hi": round(float(hi), 6),
            "excludes_zero": bool(lo > 0.0 or hi < 0.0),
            "detectable": round(POWER_FACTOR * se, 6),
        }

    return {
        "available": True,
        "kind": kind,
        "n_maps": n_maps,
        "n_events_scored": len({m.event_id for m in scored}),
        "models": models,
        "pairs": [
            pair("state_table", "race_baseline"),
            pair("state_table", "coin_flip"),
            pair("race_baseline", "coin_flip"),
        ],
        "calibration": _calibration(calibration_rows),
    }


def table_artifact(maps: Sequence[SegmentMap], kind: str) -> dict[str, Any]:
    """The published table for one mode, with the race baseline beside every cell."""
    rows = state_rows(maps, kind)
    table = StateTable(kind, rows)
    baseline = RaceBaseline(kind, maps)
    raw_by_cell: dict[tuple[int, int], tuple[int, int]] = {}
    for key, own, opp, _y in rows:
        raw_by_cell.setdefault(key, (own, opp))

    cells = []
    for key, (n, w) in sorted(table.cells.items()):
        p = w / n
        se = math.sqrt(max(p * (1.0 - p), 0.0) / n)
        own, opp = raw_by_cell[key]
        cells.append(
            {
                "own": key[0],
                "opp": key[1],
                "n": int(n),
                "p": round(p, 4),
                # Each map is counted from both sides, so a cell holds each map
                # twice and the naive binomial SE is optimistic by ~sqrt(2). It is
                # widened to say so rather than quietly understating it.
                "se": round(se * math.sqrt(2.0), 4),
                "baseline": round(baseline.p(own, opp), 4),
            }
        )
    return {
        "kind": kind,
        "n_maps": sum(1 for m in maps if m.kind == kind),
        "n_states": len(rows),
        "laplace": LAPLACE,
        "bucketed": kind == KIND_HILL,
        "cells": cells,
    }


def win_type_artifact(maps: Sequence[SegmentMap], kind: str) -> dict[str, Any]:
    """How each kind of round was won, and what winning it that way was worth.

    The swing is the win probability the winning side gained by taking the round:
    the table read after the round minus the table read before it. Published for
    every `win_type` the source reports, because the distinctions are the finding
    — a Control round that ran out of time is not one won on kills, and an SnD
    round won on a defuse is not one won before the plant, and no published Call
    of Duty analysis separates any of them.
    """
    pool = [m for m in maps if m.kind == kind]
    table = StateTable(kind, state_rows(pool, kind))
    swings: dict[str, list[float]] = defaultdict(list)
    counts: Counter[str] = Counter()
    for m in pool:
        prev = (0, 0)
        for (own, opp), wt, side in zip(m.steps, m.win_types, m.winners, strict=True):
            label = wt or "unreported"
            counts[label] += 1
            before = table.p(prev[0], prev[1]) if side == 0 else table.p(prev[1], prev[0])
            after = table.p(own, opp) if side == 0 else table.p(opp, own)
            swings[label].append(after - before)
            prev = (own, opp)
    total = float(sum(counts.values()))
    return {
        "kind": kind,
        "n_rounds": int(total),
        "types": [
            {
                "win_type": label,
                "n": n,
                "share": round(n / total, 4) if total else None,
                "mean_swing": round(float(np.mean(swings[label])), 4),
            }
            for label, n in counts.most_common()
        ],
    }


def hill_swing_artifact(maps: Sequence[SegmentMap]) -> dict[str, Any]:
    """What each hill rotation was worth, by its position in the map.

    Hardpoint has no `win_type`, so the analogous split is the hill index: how
    much win probability the average hill moved, first to last. It answers "how
    much is a first-hill lead worth" with a number rather than an opinion.
    """
    pool = [m for m in maps if m.kind == KIND_HILL]
    table = StateTable(KIND_HILL, state_rows(pool, KIND_HILL))
    by_index: dict[int, list[float]] = defaultdict(list)
    for m in pool:
        prev = (0, 0)
        for i, (own, opp) in enumerate(m.steps, start=1):
            by_index[i].append(abs(table.p(own, opp) - table.p(prev[0], prev[1])))
            prev = (own, opp)
    return {
        "kind": KIND_HILL,
        "hills": [
            {
                "hill": i,
                "n": len(by_index[i]),
                "mean_abs_swing": round(float(np.mean(by_index[i])), 4),
            }
            for i in sorted(by_index)
        ],
    }


def competitiveness(maps: Sequence[SegmentMap]) -> dict[str, Any]:
    """How far from a coin flip each map spent its time. A map-level weight.

    Defined as the mean of |WP - 0.5| over the map's non-terminal states, read
    from the full-sample table. A map in doubt to the end scores near 0, a
    blowout near 0.5.

    This is the honest core of the per-kill leverage weight the plan withdrew: a
    thirty-kill map in a blowout is not a thirty-kill map in a decider. It is
    coarser than per-kill leverage by exactly the resolution the record lacks,
    because no player action can be located inside a segment. **Nothing consumes
    it here.** The rating is where a weight on a player-map line belongs, and
    wiring an untested weight into the rating is not a thing this phase does.
    """
    tables = {k: StateTable(k, state_rows(maps, k)) for k in KINDS}
    rows: list[dict[str, Any]] = []
    for m in maps:
        table = tables[m.kind]
        vals = [
            abs(table.p(own, opp) - 0.5)
            for own, opp in m.steps
            if not is_terminal(m.kind, own, opp)
        ]
        if not vals:
            continue
        rows.append(
            {
                "game_id": m.game_id,
                "kind": m.kind,
                "year": m.year,
                "weight": round(float(np.mean(vals)), 4),
            }
        )
    by_kind: dict[str, Any] = {}
    for k in KINDS:
        col = np.array([r["weight"] for r in rows if r["kind"] == k], dtype=float)
        if not len(col):
            continue
        by_kind[k] = {
            "n": int(len(col)),
            "mean": round(float(col.mean()), 4),
            "p10": round(float(np.percentile(col, 10)), 4),
            "median": round(float(np.percentile(col, 50)), 4),
            "p90": round(float(np.percentile(col, 90)), 4),
        }
    return {
        "definition": "mean |WP - 0.5| over the map's non-terminal states",
        "consumed_by": "nothing in this phase; a rating weight belongs with P5 and P6",
        "by_kind": by_kind,
        "maps": sorted(rows, key=lambda r: (r["kind"], r["game_id"])),
    }


_FEED_SQL = """
SELECT gr.game_id, gr.round, gr.winner_side, se.event_id, e.start_date, sn.year
  FROM game_rounds gr
  JOIN games g   ON g.id = gr.game_id
  JOIN game_modes m ON m.id = g.mode_id
  JOIN series se ON se.id = g.series_id
  JOIN events e  ON e.id = se.event_id
  JOIN seasons sn ON sn.id = e.season_id
 WHERE m.name = 'Search & Destroy'
   AND gr.winner_side IN (1, 2)
 ORDER BY gr.game_id, gr.round
"""


def load_feed_snd(
    conn: psycopg.Connection[tuple[object, ...]],
) -> tuple[list[SegmentMap], dict[str, Any]]:
    """The 2018 SnD rounds, shaped like segments so one table fits both.

    This is the comparison the phase exists to make. The kill feed and
    `teamGameStats` are separate sources, separate eras and separate title
    engines, and the round score state is the one quantity both of them resolve.
    Sides here are the feed's own 1 and 2 rather than team ids, which is all the
    state model needs.
    """
    per_game: dict[int, list[tuple[int, int]]] = defaultdict(list)
    meta: dict[int, tuple[int, date, int]] = {}
    for row in conn.execute(_FEED_SQL):
        game_id = cast(int, row[0])
        per_game[game_id].append((cast(int, row[1]), cast(int, row[2])))
        meta[game_id] = (cast(int, row[3]), cast(date, row[4]), cast(int, row[5]))

    out: list[SegmentMap] = []
    race_seen: Counter[tuple[int, int]] = Counter()
    for game_id, rounds in sorted(per_game.items()):
        event_id, played_at, year = meta[game_id]
        score = [0, 0]
        steps: list[tuple[int, int]] = []
        winners: list[int] = []
        for _ordinal, side in sorted(rounds):
            idx = side - 1
            score[idx] += 1
            steps.append((score[0], score[1]))
            winners.append(idx)
        race_seen[(year, max(score))] += 1
        if max(score) != RACE[KIND_SND]:
            continue
        out.append(
            SegmentMap(
                game_id=game_id,
                kind=KIND_SND,
                year=year,
                event_id=event_id,
                played_at=played_at,
                teams=(1, 2),
                steps=tuple(steps),
                win_types=(None,) * len(steps),
                winners=tuple(winners),
                won=1.0 if score[0] > score[1] else 0.0,
            )
        )
    by_season: dict[int, dict[int, int]] = defaultdict(dict)
    for (year, reached), n in sorted(race_seen.items()):
        by_season[year][reached] = n
    return out, {
        "kept": len(out),
        "race_reached_by_season": {str(y): v for y, v in sorted(by_season.items())},
        "excluded": sum(n for (_y, r), n in race_seen.items() if r != RACE[KIND_SND]),
    }


def two_era_snd(
    modern: Sequence[SegmentMap], feed: Sequence[SegmentMap], feed_report: dict[str, Any]
) -> dict[str, Any]:
    """One table per era, cell by cell, and the largest disagreement between them.

    Two independent sources measuring one quantity either agree or they do not,
    and both outcomes are worth publishing. The comparison is only as good as its
    thinnest cell, so every cell carries its count and the widest gap is quoted
    against the standard error of that gap rather than by eye.

    **2017 is not in it, and that is a format difference rather than a data gap.**
    92 of the 93 Infinite Warfare maps in the feed end the moment a side reaches
    five rounds, so CWL 2017 played Search & Destroy as a race to five. A state
    of 4-3 in a race to five is not the state 4-3 in a race to six — it is one
    round from the map rather than two — so pooling the two would compare two
    different games. The comparison is therefore WWII 2018 against the CDL era,
    both races to six, and the 2017 maps are counted here rather than dropped
    quietly.
    """
    a = StateTable(KIND_SND, state_rows(modern, KIND_SND))
    b = StateTable(KIND_SND, state_rows(feed, KIND_SND))
    cells = []
    worst: dict[str, Any] | None = None
    for key in sorted(set(a.cells) | set(b.cells)):
        na, wa = a.cells.get(key, (0.0, 0.0))
        nb, wb = b.cells.get(key, (0.0, 0.0))
        if na < 1 or nb < 1:
            continue
        pa, pb = wa / na, wb / nb
        # Each map is counted from both sides, so both counts are doubled and
        # both variances are understated by the same factor of two.
        se = math.sqrt(2.0 * pa * (1 - pa) / na + 2.0 * pb * (1 - pb) / nb)
        delta = round(pa - pb, 4)
        row: dict[str, Any] = {
            "own": key[0],
            "opp": key[1],
            "modern_n": int(na),
            "modern_p": round(pa, 4),
            "feed_n": int(nb),
            "feed_p": round(pb, 4),
            "delta": delta,
            "z": round((pa - pb) / se, 2) if se > 0 else None,
        }
        cells.append(row)
        if worst is None or abs(delta) > abs(cast(float, worst["delta"])):
            worst = row
    return {
        "modern": {"n_maps": len(modern), "seasons": sorted({m.year for m in modern})},
        "feed": {
            "n_maps": len(feed),
            "seasons": sorted({m.year for m in feed}),
            "excluded_for_a_different_race": feed_report["excluded"],
            "race_reached_by_season": feed_report["race_reached_by_season"],
        },
        "cells": cells,
        "largest_disagreement": worst,
    }


def build_artifacts(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, dict[str, Any]]:
    """Everything this model publishes, keyed by artifact name."""
    maps, report = load_maps(conn)
    if not maps:
        return {}
    feed, feed_report = load_feed_snd(conn)

    by_mode: dict[str, Any] = {}
    for kind in KINDS:
        if not any(m.kind == kind for m in maps):
            continue
        by_mode[kind] = {
            "table": table_artifact(maps, kind),
            "backtest": walk_forward(maps, kind),
            "seasons": sorted({m.year for m in maps if m.kind == kind}),
        }
        if kind == KIND_HILL:
            by_mode[kind]["swing"] = hill_swing_artifact(maps)
        else:
            by_mode[kind]["win_types"] = win_type_artifact(maps, kind)

    return {
        "segment_win_prob": {
            "scope": "the CDL era, from teamGameStats: Hardpoint hills, Control rounds and"
            " Search & Destroy rounds. Team-resolved only — the record locates no player"
            " inside a segment, so this is a team quantity and a map-level weight, never a"
            " per-kill one.",
            "holes": {
                "seasons_absent": [2021, 2022, 2023],
                "hardpoint_2026": "Overload has no teamGameStats block",
                "control_seasons": [2024, 2025],
                "rule": "printed, never interpolated",
            },
            "anomaly_rules": {
                "round": "both teams present and exactly one winner, else the map is"
                " truncated at that round and keeps its prefix",
                "hill": "cumulative score must not decrease or pass 250, else the map is dropped",
                "map": "a map with no recorded winner is dropped",
                "maps_truncated": report.truncated,
                "segments_dropped": report.segments_dropped,
                "maps_kept": report.kept,
                "maps_dropped": report.dropped,
            },
            "by_mode": by_mode,
            "two_era_snd": two_era_snd([m for m in maps if m.kind == KIND_SND], feed, feed_report),
        },
        "segment_competitiveness": competitiveness(maps),
    }
