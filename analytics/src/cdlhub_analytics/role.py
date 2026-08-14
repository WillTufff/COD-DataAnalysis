"""Role: who takes the opening fight, what it costs them, and whether style can see it.

The rating is blind to the tradeoff the sport argues about most. An entry takes
the hard first contact so an anchor can hold an easy lane, and every box score
charges both of them the same way for a death. This module measures the trade
where the record allows it, which is less of the record than the plan assumed.

**Role is first contact, not a position on a style axis.** The style axes are
fitted on `kills_pm`, `kill_share`, `kd`, `plus_minus_pm`, `damage_pm`,
`engagement_pm` and `deaths_pm`, so adjusting K/D for a position those columns
define would manufacture the adjustment out of the thing being adjusted.
`first_bloods` and `first_deaths` are counts of who was in the opening
engagement and who lost it, they are in no basis, and they are what the argument
is actually about.

**One mode, because that is where the columns are.** `first_bloods` is present
on every row of every title and non-zero only on Search and Destroy. On
Hardpoint, Control and the rest it is present and all zero, which is the shape
that has caught this project before.

**Two eras, each answering half, and neither answering both.**

- The entry cost needs `first_deaths` to say who *lost* the opening fight, plus
  `non_traded_kills` and `damage` for what recovers it. All three are all-zero
  or absent on every CWL title, so the cost is measured on 2020-2026. Every rate
  there is per map rather than per minute: `games.duration_s` is null on all
  17,732 modern Search and Destroy maps, so no pace is computable.
- The recovery test needs an observed role label, and `fave_weapon` exists only
  on 2017-2019. So whether style carries role at all is asked there.

Trade economy and the role label never coexist in this archive. Saying so is
part of the result, and no number here is carried across the join.

**The weapon table is checked where it can be checked.** Five of the 27 weapons
carry an observed class in `kill_events.weapon_class`; the rest are named here
from the titles they belong to. One of the five contradicted the plan that
commissioned this phase, which is the reason the check exists.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
from numpy.typing import NDArray

from . import resample

FloatArray = NDArray[np.float64]

MODEL = "role"
VERSION = "1.0.0"

SMG = "smg"
AR = "ar"
SNIPER = "sniper"
TACTICAL = "tactical"
OTHER = "other"

# The 27 values `game_player_stats.fave_weapon` takes, by class. Entries in
# VERIFIED are the ones the kill feed also classes; the rest are named from the
# title's own weapon list.
WEAPON_CLASS: dict[str, str] = {
    # WWII
    "PPSh-41": SMG,
    "STG-44": AR,
    "FG 42": AR,
    "BAR": AR,
    "M1941": AR,
    "M1 Garand": AR,
    "M1 Carbine": AR,
    "Kar98k": SNIPER,
    "Springfield": SNIPER,
    # Black Ops 4
    "Saug 9mm": SMG,
    "GKS": SMG,
    "Spitfire": SMG,
    "Maddox RFB": AR,
    "ICR-7": AR,
    "KN-57": AR,
    "Grav": AR,
    "Paladin HB50": SNIPER,
    "Outlaw 308": SNIPER,
    "SwordFish": TACTICAL,
    "ABR 223": TACTICAL,
    "Auger DMR": TACTICAL,
    # Infinite Warfare
    "Erad": SMG,
    "Karma-45": SMG,
    "KBAR-32": AR,
    "NV4": AR,
    "Rampart 17": AR,
    "KBS Longbow": SNIPER,
}

# Weapons the kill feed also classes, so the table above can be wrong about them
# out loud rather than quietly. `KBAR-32` reads `ar` there and the plan that
# commissioned this phase listed it as an SMG.
VERIFIED = frozenset({"Erad", "Karma-45", "KBAR-32", "NV4", "KBS Longbow"})

# The recovery test asks one question, SMG against AR, so the classes that
# answer a different question are not folded into either.
RECOVERED_CLASSES = (SMG, AR)

# A player-season needs this many classed maps before it carries a role label,
# and the modal class needs this share of them before the label is one class.
MIN_WEAPON_MAPS = 20
MIN_DOMINANCE = 0.6

# A player-season needs this many Search and Destroy maps before its contact
# rate is read as a rate.
MIN_SND_MAPS = 30

SND_SLUG = "search-and-destroy"

BOOTSTRAP_B = 1000
BOOTSTRAP_SEED = 20260814


def params() -> dict[str, Any]:
    """What this run was configured with."""
    return {
        "min_weapon_maps": MIN_WEAPON_MAPS,
        "min_dominance": MIN_DOMINANCE,
        "min_snd_maps": MIN_SND_MAPS,
        "bootstrap_b": BOOTSTRAP_B,
        "bootstrap_seed": BOOTSTRAP_SEED,
    }


# ------------------------------------------------------------ the weapon table


def observed_classes(conn: psycopg.Connection[Any]) -> dict[str, str]:
    """The class the kill feed gives each weapon name it shares with the table."""
    rows = conn.execute(
        "SELECT DISTINCT lower(weapon), weapon_class FROM kill_events"
        " WHERE weapon_class IS NOT NULL"
    ).fetchall()
    feed = {str(r[0]): str(r[1]) for r in rows}
    return {name: feed[name.lower()] for name in WEAPON_CLASS if name.lower() in feed}


def table_disagreements(observed: dict[str, str]) -> list[str]:
    """Weapons this module classes differently from the kill feed."""
    return sorted(
        f"{name}: table says {WEAPON_CLASS[name]}, kill feed says {klass}"
        for name, klass in observed.items()
        if WEAPON_CLASS.get(name) != klass
    )


# ----------------------------------------------------------- the populations


@dataclass(frozen=True)
class WeaponSeason:
    """One CWL player-season, with the weapon class it mostly carried."""

    player_id: int
    season_id: int
    label: str
    maps: int
    dominance: float


@dataclass(frozen=True)
class ContactSeason:
    """One modern Search and Destroy player-season, at the opening engagement."""

    player_id: int
    season_id: int
    maps: int
    contact_rate: float
    contact_win_rate: float
    kd: float
    damage_per_map: float
    untraded_rate: float


_WEAPON_SQL = """
SELECT gps.player_id, se.id AS season_id, gps.fave_weapon, count(*) AS n
FROM game_player_stats gps
JOIN games g   ON g.id = gps.game_id
JOIN series s  ON s.id = g.series_id
JOIN events ev ON ev.id = s.event_id
JOIN seasons se ON se.id = ev.season_id
WHERE gps.fave_weapon IS NOT NULL AND se.year < 2020
GROUP BY gps.player_id, se.id, gps.fave_weapon
"""


def load_weapon_seasons(conn: psycopg.Connection[Any]) -> list[WeaponSeason]:
    """CWL player-seasons whose classed maps concentrate on one class."""
    counts: dict[tuple[int, int], dict[str, int]] = {}
    for r in conn.execute(_WEAPON_SQL).fetchall():
        klass = WEAPON_CLASS.get(str(r[2]))
        if klass is None:
            continue
        key = (int(r[0]), int(r[1]))
        counts.setdefault(key, {})[klass] = counts.setdefault(key, {}).get(klass, 0) + int(r[3])

    out: list[WeaponSeason] = []
    for (player_id, season_id), by_class in counts.items():
        total = sum(by_class.values())
        if total < MIN_WEAPON_MAPS:
            continue
        label, n = max(sorted(by_class.items()), key=lambda kv: kv[1])
        dominance = n / total
        if dominance < MIN_DOMINANCE or label not in RECOVERED_CLASSES:
            continue
        out.append(
            WeaponSeason(
                player_id=player_id,
                season_id=season_id,
                label=label,
                maps=total,
                dominance=dominance,
            )
        )
    return sorted(out, key=lambda w: (w.season_id, w.player_id))


_CONTACT_SQL = """
SELECT gps.player_id, se.id AS season_id, count(*) AS maps,
       sum(gps.kills) AS kills, sum(gps.deaths) AS deaths,
       sum(gps.first_bloods) AS first_bloods, sum(gps.first_deaths) AS first_deaths,
       sum(gps.damage) AS damage, sum(gps.non_traded_kills) AS untraded
