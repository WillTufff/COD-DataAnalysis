"""Five pass-or-fail tests a published all-time board has to answer.

Written before the rebuild changed a single weight, against the frozen anchor
set in `anchors.py`. They exist because "the list looks wrong" is not a test and
cannot be argued with, while "a tier A anchor sits outside the top 25" can.

What they are not: a target. A failure sends the formula back and never the
player, and no weight may be chosen to move one anchor. Only a structural change
— a component exists or does not, a cohort rule is fixed or not — may follow a
run of these, and the reason goes in the methodology document with it.

A test that cannot be answered from the data returns `inconclusive`, which is
not a pass. `unearned_top_ten` needs a complete championship record, and the
event rosters stop in 2016, so it reports inconclusive until the league-era
rosters land.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, cast

import psycopg

from ..ratings.preflight import load_seasons
from . import anchors
from .awards import TOP_TIER

Conn = psycopg.Connection[tuple[object, ...]]

# A tier A anchor below this rank is a failure. The board publishes far more
# than 25 careers; this is the depth at which "not on the list" stops being a
# matter of taste.
TOP_N = 25
# The band the unearned test polices.
TOP_TEN = 10
# The board rows the rank correlation is measured over.
CORRELATION_DEPTH = 40
# Top-25 peaks per era, over that era's share of player-seasons. The ratio is
# read in whichever direction is larger: an era that takes three times its share
# of the top and an era that takes a third of it are the same defect, and only
# one of them is visible if the test looks one way. An era with a real share of
# the archive and no peak at all is the limiting case, and it fails.
MAX_ERA_SKEW = 3.0

PASS = "pass"
FAIL = "fail"
INCONCLUSIVE = "inconclusive"
REPORT = "report"

_BOARD_SQL = """
SELECT r.player_id, p.handle, r.total, r.peak, r.peak_season_id, r.n_seasons
FROM player_career_rank r
JOIN players p ON p.id = r.player_id
WHERE r.run_id = (SELECT max(id) FROM model_runs WHERE model = 'career_rank')
  AND r.qualified
ORDER BY r.total DESC
"""

_TOP_AWARDS_SQL = """
SELECT player_id, count(*)
FROM player_awards
WHERE player_id IS NOT NULL AND award = ANY(%s)
GROUP BY player_id
"""

_SEASON_ROWS_SQL = """
SELECT season_id, count(DISTINCT player_id)
FROM player_season_adjusted
WHERE mode_id IS NULL
  AND run_id = (SELECT max(run_id) FROM player_season_adjusted)
