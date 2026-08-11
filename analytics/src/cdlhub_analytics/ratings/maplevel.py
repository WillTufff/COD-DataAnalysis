"""Team ratings at map level, and one rating per mode. Spec: /methodology#map-elo.

The published team ratings rate 1,310 series while the 5,087 decided maps
underneath them go unrated. That is the smaller half of the point. The larger
half is that a series result is a blend of three or four different games — a
Hardpoint, a Search and Destroy, a Control or Capture the Flag — and Call of
Duty rosters are famously not equally good at all of them. A single number per
team cannot say "top three in Hardpoint, mid-table in Search", and nothing
published anywhere says it either.

Three arms are fitted, all Elo, all on the same maps, all strictly walk-forward:

- **global** — one rating per team, updated once per map. The map-level control:
  it answers "is the extra sample worth anything" without changing the model.
- **mode** — one rating per (team, mode). Full mode specificity, and a fifth of
  the sample behind each number.
- **blend** — the two above, mixed per team by how much mode history it has:
  ``w = n / (n + BLEND_K)`` maps in that mode. It nests both — ``w = 0`` is
  global, ``w = 1`` is mode — so it needs no choice between them, which is why
  it is the arm whose rollup goes to the backtest table.

All three share one K. That is deliberate: the experiment is about how finely
rating state is cut, and giving the mode arm its own K would confound the two.
The sweep re-scores every arm across K anyway, so a reader can check whether the
answer survives — a result that only holds at one K is not a result.

**Staying comparable.** The existing backtest table is series-level, and a map
Brier cannot be read against a series Brier. So each arm also rolls up: at the
moment a series starts, the ratings are frozen, a win probability is computed
for each of the five maps the title's rotation would play, and those are
combined into P(win the series) as a best-of-five. The rotation is a league
rule, known before a ball is thrown; the number of maps actually played is not,
and using it would leak the result — a series that went five was 3-2 by
definition. Nothing from inside the series enters its own series prediction.

**Is specialization real?** A spread of per-mode ratings proves nothing on its
own: fit five noisy numbers per team instead of one and they will differ. So the
spread is tested against a permutation null that shuffles mode labels within
each event, keeping every team, opponent, result, date and the event's own mode
mix, and destroying only the association between a team and which mode it was
playing. If the real spread sits inside that null, the per-mode ratings are
noise wearing five hats, and this module says so.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import product
from typing import Any, cast

import numpy as np
import psycopg

from ..backtest import Prediction, evaluate
from .elo import INITIAL, expected

MODEL = "map_elo"
VERSION = "1.0.0"

# A map carries less information than a series, and there are 3.9 of them per
# series. K is halved from the series-level 32 rather than tuned: the sweep
# below re-scores the grid, but the published value is a declared constant, the
# same rule `sweep.py` states for Elo's K and Glicko-2's tau.
K = 16.0

# Maps in a mode before that mode's own rating carries half the weight in the
# blend. Declared, not fitted; the sweep varies it.
BLEND_K = 40.0

# The arm whose series rollup is published to the backtest table. Chosen because
# it nests the other two, not because of its score.
PUBLISHED_ARM = "blend"

ARMS = ("global", "mode", "blend")

# The mode rotation each title plays, in map order. This is a league rule, not
# an observation of these series: it is what both teams know they are walking
# into. Deriving it from the series being predicted would leak the result, so it
# is declared here and checked against the archive by a test rather than read
# off it. Every title's first three maps are fixed and the last two repeat the
# first two, which is why a best-of-five rollup needs only this.
#
# Maps 1, 2, 4 and 5 are Hardpoint, Search, Hardpoint, Search in every title the
# archive covers, so the third map is the only thing that varies and the only
# thing declared per title. A title missing from this table has no rollup at
# all — the condition `checks.sh` fails on, since a new title arriving without a
# rotation is exactly what should stop a release.
THIRD_MAP: dict[str, str] = {
    "IW": "uplink",
    "WWII": "capture-the-flag",
    "BO4": "control",
    "MW19": "domination",
    "BOCW": "control",
    "VG": "control",
    "MWII": "control",
    "MWIII": "control",
    "BO6": "control",
    "BO7": "overload",
}
ROTATION: dict[str, tuple[str, ...]] = {
    title: ("hardpoint", "search-and-destroy", third, "hardpoint", "search-and-destroy")
    for title, third in THIRD_MAP.items()
}
SERIES_WINS_NEEDED = 3

# A (team, mode) cell enters the specialization table once it has this many maps.
# Below it the cell is mostly its 1500 starting value and says nothing.
MIN_MODE_MAPS = 25

# Permutations of the null that mode does not matter. Each one is a full refit.
N_PERMUTATIONS = 300
PERMUTATION_SEED = 20180121  # CWL New Orleans open final

SWEEP_KS = (4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 32.0, 48.0)
SWEEP_BLEND_KS = (10.0, 20.0, 40.0, 80.0, 160.0)


MAP_SQL = """
SELECT g.id, s.id, s.team1_id, s.team2_id, g.winner_team_id, gm.slug, t.short_name,
       s.played_at, s.event_id, g.ordinal