FROM game_player_stats gps
JOIN games g    ON g.id = gps.game_id
JOIN game_modes gm ON gm.id = g.mode_id
JOIN series s   ON s.id = g.series_id
JOIN events ev  ON ev.id = s.event_id
JOIN seasons se ON se.id = ev.season_id
WHERE gm.slug = %(mode)s AND se.year >= 2020
  AND gps.first_bloods IS NOT NULL AND gps.first_deaths IS NOT NULL
GROUP BY gps.player_id, se.id
HAVING count(*) >= %(min_maps)s AND sum(gps.deaths) > 0
"""


def load_contact_seasons(conn: psycopg.Connection[Any]) -> list[ContactSeason]:
    """Modern Search and Destroy player-seasons, at the opening engagement."""
    out: list[ContactSeason] = []
    for r in conn.execute(_CONTACT_SQL, {"mode": SND_SLUG, "min_maps": MIN_SND_MAPS}).fetchall():
        maps = int(r[2])
        kills, deaths = float(r[3] or 0.0), float(r[4] or 0.0)
        first_bloods, first_deaths = float(r[5] or 0.0), float(r[6] or 0.0)
        damage, untraded = float(r[7] or 0.0), float(r[8] or 0.0)
        contacts = first_bloods + first_deaths
        if contacts <= 0:
            continue
        out.append(
            ContactSeason(
                player_id=int(r[0]),
                season_id=int(r[1]),
                maps=maps,
                contact_rate=contacts / maps,
                contact_win_rate=first_bloods / contacts,
                kd=kills / deaths,
                damage_per_map=damage / maps,
                untraded_rate=untraded / kills if kills > 0 else 0.0,
            )
        )
    return sorted(out, key=lambda c: (c.season_id, c.player_id))


def load_style_axes(
    conn: psycopg.Connection[Any], style_run: int
) -> dict[tuple[int, int], FloatArray]:
    """Every player-season's style position, as a vector in axis order."""
    rows = conn.execute(
        "SELECT player_id, season_id, axis, score FROM player_style_season"
        " WHERE run_id = %s ORDER BY player_id, season_id, axis",
        (style_run,),
    ).fetchall()
    by_key: dict[tuple[int, int], list[tuple[int, float]]] = {}
    for r in rows:
        by_key.setdefault((int(r[0]), int(r[1])), []).append((int(r[2]), float(r[3])))
    return {
        key: np.asarray([score for _, score in sorted(axes)], dtype=float)
        for key, axes in by_key.items()
    }