GROUP BY season_id
"""


@dataclass(frozen=True)
class Result:
    name: str
    verdict: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "test": self.name,
            "verdict": self.verdict,
            "summary": self.summary,
            **self.detail,
        }


@dataclass(frozen=True)
class Board:
    rows: list[dict[str, Any]]

    def rank_of(self, player_id: int | None) -> int | None:
        if player_id is None:
            return None
        for rank, row in enumerate(self.rows, start=1):
            if row["player_id"] == player_id:
                return rank
        return None

    def top(self, depth: int) -> list[dict[str, Any]]:
        return self.rows[:depth]


def load_board(conn: Conn) -> Board:
    return Board(
        [
            {
                "player_id": cast(int, row[0]),
                "handle": str(row[1]),
                "total": float(cast(float, row[2])),
                "peak": float(cast(float, row[3])),
                "peak_season_id": cast(int, row[4]),
                "n_seasons": cast(int, row[5]),
            }
            for row in conn.execute(_BOARD_SQL).fetchall()
        ]
    )


def absent_legend(board: Board, anchor_set: dict[str, Any]) -> Result:
    """A consensus anchor the board does not rank near the top."""
    absent: list[dict[str, Any]] = []
    for player in anchor_set["players"]:
        if player["tier"] != "A":
            continue
        rank = board.rank_of(player["player_id"])
        if rank is None or rank > TOP_N:
            absent.append({"handle": player["handle"], "rank": rank})
    verdict = FAIL if absent else PASS
    return Result(
        "absent_legend",
        verdict,
        f"{len(absent)} of {anchor_set['tier_counts'].get('A', 0)} tier A anchors "
        f"sit outside the top {TOP_N}",
        {"top_n": TOP_N, "absent": absent},
    )


def unearned_top_ten(conn: Conn, board: Board, anchor_set: dict[str, Any]) -> Result:
    """A top-ten career with no championship, no top-tier award and no mention.

    Blocked while the championship record is partial: a player whose rosters
    were never loaded would read as having won nothing, and the test would
    convict the archive rather than the formula.
    """
    if not anchors.rings_are_complete(conn):
        window = conn.execute(
            "SELECT max(s.year) FROM event_rosters r"
            " JOIN events e ON e.id = r.event_id JOIN seasons s ON s.id = e.season_id"
        ).fetchone()
        covered_to = window[0] if window else None
        return Result(
            "unearned_top_ten",
            INCONCLUSIVE,
            "championships are only attributable through "
            f"{covered_to}, so a zero here is missing data and not a fact",
            {"rings_covered_to": covered_to},
        )

    named = {str(p["handle"]).casefold() for p in anchor_set["players"]}
    wins = {pid: facts["event_wins"] for pid, facts in _resume_by_id(conn, board).items()}
    top_awards: dict[int, int] = {
        cast(int, row[0]): cast(int, row[1])
        for row in conn.execute(_TOP_AWARDS_SQL, (sorted(TOP_TIER),)).fetchall()
    }

    unearned = [
        {"handle": row["handle"], "rank": rank}
        for rank, row in enumerate(board.top(TOP_TEN), start=1)
        if wins.get(row["player_id"], 0) == 0
        and int(top_awards.get(row["player_id"], 0)) == 0
        and str(row["handle"]).casefold() not in named
    ]
    return Result(
        "unearned_top_ten",
        FAIL if unearned else PASS,
        f"{len(unearned)} of the top {TOP_TEN} have no ring, no top-tier award "
        "and no published mention",
        {"unearned": unearned},
    )


def _resume_by_id(conn: Conn, board: Board) -> dict[int, dict[str, Any]]:
    return anchors.resume(conn, [row["player_id"] for row in board.rows])


def rank_correlation(board: Board, anchor_set: dict[str, Any]) -> Result:
    """Spearman correlation against the averaged published rank. Reported only.

    Optimising this would be fitting to the lists, which is the one thing the
    anchor set may not become. It is here so a large move in either direction is
    visible in the record.
    """
    external = {
        player["player_id"]: player["mean_all_time_rank"]
        for player in anchor_set["players"]
        if player["mean_all_time_rank"] is not None and player["player_id"] is not None
    }
    pairs = [
        (rank, external[row["player_id"]])
        for rank, row in enumerate(board.top(CORRELATION_DEPTH), start=1)
        if row["player_id"] in external
    ]
    rho = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
    return Result(
        "rank_correlation",
        REPORT,
        f"rho = {rho:.4f} over {len(pairs)} players" if rho is not None else "too few players",
        {"rho": rho, "n_pairs": len(pairs), "depth": CORRELATION_DEPTH},
    )


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3:
        return None
    a, b = _rank_values(left), _rank_values(right)
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = float(sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True)))
    var_a = float(sum((x - mean_a) ** 2 for x in a))
    var_b = float(sum((y - mean_b) ** 2 for y in b))
    if var_a <= 0.0 or var_b <= 0.0:
        return None
    return cov / (math.sqrt(var_a) * math.sqrt(var_b))


def _rank_values(values: list[float]) -> list[float]:
    """Ranks with ties averaged, so a shared external rank does not bias rho."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(order):
        stop = index
        while stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]:
            stop += 1
        shared = (index + stop) / 2.0 + 1.0
        for position in range(index, stop + 1):
            ranks[order[position]] = shared
        index = stop + 1
    return ranks