FROM games g
JOIN series s      ON s.id = g.series_id
JOIN events ev     ON ev.id = s.event_id
JOIN seasons se    ON se.id = ev.season_id
JOIN titles t      ON t.id = se.title_id
JOIN game_modes gm ON gm.id = g.mode_id
WHERE s.team1_id IS NOT NULL AND s.team2_id IS NOT NULL
  AND g.winner_team_id IN (s.team1_id, s.team2_id)   -- undecided maps are never rated
ORDER BY s.played_at, s.id, g.ordinal
"""


@dataclass(frozen=True)
class MapResult:
    """One decided map, with the framing a walk-forward fit needs."""

    game_id: int
    series_id: int
    team1: int
    team2: int
    team1_won: bool
    mode: str
    title: str
    played_at: datetime
    event_id: int
    ordinal: int


def load_maps(conn: psycopg.Connection[tuple[object, ...]]) -> list[MapResult]:
    return [
        MapResult(
            game_id=cast(int, r[0]),
            series_id=cast(int, r[1]),
            team1=cast(int, r[2]),
            team2=cast(int, r[3]),
            team1_won=cast(int, r[4]) == cast(int, r[2]),
            mode=cast(str, r[5]),
            title=cast(str, r[6]),
            played_at=cast(datetime, r[7]),
            event_id=cast(int, r[8]),
            ordinal=cast(int, r[9]),
        )
        for r in conn.execute(MAP_SQL)
    ]


def params() -> dict[str, Any]:
    return {
        "k": K,
        "blend_k": BLEND_K,
        "level": "map",
        "arms": list(ARMS),
        "published_arm": PUBLISHED_ARM,
        "min_mode_maps": MIN_MODE_MAPS,
        "n_permutations": N_PERMUTATIONS,
        "series_wins_needed": SERIES_WINS_NEEDED,
    }


# ----------------------------------------------------------------- the fit


@dataclass
class State:
    """Elo state for all three arms at once.

    Global and per-mode ratings advance independently on the same K, off the
    same results. The blend holds no state of its own — it is a read of the
    other two, weighted by how much mode history the team has — so the three
    arms are three ways of reading one pass over the archive rather than three
    passes that could drift apart.
    """

    k: float = K
    blend_k: float = BLEND_K
    glob: dict[int, float] = field(default_factory=dict)
    per_mode: dict[tuple[int, str], float] = field(default_factory=dict)
    n_maps: dict[tuple[int, str], int] = field(default_factory=dict)

    def global_rating(self, team: int) -> float:
        return self.glob.get(team, INITIAL)

    def mode_rating(self, team: int, mode: str) -> float:
        return self.per_mode.get((team, mode), INITIAL)

    def blend_weight(self, team: int, mode: str) -> float:
        n = self.n_maps.get((team, mode), 0)
        return n / (n + self.blend_k)

    def blend_rating(self, team: int, mode: str) -> float:
        w = self.blend_weight(team, mode)
        return w * self.mode_rating(team, mode) + (1.0 - w) * self.global_rating(team)

    def rating(self, arm: str, team: int, mode: str) -> float:
        match arm:
            case "global":
                return self.global_rating(team)
            case "mode":
                return self.mode_rating(team, mode)
            case "blend":
                return self.blend_rating(team, mode)
        raise ValueError(f"unknown arm: {arm!r}")

    def predict(self, arm: str, a: int, b: int, mode: str) -> float:
        return expected(self.rating(arm, a, mode), self.rating(arm, b, mode))

    def update(self, a: int, b: int, mode: str, a_won: bool) -> None:
        s = 1.0 if a_won else 0.0
        pg = expected(self.global_rating(a), self.global_rating(b))
        ga, gb = self.global_rating(a), self.global_rating(b)
        self.glob[a] = ga + self.k * (s - pg)
        self.glob[b] = gb + self.k * ((1.0 - s) - (1.0 - pg))

        pm = expected(self.mode_rating(a, mode), self.mode_rating(b, mode))
        ma, mb = self.mode_rating(a, mode), self.mode_rating(b, mode)
        self.per_mode[(a, mode)] = ma + self.k * (s - pm)
        self.per_mode[(b, mode)] = mb + self.k * ((1.0 - s) - (1.0 - pm))

        self.n_maps[(a, mode)] = self.n_maps.get((a, mode), 0) + 1
        self.n_maps[(b, mode)] = self.n_maps.get((b, mode), 0) + 1


@dataclass
class Walk:
    """One walk-forward pass: every arm's map and series predictions, and the
    ratings left standing at the end."""

    map_preds: dict[str, dict[int, Prediction]]
    series_preds: dict[str, dict[int, Prediction]]
    state: State
    n_series_rolled: int
    n_series_no_rotation: int
    n_series_other_format: int  # not a race to three, so no best-of-five rollup


def _bo5(ps: Sequence[float]) -> float:
    """P(win at least SERIES_WINS_NEEDED of these independent maps).

    Enumerated rather than summed by a binomial, because the five probabilities
    differ: a Hardpoint specialist meeting a Search specialist is exactly the
    case a per-mode rating exists to describe, and collapsing it to one p would
    throw away the thing being tested.
    """
    total = 0.0
    for outcome in product((True, False), repeat=len(ps)):
        if sum(outcome) < SERIES_WINS_NEEDED:
            continue
        prob = 1.0
        for won, p in zip(outcome, ps, strict=True):
            prob *= p if won else (1.0 - p)
        total += prob
    return total


def _series_blocks(maps: Sequence[MapResult]) -> list[list[MapResult]]:
    """Maps in play order, cut into series. Contiguous by the load ordering."""
    out: list[list[MapResult]] = []
    current: int | None = None
    for m in maps:
        if m.series_id != current:
            out.append([])
            current = m.series_id
        out[-1].append(m)
    return out


def walk_forward(
    maps: Sequence[MapResult],
    k: float = K,
    blend_k: float = BLEND_K,
    lineage: dict[int, int] | None = None,
    modes: Sequence[str] | None = None,
) -> Walk:
    """Rate every map; roll each series up from the ratings that opened it.

    `modes` overrides the mode label of each map positionally, which is how the
    permutation null gets a refit without a second copy of this function.
    """
    lin = lineage or {}
    state = State(k=k, blend_k=blend_k)
    map_preds: dict[str, dict[int, Prediction]] = {arm: {} for arm in ARMS}
    series_preds: dict[str, dict[int, Prediction]] = {arm: {} for arm in ARMS}
    labels = list(modes) if modes is not None else [m.mode for m in maps]
    at = 0
    rolled = 0
    no_rotation = 0
    other_format = 0

    for block in _series_blocks(maps):
        head = block[0]
        l1, l2 = lin.get(head.team1, head.team1), lin.get(head.team2, head.team2)

        # The rollup reads the ratings as they stand *before* the first map, so
        # nothing inside the series informs the series prediction. Series whose
        # maps are archived without a winner are still rolled up — the rollup
        # asks about the series, and the series has a winner.
        #
        # Whether this was a race to three is read from the winner's map count
        # and not from `best_of`, which is unreliable: series are recorded as
        # best-of-seven on five-map scorelines and vice versa. A race to four
        # or a series the archive holds two maps of is counted and skipped,
        # because _bo5 would be answering a question those series did not ask.
        rotation = ROTATION.get(head.title)
        wins1 = sum(1 for m in block if m.team1_won)
        wins2 = len(block) - wins1
        if rotation is None:
            no_rotation += 1
        elif max(wins1, wins2) != SERIES_WINS_NEEDED:
            other_format += 1
        elif wins1 != wins2:
            for arm in ARMS:
                p = _bo5([state.predict(arm, l1, l2, mode) for mode in rotation])
                series_preds[arm][head.series_id] = Prediction(
                    p=p,
                    won=wins1 > wins2,
                    when=head.played_at.date(),
                    series_id=head.series_id,
                )
            rolled += 1

        for m in block:
            mode = labels[at]
            at += 1
            for arm in ARMS:
                map_preds[arm][m.game_id] = Prediction(
                    p=state.predict(arm, l1, l2, mode),
                    won=m.team1_won,
                    when=m.played_at.date(),
                )
            state.update(l1, l2, mode, m.team1_won)

    return Walk(
        map_preds=map_preds,
        series_preds=series_preds,
        state=state,
        n_series_rolled=rolled,
        n_series_no_rotation=no_rotation,
        n_series_other_format=other_format,
    )


# ----------------------------------------------------------------- scoring


def _report(ps: Sequence[Prediction]) -> dict[str, Any]:
    rep = evaluate(list(ps))
    return {
        "n": rep.n,
        "brier": round(rep.brier, 5),
        "log_loss": round(rep.log_loss, 5),
        "accuracy": round(rep.accuracy, 4),
    }


def by_mode(
    maps: Sequence[MapResult], preds: dict[str, dict[int, Prediction]]
) -> list[dict[str, Any]]:
    """Each arm's map Brier within each mode.

    The interesting failure is local: a per-mode rating could be right about
    Search and Destroy — the mode with the least scoreboard signal and the most
    distinct skill — while losing overall on the modes with thinner samples. An
    overall number alone would hide that either way.
    """
    mode_of = {m.game_id: m.mode for m in maps}
    buckets: dict[str, dict[str, list[Prediction]]] = defaultdict(lambda: defaultdict(list))
    for arm, per_game in preds.items():
        for game_id, p in per_game.items():
            buckets[mode_of[game_id]][arm].append(p)
    from .significance import paired_gaps

    out = []
    for mode in sorted(buckets, key=lambda m: -sum(len(v) for v in buckets[m].values())):
        games = [g for g in preds[ARMS[0]] if mode_of[g] == mode]
        # Arm scores are nested rather than spread across the row: one of the
        # arms is called "mode", and so is the column holding the mode slug.
        out.append(
            {
                "mode": mode,
                "n_maps": len(buckets[mode][ARMS[0]]),
                "arms": {arm: _report(buckets[mode][arm]) for arm in ARMS if buckets[mode][arm]},
                # Within a single mode the arms are a few thousandths apart, and
                # one of those gaps runs the other way from the overall table.
                # Reporting it without an interval would be exactly the reading
                # of noise this project objects to elsewhere.
                "gaps": paired_gaps(
                    {arm: {g: preds[arm][g] for g in games} for arm in ARMS}, unit="map"
                ),
            }
        )
    return out


def sweep(
    maps: Sequence[MapResult],
    lineage: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Every arm re-scored across K and the blend constant.

    Sensitivity, not selection — the same rule `sweep.py` states. Its job here
    is specifically to answer the objection that the mode arm lost because K was
    chosen for the global one: if global beats mode at every K, that is about
    the state granularity, which is the question.
    """
    grid = []
    for k in SWEEP_KS:
        walk = walk_forward(maps, k=k, blend_k=BLEND_K, lineage=lineage)
        cell: dict[str, Any] = {"k": k, "blend_k": BLEND_K}
        for arm in ARMS:
            cell[arm] = _report(list(walk.map_preds[arm].values()))
        grid.append(cell)

    blend_grid = []
    for blend_k in SWEEP_BLEND_KS:
        walk = walk_forward(maps, k=K, blend_k=blend_k, lineage=lineage)
        blend_grid.append(
            {"k": K, "blend_k": blend_k, "blend": _report(list(walk.map_preds["blend"].values()))}
        )

    return {
        "note": (
            "Sensitivity analysis, not model selection: the published K and blend "
            "constant are declared and are not chosen from this grid."
        ),
        "published": {"k": K, "blend_k": BLEND_K},
        "k_grid": grid,
        "blend_grid": blend_grid,
        "best_by_brier": {
            arm: min(grid, key=lambda c: cast(float, c[arm]["brier"]))["k"] for arm in ARMS
        },
        # The question the grid exists to answer, reduced to one boolean.
        "mode_beats_global_at_every_k": all(
            c["mode"]["brier"] < c["global"]["brier"] for c in grid
        ),
        "global_beats_mode_at_every_k": all(
            c["global"]["brier"] < c["mode"]["brier"] for c in grid
        ),
    }