# ------------------------------------------------------- the recovery test


@dataclass(frozen=True)
class Recovery:
    """What the style axes recover of an observed weapon class."""

    n_seasons: int
    n_players: int
    n_axes: int
    base_rate: float
    accuracy: float
    auc_by_axis: dict[int, float]
    verdict: str


# The rule the recovery rate is read by, fixed before the number was computed.
RECOVERY_CARRIES = 0.75
RECOVERY_AMBIGUOUS = 0.60


def verdict_for(accuracy: float) -> str:
    """The pre-registered reading of a recovery rate."""
    if accuracy >= RECOVERY_CARRIES:
        return "the axes carry role"
    if accuracy >= RECOVERY_AMBIGUOUS:
        return "ambiguous between loose axes and a loose proxy"
    return "the axes do not carry role"


def _discriminant(x: FloatArray, y: NDArray[np.bool_]) -> tuple[FloatArray, float] | None:
    """Fisher's linear discriminant, and the midpoint it separates at."""
    if y.all() or not y.any():
        return None
    pos, neg = x[y], x[~y]
    mu = pos.mean(axis=0) - neg.mean(axis=0)
    centred = np.vstack([pos - pos.mean(axis=0), neg - neg.mean(axis=0)])
    cov = centred.T @ centred / max(1, centred.shape[0] - 2)
    cov = cov + np.eye(cov.shape[0]) * 1e-9
    try:
        w = np.linalg.solve(cov, mu)
    except np.linalg.LinAlgError:
        return None
    cut = 0.5 * float(w @ (pos.mean(axis=0) + neg.mean(axis=0)))
    return w, cut