def era_balance(conn: Conn, board: Board) -> Result:
    """Top-25 peaks per era, against how much of the archive each era is.

    Measured in both directions. The board withholding an era shows here as an
    era with player-seasons and no peaks, which is the failure it should be.
    """
    seasons = load_seasons(conn)
    season_players: dict[int, int] = {
        cast(int, row[0]): cast(int, row[1]) for row in conn.execute(_SEASON_ROWS_SQL).fetchall()
    }

    exposure: dict[str, int] = {}
    for season_id, players in season_players.items():
        season = seasons.get(season_id)
        if season is None:
            continue
        exposure[season.era_key] = exposure.get(season.era_key, 0) + int(players)
    total_exposure = sum(exposure.values())

    peaks: dict[str, int] = {}
    for row in board.top(TOP_N):
        season = seasons.get(row["peak_season_id"])
        key = season.era_key if season else "unknown"
        peaks[key] = peaks.get(key, 0) + 1
    total_peaks = sum(peaks.values())

    rows: list[dict[str, Any]] = []
    worst = 0.0
    unrepresented: list[str] = []
    for era in sorted(set(exposure) | set(peaks)):
        share_peaks = peaks.get(era, 0) / total_peaks if total_peaks else 0.0
        share_rows = exposure.get(era, 0) / total_exposure if total_exposure else 0.0
        skew: float | None = None
        if share_rows > 0.0 and share_peaks > 0.0:
            skew = max(share_peaks / share_rows, share_rows / share_peaks)
            worst = max(worst, skew)
        elif share_rows > 0.0:
            unrepresented.append(era)
        rows.append(
            {
                "era": era,
                "top25_peaks": peaks.get(era, 0),
                "player_seasons": exposure.get(era, 0),
                "skew": skew,
                "unrepresented": era in unrepresented,
            }
        )
    failed = bool(unrepresented) or worst > MAX_ERA_SKEW
    summary = f"worst era skew {worst:.2f} against a limit of {MAX_ERA_SKEW}"
    if unrepresented:
        summary += f"; no top-{TOP_N} peak in " + ", ".join(unrepresented)
    return Result(
        "era_balance",
        FAIL if failed else PASS,
        summary,
        {
            "max_skew": MAX_ERA_SKEW,
            "worst": worst,
            "unrepresented": unrepresented,
            "eras": rows,
        },
    )


def coverage_honesty(conn: Conn) -> Result:
    """Every published career row says what the archive could see of it.

    A career that starts before the seasons the board scores is not a short
    career, and a row that does not say so is a claim the data cannot support.
    """
    columns = {
        str(row[0])
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'player_career_rank'"
        ).fetchall()
    }
    missing = sorted({"seasons_covered", "coverage_from_year"} - columns)
    return Result(
        "coverage_honesty",
        FAIL if missing else PASS,
        "player_career_rank carries no coverage field"
        if missing
        else "every career row carries its coverage",
        {"missing_columns": missing},
    )


def run(conn: Conn) -> dict[str, Any]:
    """Every test against the frozen anchor set and the published board."""
    anchor_set = anchors.load(conn)
    board = load_board(conn)
    results = [
        absent_legend(board, anchor_set),
        unearned_top_ten(conn, board, anchor_set),
        rank_correlation(board, anchor_set),
        era_balance(conn, board),
        coverage_honesty(conn),
    ]
    gating = [r for r in results if r.verdict != REPORT]
    return {
        "anchor_set": {
            "cut": anchor_set["cut"],
            "sha256": anchor_set["sha256"],
            "frozen_sha256": anchor_set["frozen_sha256"],
            "matches_frozen": anchor_set["sha256"] == anchor_set["frozen_sha256"],
            "unresolved": anchor_set["unresolved"],
            "tier_counts": anchor_set["tier_counts"],
        },
        "board_rows": len(board.rows),
        "passed": sum(1 for r in gating if r.verdict == PASS),
        "failed": sum(1 for r in gating if r.verdict == FAIL),
        "inconclusive": sum(1 for r in gating if r.verdict == INCONCLUSIVE),
        "results": [r.as_dict() for r in results],
    }


def main() -> int:
    from .. import db

    with db.connect() as conn:
        print(json.dumps(run(conn), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