# ----------------------------------------------- is mode specialization real?


def _cells(state: State, min_maps: int) -> list[tuple[int, str, float, int]]:
    """(team, mode, rating minus that team's global rating, maps) per qualified cell."""
    out = []
    for (team, mode), n in state.n_maps.items():
        if n < min_maps:
            continue
        out.append((team, mode, state.mode_rating(team, mode) - state.global_rating(team), n))
    return out


def specialization(
    maps: Sequence[MapResult],
    lineage: dict[int, int] | None = None,
    n_permutations: int = N_PERMUTATIONS,
) -> dict[str, Any]:
    """Does a team's rating actually vary by mode, beyond what noise supplies?

    The statistic is the spread — the standard deviation across qualified
    (team, mode) cells of that cell's rating minus the team's global rating. It
    is compared against a null in which mode labels are permuted within each
    event. That keeps every team, opponent, result, date, and the event's own
    mode mix, and breaks only the link between a team and which mode it was
    playing, which is precisely the thing "mode specialization" claims exists.

    A null this construction cannot rule out is that the spread is real but
    entirely a property of how many maps a cell has. That is why the cell floor
    is applied identically inside every permutation rather than fixed to the
    real cells: a permuted refit qualifies its own cells the same way.
    """
    real = walk_forward(maps, lineage=lineage)
    cells = _cells(real.state, MIN_MODE_MAPS)
    if len(cells) < 10:
        return {"available": False, "reason": "too few qualified (team, mode) cells"}
    observed = float(np.std([c[2] for c in cells], ddof=1))

    by_event: dict[int, list[int]] = defaultdict(list)
    for i, m in enumerate(maps):
        by_event[m.event_id].append(i)

    # Events in an order fixed by what they contain, not by when their id first
    # appeared. One generator shuffles every event in turn, so the order decides
    # which draws land on which event — and with `event_id` deciding it, a reload
    # that renumbered events moved this null while no map moved. The date breaks
    # ties, which is the event's own property rather than the loader's.
    events = sorted(
        by_event.values(),
        key=lambda idxs: (sorted(maps[i].mode for i in idxs), maps[idxs[0]].played_at),
    )

    rng = np.random.default_rng(PERMUTATION_SEED)
    base = [m.mode for m in maps]
    null: list[float] = []
    for _ in range(n_permutations):
        shuffled = list(base)
        for idxs in events:
            picked = [base[i] for i in idxs]
            rng.shuffle(picked)
            for slot, mode in zip(idxs, picked, strict=True):
                shuffled[slot] = mode
        perm = walk_forward(maps, lineage=lineage, modes=shuffled)
        perm_cells = _cells(perm.state, MIN_MODE_MAPS)
        if len(perm_cells) >= 10:
            null.append(float(np.std([c[2] for c in perm_cells], ddof=1)))

    if not null:
        return {"available": False, "reason": "no permutation produced enough cells"}
    arr = np.asarray(null, dtype=float)
    null_mean = float(arr.mean())
    null_hi = float(np.percentile(arr, 97.5))
    # Add-one p-value: the observed spread counts as one of its own draws, so a
    # null that is never exceeded reports 1/(B+1) rather than an impossible zero.
    p = float((np.sum(arr >= observed) + 1) / (len(arr) + 1))

    return {
        "available": True,
        "statistic": "SD across qualified (team, mode) cells of mode rating − global rating",
        "min_mode_maps": MIN_MODE_MAPS,
        "n_cells": len(cells),
        "observed_sd": round(observed, 2),
        "null_permutations": len(arr),
        "null_mean_sd": round(null_mean, 2),
        "null_lo": round(float(np.percentile(arr, 2.5)), 2),
        "null_hi": round(null_hi, 2),
        "p_value": round(p, 4),
        "exceeds_null": bool(observed > null_hi),
        # How much of the observed spread survives subtracting what noise alone
        # produces. Reported in rating points because that is the unit a reader
        # can convert: 400 points is a 10-to-1 favourite.
        "excess_sd": round(max(0.0, observed - null_mean), 2),
    }