def _auc(scores: FloatArray, y: NDArray[np.bool_]) -> float:
    """Rank-based area under the ROC curve."""
    if y.all() or not y.any():
        return 0.5
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(scores.size, dtype=float)
    ranks[order] = np.arange(1, scores.size + 1, dtype=float)
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def recovery(
    seasons: Sequence[WeaponSeason], axes: dict[tuple[int, int], FloatArray]
) -> Recovery | None:
    """Held-out recovery of weapon class from the style axes, folded by player.

    A player contributes several seasons, so a fold holds out the player rather
    than the season: a model that had seen one of a player's seasons would be
    scored on how well it remembers them.
    """
    usable = [s for s in seasons if (s.player_id, s.season_id) in axes]
    if not usable:
        return None
    width = min(axes[(s.player_id, s.season_id)].size for s in usable)
    if width == 0:
        return None
    x = np.asarray([axes[(s.player_id, s.season_id)][:width] for s in usable], dtype=float)
    y = np.asarray([s.label == SMG for s in usable], dtype=bool)
    players = np.asarray([s.player_id for s in usable], dtype=np.int64)

    correct = 0
    for player in np.unique(players):
        held = players == player
        fit = _discriminant(x[~held], y[~held])
        if fit is None:
            continue
        w, cut = fit
        correct += int(((x[held] @ w > cut) == y[held]).sum())

    base = float(max(y.mean(), 1.0 - y.mean()))
    accuracy = correct / len(usable)
    return Recovery(
        n_seasons=len(usable),
        n_players=int(np.unique(players).size),
        n_axes=width,
        base_rate=base,
        accuracy=accuracy,
        auc_by_axis={i + 1: _auc(x[:, i], y) for i in range(width)},
        verdict=verdict_for(accuracy),
    )


# --------------------------------------------------------- the entry cost


@dataclass(frozen=True)
class Cost:
    """What one more opening engagement per map goes with, on one outcome."""

    outcome: str
    slope: float
    lo95: float
    hi95: float
    n_seasons: int
    n_players: int


def _standardise_within_season(values: Sequence[float], season_ids: Sequence[int]) -> FloatArray:
    """Each value as a deviation from its own season, in that season's own SD."""
    arr = np.asarray(values, dtype=float)
    seasons = np.asarray(season_ids, dtype=np.int64)
    out = np.zeros_like(arr)
    for season in np.unique(seasons):
        rows = seasons == season
        block = arr[rows]
        sd = float(block.std(ddof=1)) if block.size > 1 else 0.0
        out[rows] = (block - block.mean()) / sd if sd > 0 else 0.0
    return out


def _slope(x: FloatArray, y: FloatArray) -> float:
    var = float(((x - x.mean()) ** 2).sum())
    if var <= 0:
        return 0.0
    return float(((x - x.mean()) * (y - y.mean())).sum() / var)


def entry_cost(seasons: Sequence[ContactSeason], outcome: str) -> Cost | None:
    """The slope of an outcome on contact rate, both taken within season.

    The interval resamples players rather than seasons, because one player's
    seasons are not independent draws.
    """
    if len(seasons) < 10:
        return None
    getters: dict[str, Callable[[ContactSeason], float]] = {
        "kd": lambda s: s.kd,
        "damage_per_map": lambda s: s.damage_per_map,
        "untraded_rate": lambda s: s.untraded_rate,
    }
    getter = getters[outcome]
    season_ids = [s.season_id for s in seasons]
    x = _standardise_within_season([s.contact_rate for s in seasons], season_ids)
    y = _standardise_within_season([getter(s) for s in seasons], season_ids)

    # Players are the resample unit, ordered by what they contain so a renumbered
    # player_id cannot permute the population, and the generator is seeded from
    # the same contents rather than from the keys.
    by_player: dict[int, list[int]] = {}
    for i, s in enumerate(seasons):
        by_player.setdefault(s.player_id, []).append(i)
    blocks = list(by_player.values())
    contact_key = [float(sum(seasons[i].contact_rate for i in rows)) for rows in blocks]
    outcome_key = [float(sum(getter(seasons[i]) for i in rows)) for rows in blocks]
    size_key = [float(len(rows)) for rows in blocks]
    order = resample.order([contact_key, outcome_key, size_key])
    grouped = [blocks[i] for i in order]
    rng = resample.stream(
        BOOTSTRAP_SEED,
        np.asarray([contact_key[i] for i in order], dtype=float),
        np.asarray([outcome_key[i] for i in order], dtype=float),
    )

    draws: list[float] = []
    for _ in range(BOOTSTRAP_B):
        picked = rng.integers(0, len(grouped), size=len(grouped))
        rows = [i for p in picked for i in grouped[p]]
        if len(rows) < 3:
            continue
        draws.append(_slope(x[rows], y[rows]))
    if not draws:
        return None
    lo, hi = np.quantile(np.asarray(draws, dtype=float), [0.025, 0.975])
    return Cost(
        outcome=outcome,
        slope=_slope(x, y),
        lo95=float(lo),
        hi95=float(hi),
        n_seasons=len(seasons),
        n_players=len(grouped),
    )


