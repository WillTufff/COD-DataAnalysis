"""Orchestrator: build every career-rank row, scoped to the frozen evaluation
population until an explicit full-archive run. Locked against the
pre-registration; the full-archive task reuses this unchanged.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, cast

import psycopg

from .. import style, writeback
from ..ratings.preflight import load_seasons
from . import (
    PUBLISH_FROM_YEAR,
    anchors,
    awards,
    blend,
    breadth,
    convergent,
    evalpop,
    resume,
    roster_strength,
    value_backbone,
)

Conn = psycopg.Connection[tuple[object, ...]]

MODEL = "career_rank"
VERSION = "1.0.0"

# The one artifact this model publishes; carries the top-tens plus enough of
# each player's row for the metric-diff harness to key on. Following
# `metricdiff.run.REPORT_ARTIFACT`'s naming, not a name of its own invention.
ARTIFACT_NAME = "career_rank"


def _era_season_scores(
    raw: list[breadth.SeasonBreadth],
    shrunk: list[breadth.SeasonBreadth],
    seasons: dict[int, Any],
) -> list[dict[str, Any]]:
    """Per era: how wide the season score is before and after the shrinkage.

    The reason this is published rather than measured once and written down.
    A pre-2017 season carries about half as many percentiles over about a
    third as many maps as a CDL season, so its score scatters wider for a
    reason that is not the player. Admitting those seasons and reading the era
    gate without this table would clear the gate on that scatter. The gate's
    reading and this table are the two halves of one claim.
    """
    after = {(row.player_id, row.season_id): row for row in shrunk}
    grouped: dict[str, list[tuple[breadth.SeasonBreadth, breadth.SeasonBreadth]]] = {}
    for row in raw:
        season = seasons.get(row.season_id)
        if season is None:
            continue
        grouped.setdefault(season.era_key, []).append((row, after[(row.player_id, row.season_id)]))

    out: list[dict[str, Any]] = []
    for era, pairs in sorted(grouped.items()):
        out.append(
            {
                "era": era,
                "seasons": len(pairs),
                "median_metrics": _median([float(before.n_stats) for before, _ in pairs]),
                "median_maps": _median([float(before.maps) for before, _ in pairs]),
                "sd_before": _sd([before.score for before, _ in pairs]),
                "sd_after": _sd([later.score for _, later in pairs]),
                "median_shrink_weight": _median(
                    [
                        before.maps / (before.maps + breadth.SHRINK_K) if before.maps else 0.0
                        for before, _ in pairs
                    ]
                ),
            }
        )
    return out


def _family_coverage(
    rows: list[breadth.SeasonBreadth], seasons: dict[int, Any]
) -> list[dict[str, Any]]:
    """Per era: how many of the six families a season was actually built from.

    Published because the source plan's claim is wrong and the measurement says
    so. Family weighting does not make a 14-metric 2013 season comparable to a
    26-metric league one: coverage is era-partitioned, not merely thin, and no
    era carries all six. Renormalizing over the live families still compares a
    score built from three families against one built from five. What the
    weighting is worth is the within-era repair — a season stops being scored
    on how many metrics happen to point at its slaying — and this table is what
    keeps the larger claim from being read into it.
    """
    grouped: dict[str, list[breadth.SeasonBreadth]] = {}
    for row in rows:
        season = seasons.get(row.season_id)
        if season is None:
            continue
        grouped.setdefault(season.era_key, []).append(row)

    out: list[dict[str, Any]] = []
    for era, cohort in sorted(grouped.items()):
        entry: dict[str, Any] = {
            "era": era,
            "seasons": len(cohort),
            "median_families": _median([float(len(row.families)) for row in cohort]),
        }
        for family in breadth.FAMILIES:
            present = sum(1 for row in cohort if family in row.families)
            entry[family] = round(100.0 * present / len(cohort), 1)
        out.append(entry)
    return out


def _era_gap(
    season_scores: dict[tuple[int, int], float], seasons: dict[int, Any]
) -> dict[str, Any]:
    """The within-player CWL-minus-CDL gap, the figure Phase C is gated on.

    Read within player and never pooled. Pooled, the marginals hide it
    completely: the CWL cohorts were open-bracket fields and a percentile
    earned against them is not the same percentile earned inside a closed
    twelve-team league, so the same player scores higher in a CWL season for a
    reason that is not the player.
    """
    by_player: dict[int, dict[str, list[float]]] = {}
    for (player_id, season_id), score in season_scores.items():
        season = seasons.get(season_id)
        if season is None:
            continue
        by_player.setdefault(player_id, {}).setdefault(season.era_key, []).append(score)

    gaps: list[float] = []
    for eras in by_player.values():
        if "CWL" in eras and "CDL" in eras:
            gaps.append(sum(eras["CWL"]) / len(eras["CWL"]) - sum(eras["CDL"]) / len(eras["CDL"]))
    if not gaps:
        return {"n_players": 0}
    return {
        "n_players": len(gaps),
        "mean": round(sum(gaps) / len(gaps), 4),
        "median": _median(gaps),
        "sd": _sd(gaps),
        "share_higher_in_cwl": round(sum(1 for g in gaps if g > 0) / len(gaps), 4),
    }


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 4)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 4)


def _sd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return round(math.sqrt(variance), 4)


def _stack_distribution(award_rows: list[awards.AwardRow]) -> dict[str, int]:
    """How far the tiers stack, over the whole archive. Published because the
    stack is a declared rule rather than an artefact: three recognitions in one
    season is more than one, and the reader is entitled to see how rare that is
    before deciding whether the rule paid for itself.
    """
    counted: dict[str, int] = {}
    for credit in awards.credits(award_rows):
        key = f"{credit.points:g}"
        counted[key] = counted.get(key, 0) + 1
    return dict(sorted(counted.items(), key=lambda pair: float(pair[0])))


def params() -> dict[str, Any]:
    return {
        "publish_from_year": PUBLISH_FROM_YEAR,
        "shrink_k": breadth.SHRINK_K,
        "basket_size": len(breadth.gold_basket()),
        "min_seasons_floor": evalpop.MIN_SEASONS,
        **awards.params(),
        **resume.params(),
    }


@dataclass(frozen=True)
class PlayerRow:
    player_id: int
    career: blend.CareerRank
    seasons: dict[int, float]  # season_id -> season score, breadth and VALUE blended
    season_sd: dict[int, float]  # season_id -> the blend's carried width
    # The two halves of the season score, published beside it. `season_value`
    # is absent where the season was scored on breadth alone.
    season_breadth: dict[int, float]
    season_value: dict[int, float]
    # The metric families that season was built from. No era carries all six,
    # so this is what stops the reader assuming a 2014 score and a 2023 one
    # were built the same way.
    season_families: dict[int, tuple[str, ...]]
    net_of_teammates: dict[int, float]
    opponent_strength: dict[int, float]
    resume: dict[int, float]  # season_id -> finish credit, share of the year
    resume_credit: dict[int, float]  # the same, before the per-year division
    accolade: dict[int, float]  # season_id -> award credit, share of the year
    accolade_credit: dict[int, float]  # the same, before the per-year division
    season_awards: dict[int, tuple[str, ...]]  # season_id -> the awards that season held
    # season_id -> the components that season carried. Its keys are every
    # season the career touches, which `seasons` alone is not: a season with a
    # finish and no box score appears here and not there.
    components: dict[int, tuple[str, ...]]
    chips: int  # title wins over the whole career, not only published seasons
    rings: int


def build(
    conn: Conn, restrict_to: set[int] | None = None
) -> tuple[list[PlayerRow], dict[str, Any]]:
    seasons = load_seasons(conn)

    basket = breadth.gold_basket()
    points = breadth.load_metric_points(conn, basket)
    slice_maps = breadth.load_slice_maps(conn)
    raw_breadth = breadth.build(points, slice_maps)
    # Shrunk before anything reads a score: the cohort a season is pulled
    # toward is its own season's whole field, so restricting the run to the
    # frozen evaluation population must not change what a season is worth.
    season_breadth = breadth.shrink(raw_breadth)
    shrink_fit = breadth.estimate_shrink_k(raw_breadth)

    # The VALUE backbone, blended onto the shrunk breadth at the declared
    # weight. This is the whole of the season score: award credit used to be
    # added on top of it, and is now ACCOLADE, its own component with its own
    # weight, fixed at the career blend rather than hidden inside a
    # performance number.
    season_value = roster_strength.load_season_value(conn)
    blended = value_backbone.blend(season_breadth, season_value)
    blended_by_key = {(row.player_id, row.season_id): row for row in blended}
    families_by_key = {(row.player_id, row.season_id): row.families for row in raw_breadth}

    season_score_by_key: dict[tuple[int, int], float] = {}
    season_sd_by_key: dict[tuple[int, int], float] = {}
    breadth_by_key: dict[tuple[int, int], float] = {}
    value_by_key: dict[tuple[int, int], float] = {}
    families_by_published_key: dict[tuple[int, int], tuple[str, ...]] = {}
    withheld = 0
    for sb in season_breadth:
        key = (sb.player_id, sb.season_id)
        if restrict_to is not None and sb.player_id not in restrict_to:
            continue
        if seasons[sb.season_id].year < PUBLISH_FROM_YEAR:
            withheld += 1
            continue
        row = blended_by_key[key]
        season_score_by_key[key] = row.score
        breadth_by_key[key] = row.breadth
        if row.value_scaled is not None:
            value_by_key[key] = row.value_scaled
        families_by_published_key[key] = families_by_key.get(key, ())
        if row.sd is not None:
            season_sd_by_key[key] = row.sd

    team_season_value = roster_strength.build_team_season_value(conn, season_value)
    net_rows = roster_strength.net_of_teammates(conn, season_value)
    opp_rows = roster_strength.opponent_strength(conn, team_season_value)

    net_by_player: dict[int, dict[int, float]] = {}
    for net_row in net_rows:
        net_by_player.setdefault(net_row.player_id, {})[net_row.season_id] = net_row.net
    opp_by_player: dict[int, dict[int, float]] = {}
    for opp_row in opp_rows:
        opp_by_player.setdefault(opp_row.player_id, {})[opp_row.season_id] = (
            opp_row.mean_opponent_value
        )

    # Finish credit is built over the whole archive and then attached to the
    # seasons this run publishes: the credit a 2015 win earned is a fact about
    # 2015, and withholding the season score does not change it.
    #
    # It is scoped by population and by the year floor, and by nothing else.
    # Requiring a qualifying breadth row as well used to delete the credit on
    # any season the box-score archive did not reach, which is the same
    # missing-is-not-zero rule the blend keeps, broken here. Measured on
    # 2026-08-22 that cost 111 season credits across 84 players, every one of
    # them a player the board already ranks.
    resume_by_player: dict[int, dict[int, float]] = {}
    resume_credit_by_player: dict[int, dict[int, float]] = {}
    resume_withheld = 0
    for entry in resume.build(conn):
        if restrict_to is not None and entry.player_id not in restrict_to:
            continue
        if seasons[entry.season_id].year < PUBLISH_FROM_YEAR:
            resume_withheld += 1
            continue
        resume_by_player.setdefault(entry.player_id, {})[entry.season_id] = entry.resume
        resume_credit_by_player.setdefault(entry.player_id, {})[entry.season_id] = entry.credit
    rings_covered_from = resume.coverage_from(conn)

    # Award credit is normalised against its own year over the whole archive
    # and then scoped to this run, on the same rule the finish credit keeps: a
    # 2016 first team is worth a share of 2016 whoever else the run happens to
    # publish.
    accolade_by_player: dict[int, dict[int, float]] = {}
    accolade_credit_by_player: dict[int, dict[int, float]] = {}
    awards_by_player: dict[int, dict[int, tuple[str, ...]]] = {}
    award_rows = awards.load_award_rows(conn)
    unresolved_awards = awards.load_unresolved(conn)
    accolade_withheld = 0
    for honour in awards.score(award_rows):
        if restrict_to is not None and honour.player_id not in restrict_to:
            continue
        if seasons[honour.season_id].year < PUBLISH_FROM_YEAR:
            accolade_withheld += 1
            continue
        accolade_by_player.setdefault(honour.player_id, {})[honour.season_id] = honour.accolade
        accolade_credit_by_player.setdefault(honour.player_id, {})[honour.season_id] = honour.credit
        awards_by_player.setdefault(honour.player_id, {})[honour.season_id] = honour.awards

    seasons_by_player: dict[int, dict[int, float]] = {}
    for (player_id, season_id), score in season_score_by_key.items():
        seasons_by_player.setdefault(player_id, {})[season_id] = score
    season_sd_by_player: dict[int, dict[int, float]] = {}
    for (player_id, season_id), sd in season_sd_by_key.items():
        season_sd_by_player.setdefault(player_id, {})[season_id] = sd
    breadth_by_player: dict[int, dict[int, float]] = {}
    for (player_id, season_id), value in breadth_by_key.items():
        breadth_by_player.setdefault(player_id, {})[season_id] = value
    value_by_player: dict[int, dict[int, float]] = {}
    for (player_id, season_id), value in value_by_key.items():
        value_by_player.setdefault(player_id, {})[season_id] = value
    families_by_player: dict[int, dict[int, tuple[str, ...]]] = {}
    for (player_id, season_id), live in families_by_published_key.items():
        families_by_player.setdefault(player_id, {})[season_id] = live

    # One entry per season the career touches at all, carrying whichever
    # components that season has. A season with a finish and no box score
    # reaches the blend as a RESUME-only entry; the blend renormalizes over
    # what is there and never scores the absent half as zero.
    scored: list[blend.SeasonScore] = []
    for player_id in sorted(
        set(seasons_by_player) | set(resume_by_player) | set(accolade_by_player)
    ):
        performance = seasons_by_player.get(player_id, {})
        finishes = resume_by_player.get(player_id, {})
        honours = accolade_by_player.get(player_id, {})
        for season_id in sorted(set(performance) | set(finishes) | set(honours)):
            components: dict[str, float] = {}
            if season_id in performance:
                components[blend.PERFORMANCE] = performance[season_id]
            if season_id in finishes:
                components[blend.RESUME] = finishes[season_id]
            if season_id in honours:
                components[blend.ACCOLADE] = honours[season_id]
            scored.append(
                blend.SeasonScore(
                    player_id=player_id,
                    season_id=season_id,
                    components=components,
                    sd=season_sd_by_player.get(player_id, {}).get(season_id),
                )
            )

    career_rows = blend.build(scored, seasons)
    unrankable = len({s.player_id for s in scored}) - len(career_rows)
    career_titles = anchors.resume(conn, [row.player_id for row in career_rows])

    out: list[PlayerRow] = []
    for career_row in career_rows:
        out.append(
            PlayerRow(
                player_id=career_row.player_id,
                career=career_row,
                seasons=seasons_by_player.get(career_row.player_id, {}),
                season_sd=season_sd_by_player.get(career_row.player_id, {}),
                season_breadth=breadth_by_player.get(career_row.player_id, {}),
                season_value=value_by_player.get(career_row.player_id, {}),
                season_families=families_by_player.get(career_row.player_id, {}),
                net_of_teammates=net_by_player.get(career_row.player_id, {}),
                opponent_strength=opp_by_player.get(career_row.player_id, {}),
                resume=resume_by_player.get(career_row.player_id, {}),
                resume_credit=resume_credit_by_player.get(career_row.player_id, {}),
                accolade=accolade_by_player.get(career_row.player_id, {}),
                accolade_credit=accolade_credit_by_player.get(career_row.player_id, {}),
                season_awards=awards_by_player.get(career_row.player_id, {}),
                components={
                    entry.season_id: entry.present
                    for entry in scored
                    if entry.player_id == career_row.player_id
                },
                chips=int(career_titles.get(career_row.player_id, {}).get("chips", 0)),
                rings=int(career_titles.get(career_row.player_id, {}).get("rings", 0)),
            )
        )

    payload = {
        "model": MODEL,
        "version": VERSION,
        "evaluation_population": evalpop.stamp(),
        "team_strength_proxy_check": roster_strength.proxy_check(conn, team_season_value),
        "restricted": restrict_to is not None,
        "n_players_scored": len(out),
        "publish_from_year": PUBLISH_FROM_YEAR,
        "seasons_withheld": withheld,
        "seasons_withheld_rule": (
            "a season before the floor is scored and not published: the field it "
            "would be standardized inside is not yet comparable to a league one"
        ),
        "basket_size": len(basket),
        # The six families, authored metric by metric and fixed in the
        # pre-registration before this ran. The catalog's own `category` is
        # twelve mode-shaped labels and is not this.
        "families": {
            "rule": breadth.FAMILY_RULE,
            "names": list(breadth.FAMILIES),
            "sizes": {
                family: sum(1 for f in breadth.FAMILY.values() if f == family)
                for family in breadth.FAMILIES
            },
            "era_coverage": _family_coverage(raw_breadth, seasons),
        },
        "value_backbone": value_backbone.coverage(blended),
        # The map-count shrinkage, and what a refit on this run's rows would
        # have put the constant at. The constant is frozen; this is the drift
        # made visible.
        "shrinkage": {
            "k": breadth.SHRINK_K,
            "rule": breadth.SHRINK_RULE,
            "min_cohort": breadth.MIN_SHRINK_COHORT,
            "refit": shrink_fit,
        },
        "era_season_scores": _era_season_scores(raw_breadth, season_breadth, seasons),
        # The gate's own number. Re-pinned at +8.7335 on the board Phase D
        # opened against; the +13.4 the anatomy document recorded predates
        # Phase A and Phase B and is kept here as the historical figure.
        "era_gap": {
            **_era_gap(season_score_by_key, seasons),
            "previous_published": 8.7335,
            "anatomy_figure": 13.4,
        },
        # An outside referent nothing in this engine was fitted to. It reaches
        # one era and the payload says which.
        "convergent": convergent.check(
            season_score_by_key, convergent.load_third_party(conn), seasons
        ),
        # The finish component. It does not enter `career` — the fixed-weight
        # blend is R7 — so this is the whole of what the run says about it.
        "resume": {
            **resume.params(),
            "rings_covered_from": rings_covered_from,
            "n_player_seasons": sum(len(row.resume) for row in out),
            "seasons_withheld_below_floor": resume_withheld,
            # Season credits on a season with no qualifying breadth row. They
            # used to be dropped here; they are kept and marked instead.
            "n_without_performance": sum(
                1 for row in out for season_id in row.resume if season_id not in row.seasons
            ),
        },
        # The accolade component. Like the finish credit it does not enter
        # `career` — the fixed-weight blend is R7 — so this is the whole of
        # what the run says about it.
        "accolade": {
            **awards.params(),
            "n_player_seasons": sum(len(row.accolade) for row in out),
            "seasons_withheld_below_floor": accolade_withheld,
            "thin_years": sorted(awards.thin_years(award_rows)),
            # Award rows the record cannot attach to a player. They are never a
            # loss to anybody and never reduce a season.
            "unresolved_rows": len(unresolved_awards),
            # Seasons carrying an award and no breadth row. They used to be
            # scored as a bump on a score that did not exist.
            "n_without_performance": sum(
                1 for row in out for season_id in row.accolade if season_id not in row.seasons
            ),
            "stack_distribution": _stack_distribution(award_rows),
            "density": awards.density(award_rows, unresolved_awards),
        },
        "career": blend.artifact([r.career for r in out], n_unrankable=unrankable),
        # Every scored player, not just the top ten: the metric-diff harness
        # keys a list by `player_id` (see `LIST_KEYS` in
        # `metricdiff/snapshot.py`), so this is what makes every player's
        # total/peak/best_three individually diffable across runs, not only
        # the leaderboard's head.
        "players": [
            {
                "player_id": row.player_id,
                "qualified": row.career.qualified,
                "n_seasons": row.career.n_seasons,
                "total": round(row.career.total, 2),
                "total_sd": None if row.career.total_sd is None else round(row.career.total_sd, 2),
                "peak": round(row.career.peak, 2),
                "peak_season_id": row.career.peak_season_id,
                "best_three": None
                if row.career.best_three is None
                else round(row.career.best_three, 2),
                "chips": row.chips,
                "rings": row.rings,
                "resume_total": round(sum(row.resume.values()), 4),
                "seasons_covered": row.career.seasons_covered,
                "coverage_from_year": row.career.coverage_from_year,
                "components_present": list(row.career.components_present),
            }
            for row in out
        ],
    }
    return out, payload


def run_against_frozen(conn: Conn) -> tuple[list[PlayerRow], dict[str, Any]]:
    pointer = evalpop.frozen()
    if pointer is None:
        raise RuntimeError("no frozen evaluation population — run evalpop.freeze first")
    player_ids = set(evalpop.read_set(cast(str, pointer["cut"])))
    return build(conn, restrict_to=player_ids)


def run_full_archive(conn: Conn) -> tuple[list[PlayerRow], dict[str, Any]]:
    return build(conn, restrict_to=None)


def write(
    conn: Conn, restrict_to: set[int] | None = None
) -> tuple[list[PlayerRow], dict[str, Any], int]:
    """`build`, then publish it: a real `model_runs` row plus the artifact,
    so the metric-diff harness (`metricdiff/snapshot.py`, which snapshots
    every `model_runs` row's `model_artifacts`) picks this model up the same
    way it already picks up `career.py`'s. `build` itself stays pure — no
    connection writes — so tests can call it without touching `model_runs`.
    """
    out, payload = build(conn, restrict_to=restrict_to)
    run_id = writeback.open_run(conn, MODEL, VERSION, params(), style.data_through(conn))
    conn.execute(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        (run_id, ARTIFACT_NAME, json.dumps(payload, allow_nan=False)),
    )
    payload["run_id"] = run_id
    return out, payload, run_id