def mode_table(
    state: State, names: dict[int, str], min_maps: int = MIN_MODE_MAPS
) -> list[dict[str, Any]]:
    """The publishable object: per-team, per-mode rating and its gap from global.

    Sorted by the size of the gap, so the top of the table is the claim the
    module exists to be able to make — this roster is better at this mode than
    it is in general. Whether that ordering means anything is what
    `specialization` decides, and the two are always stored together.
    """
    rows = []
    for team, mode, delta, n in _cells(state, min_maps):
        rows.append(
            {
                "team_id": team,
                "team": names.get(team, str(team)),
                "mode": mode,
                "rating": round(state.mode_rating(team, mode), 1),
                "global_rating": round(state.global_rating(team), 1),
                "delta": round(delta, 1),
                "n_maps": n,
            }
        )
    rows.sort(key=lambda r: -abs(cast(float, r["delta"])))
    return rows


def _team_names(conn: psycopg.Connection[tuple[object, ...]]) -> dict[int, str]:
    rows = conn.execute("SELECT id, name FROM teams").fetchall()
    return {cast(int, r[0]): cast(str, r[1]) for r in rows}


# ----------------------------------------------------------------- artifacts


def build_artifacts(
    conn: psycopg.Connection[tuple[object, ...]],
    maps: Sequence[MapResult],
    lineage: dict[int, int] | None = None,
    series_models: dict[str, list[Prediction]] | None = None,
    n_permutations: int = N_PERMUTATIONS,
) -> tuple[dict[str, Any], list[Prediction]]:
    """Everything this model publishes, plus the predictions the backtest row uses.

    `series_models` is the existing series-level table — Elo, Glicko-2,
    winprob — handed in so the rollup is compared against it with the paired
    intervals `significance.py` provides, on exactly the series both reached,
    rather than by reading two Brier scores off two tables.
    """
    from .significance import paired_gaps

    walk = walk_forward(maps, lineage=lineage)
    map_scores = {arm: _report(list(walk.map_preds[arm].values())) for arm in ARMS}

    map_gaps = paired_gaps({arm: walk.map_preds[arm] for arm in ARMS})
    map_gaps["coin_flip_brier"] = 0.25

    rollup: dict[str, list[Prediction]] = {
        f"map_{arm}": list(walk.series_preds[arm].values()) for arm in ARMS
    }
    combined = {**rollup, **(series_models or {})}
    series_gaps = paired_gaps(
        {
            name: {p.series_id: p for p in preds if p.series_id is not None}
            for name, preds in combined.items()
        }
    )

    art: dict[str, Any] = {
        "map_backtest": {
            "n_maps": len(maps),
            "k": K,
            "blend_k": BLEND_K,
            "published_arm": PUBLISHED_ARM,
            "arms": map_scores,
            "coin_flip_brier": 0.25,
            "gaps": map_gaps,
            "by_mode": by_mode(maps, walk.map_preds),
        },
        "series_rollup": {
            "n_series": walk.n_series_rolled,
            "n_series_no_rotation": walk.n_series_no_rotation,
            "n_series_other_format": walk.n_series_other_format,
            "wins_needed": SERIES_WINS_NEEDED,
            "rotation": {t: list(r) for t, r in ROTATION.items()},
            "method": (
                "ratings frozen at the first map of the series; one win probability per "
                "map of the title's rotation; combined as a best-of-five. The number of "
                "maps actually played is never read — it is the result."
            ),
            "arms": {f"map_{arm}": _report(list(walk.series_preds[arm].values())) for arm in ARMS},
            "gaps": series_gaps,
        },
        "mode_specialization": specialization(maps, lineage, n_permutations),
        "mode_ratings": {
            "min_mode_maps": MIN_MODE_MAPS,
            "rows": mode_table(walk.state, _team_names(conn)),
        },
        "map_sweep": sweep(maps, lineage),
    }
    return art, list(walk.series_preds[PUBLISHED_ARM].values())


def data_through(maps: Sequence[MapResult]) -> date:
    return max(m.played_at for m in maps).date()