def adjusted_kd(seasons: Sequence[ContactSeason], cost: Cost | None) -> list[dict[str, float]]:
    """Raw K/D, the K/D the contact rate accounts for, and what is left.

    All three ship together. An adjustment nobody can see the size of is worse
    than no adjustment.
    """
    if cost is None:
        return []
    season_ids = [s.season_id for s in seasons]
    x = _standardise_within_season([s.contact_rate for s in seasons], season_ids)
    raw = _standardise_within_season([s.kd for s in seasons], season_ids)
    return [
        {
            "player_id": float(s.player_id),
            "season_id": float(s.season_id),
            "raw": float(raw[i]),
            "adjustment": float(-cost.slope * x[i]),
            "adjusted": float(raw[i] - cost.slope * x[i]),
        }
        for i, s in enumerate(seasons)
    ]


# How many of the largest adjustments are published beside their raw values, so
# the size of what the role model gives back is readable rather than asserted.
AUDIT_ROWS = 12


def audit(
    conn: psycopg.Connection[Any], seasons: Sequence[ContactSeason], cost: Cost | None
) -> list[dict[str, Any]]:
    """The largest adjustments, each with the raw value it moved and by how much."""
    rows = adjusted_kd(seasons, cost)
    if not rows:
        return []
    handles = {
        int(r[0]): str(r[1]) for r in conn.execute("SELECT id, handle FROM players").fetchall()
    }
    years = {int(r[0]): int(r[1]) for r in conn.execute("SELECT id, year FROM seasons").fetchall()}
    ranked = sorted(rows, key=lambda r: -abs(r["adjustment"]))[:AUDIT_ROWS]
    return [
        {
            "player": handles.get(int(r["player_id"]), str(int(r["player_id"]))),
            "year": years.get(int(r["season_id"])),
            "raw": round(r["raw"], 4),
            "adjustment": round(r["adjustment"], 4),
            "adjusted": round(r["adjusted"], 4),
        }
        for r in ranked
    ]


# --------------------------------------------------------- the per-season rows


# One stored row: the identity, the position, and the three K/D numbers that
# ship together.
RoleRow = tuple[int, int, int, float, float, float, float | None, float | None, float | None]


def season_rows(seasons: Sequence[ContactSeason], cost: Cost | None) -> list[RoleRow]:
    """One row per qualified player-season, for `player_role_season`.

    The percentile is within the season, because the contact rate a mode
    demands moves with the title and a cross-era percentile would compare a
    2020 rate against a 2026 one.
    """
    adjusted = {(int(r["player_id"]), int(r["season_id"])): r for r in adjusted_kd(seasons, cost)}
    by_season: dict[int, list[float]] = {}
    for s in seasons:
        by_season.setdefault(s.season_id, []).append(s.contact_rate)
    ranked = {season: sorted(rates) for season, rates in by_season.items()}

    out: list[RoleRow] = []
    for s in seasons:
        peers = ranked[s.season_id]
        below = sum(1 for r in peers if r < s.contact_rate)
        pctl = below / (len(peers) - 1) if len(peers) > 1 else 0.5
        row = adjusted.get((s.player_id, s.season_id))
        out.append(
            (
                s.player_id,
                s.season_id,
                s.maps,
                s.contact_rate,
                s.contact_win_rate,
                pctl,
                None if row is None else row["raw"],
                None if row is None else row["adjustment"],
                None if row is None else row["adjusted"],
            )
        )
    return sorted(out, key=lambda r: (r[1], r[0]))


def write(
    conn: psycopg.Connection[Any],
    run_id: int,
    rows: Sequence[RoleRow],
) -> None:
    """Store the per-season positions of one role run."""
    if not rows:
        return
    conn.cursor().executemany(
        "INSERT INTO player_role_season (run_id, player_id, season_id, maps,"
        " contact_rate, contact_win_rate, contact_pctl, kd_raw, kd_adjustment, kd_adjusted)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(run_id, *r) for r in rows],
    )


# ------------------------------------------------------------- the artifact


def statement(rec: Recovery | None, costs: Sequence[Cost]) -> str:
    """One line for the run log."""
    parts = []
    if rec is not None:
        parts.append(
            f"weapon class recovered {rec.accuracy:.0%} against a {rec.base_rate:.0%} "
            f"base rate ({rec.verdict})"
        )
    kd = next((c for c in costs if c.outcome == "kd"), None)
    if kd is not None:
        parts.append(
            f"one SD more opening contact goes with {kd.slope:+.3f} SD of K/D "
            f"[{kd.lo95:+.3f}, {kd.hi95:+.3f}]"
        )
    return "; ".join(parts) if parts else "nothing measurable"


def build(
    conn: psycopg.Connection[Any], style_run: int
) -> tuple[Recovery | None, list[Cost], list[RoleRow], dict[str, Any]]:
    """Both halves, each on the era that can carry it."""
    observed = observed_classes(conn)
    weapon_seasons = load_weapon_seasons(conn)
    axes = load_style_axes(conn, style_run)
    rec = recovery(weapon_seasons, axes)

    contact = load_contact_seasons(conn)
    costs = [
        c for c in (entry_cost(contact, o) for o in ("kd", "damage_per_map", "untraded_rate")) if c
    ]

    labels = [s.label for s in weapon_seasons]
    payload: dict[str, Any] = {
        "available": bool(weapon_seasons) or bool(contact),
        "style_run_id": style_run,
        "weapon_table": {
            "n_weapons": len(WEAPON_CLASS),
            "verified_against_feed": sorted(VERIFIED),
            "observed": observed,
            "disagreements": table_disagreements(observed),
        },
        "recovery": None
        if rec is None
        else {
            "n_seasons": rec.n_seasons,
            "n_players": rec.n_players,
            "n_axes": rec.n_axes,
            "base_rate": rec.base_rate,
            "accuracy": rec.accuracy,
            "auc_by_axis": {str(k): v for k, v in rec.auc_by_axis.items()},
            "verdict": rec.verdict,
            "rule": {
                "carries_at": RECOVERY_CARRIES,
                "ambiguous_at": RECOVERY_AMBIGUOUS,
            },
            "labels": {klass: labels.count(klass) for klass in RECOVERED_CLASSES},
        },
        "entry_cost": [
            {
                "outcome": c.outcome,
                "slope": c.slope,
                "lo95": c.lo95,
                "hi95": c.hi95,
                "n_seasons": c.n_seasons,
                "n_players": c.n_players,
                "separates": bool(c.lo95 > 0 or c.hi95 < 0),
            }
            for c in costs
        ],
        "adjustment_audit": audit(
            conn, contact, next((c for c in costs if c.outcome == "kd"), None)
        ),
        "n_contact_seasons": len(contact),
        "mode": SND_SLUG,
        # What each era can and cannot answer, so no reader has to infer it from
        # a missing number.
        "era_split": {
            "recovery_era": "2017-2019",
            "cost_era": "2020-2026",
            "why": (
                "first_deaths and non_traded_kills are all zero on every CWL title and "
                "fave_weapon is absent on every CDL title, so the label and the cost "
                "never share an era"
            ),
        },
        "statement": statement(rec, costs),
    }
    kd_cost = next((c for c in costs if c.outcome == "kd"), None)
    return rec, costs, season_rows(contact, kd_cost), payload


def headline(payload: dict[str, Any]) -> str:
    return str(payload.get("statement", "nothing measurable"))
