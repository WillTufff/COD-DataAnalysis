"""Conditions a published run is not allowed to satisfy quietly.

    uv run python -m cdlhub_analytics.gates [--dsn DSN]

Every check here started as a counter in an artifact. A model met a degraded
condition, wrote the count, and carried on; the run reported success, the test
suite stayed green, and the site published the result. Three of those shipped at
once — a title whose series rollup never ran, a cohort that rated every player
1.00, and an axis wearing the name of the axis behind it — and none of them
could be found by reading the code, only by reading the numbers afterwards.

So each is stated here as a condition on the newest run of its model, checked
against the database rather than against a fixture, and failed loudly. The rule
these follow: a model may decline to publish something, but the decision has to
be visible from outside the model.

Exit codes are three-way on purpose. 0 is every gate met, 1 is a gate failed,
and 3 is "these gates did not run" — no database, or a model that has never been
fitted here. `checks.sh` reports 3 as a skip rather than a pass, because a gate
that cannot run is not a gate that passed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import psycopg

from . import aging, career, errorcontrol, metrics, role, seriesdyn, style, validation
from .career_rank import engine as career_rank
from .career_rank import evalpop as career_evalpop
from .db import connect
from .era import MIN_MAPS
from .metricdiff import MODEL as METRIC_DIFF_MODEL
from .metricdiff.run import POPULATION_ARTIFACT, REPORT_ARTIFACT
from .ratings import evalspec, maplevel, player_rating, prior, statespace
from .style import marker_column
from .writeback import latest_run_id

# The rating fits several feature-set versions under one model name, so its runs
# are found by version rather than by recency. The other models name themselves.
RATING_MODEL = "player_rating"
STYLE_MODEL = style.MODEL
DYNAMICS_MODEL = seriesdyn.MODEL

# Models written by the pipeline package rather than through
# `writeback.open_run`, so they carry no provenance block and are not asked for
# an evaluation-set hash.
EXTERNAL_MODELS = {"kill_feed_reconciliation", "codwiki_overlap_reconciliation"}

# How far the recomputed SKILL floor may sit from the value pinned before the
# prior existed. The floor is published to four decimals, so this is a rounding
# tolerance rather than room for the threshold to drift.
FLOOR_TOL = 5e-4

# A line the release prints and does not fail on. Used where a published number
# legitimately moved, the move has been written down with its date and reason,
# and the run still has to show the distance rather than close it.
REPORTED = "reported: "

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CANNOT_RUN = 3

# The published bases, pinned: how many components each retains and the column
# each of those loads hardest on, under the same normalization that assigns the
# names. A basis moves when the metric layer gains or loses a column, which has
# happened once already — the CWL core grew from 21 columns to 26, began
# retaining a fifth component, and slid three published names one seat down
# without anything failing. Drift here is not necessarily wrong, but it is
# always a decision: re-read the loadings, rename what needs renaming, and
# update this table in the same commit.
PUBLISHED_BASES: dict[str, tuple[tuple[str, str], ...]] = {
    # Re-read on 2026-08-16, after the 2013-2016 load. `core CWL` and both CDL
    # bases came back identical. `extended CWL` grew from ten components to
    # twelve and reordered below its third: it is the robustness arm, not the
    # published one, and its first three axes are unchanged. The wiki-era
    # bases are new and thin, which is what six columns over four seasons supports.
    "core 2013-2016": (
        ("volume", "kills"),
        ("survival", "deaths"),
    ),
    # Re-read on 2026-08-18, after 2014 entered the basis for the first time.
    # `build_basis` keeps only rows with a rating, and 2014 had none until the
    # Search and Destroy, Domination and Blitz cohorts were recovered, so a
    # whole season joined the population this is fitted on. The two named axes
    # held. Axis 3's marker moved from kills to deaths on a real margin
    # (+0.464 against -0.410) and axis 4 held. Both are unnamed, so nothing
    # published is renamed.
    "extended 2013-2016": (
        ("volume", "kills"),
        ("survival", "deaths"),
        ("axis 3", "deaths"),
        ("axis 4", "deaths"),
    ),
    "core CWL": (
        ("volume", "kills"),
        ("survival", "deaths"),
        ("axis 3", "assists"),
        ("streak depth", "deep_streak_rate"),
        ("risk", "eight_plus_streaks"),
    ),
    "extended CWL": (
        ("volume", "kills"),
        ("survival", "deaths"),
        ("axis 3", "assists"),
        ("axis 4", "kd"),
        ("risk", "eight_plus_streaks"),
        ("streak depth", "streak6"),
        ("axis 7", "hill_time"),
        ("axis 8", "streak4"),
        ("axis 9", "deaths"),
        ("axis 10", "streak6"),
        ("axis 11", "suicides"),
        ("axis 12", "streak7"),
    ),
    "core CDL": (
        ("volume", "kills"),
        ("survival", "deaths"),
    ),
    # Axis 3 re-pinned 2026-08-18 from plus_minus to kd, and the two are a tie
    # rather than a move: kd loads +0.337666 and plus-minus +0.337585, a gap of
    # 0.00008. Per-map plus-minus and K/D are the same quantity differenced two
    # ways, so which of them the marker rule picks is decided in the fifth
    # decimal and will keep changing. The axis itself did not move.
    "extended CDL": (
        ("volume", "kills"),
        ("survival", "deaths"),
        ("axis 3", "kd"),
        ("axis 4", "damage"),
        ("axis 5", "damage"),
    ),
}


class CannotRun(Exception):
    """The gate has nothing to read, which is neither a pass nor a failure."""


def artifact(conn: psycopg.Connection[Any], model: str, name: str) -> dict[str, Any]:
    run_id = latest_run_id(conn, model)
    if run_id is None:
        raise CannotRun(f"no {model} run in this database")
    row = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = %s",
        (run_id, name),
    ).fetchone()
    if row is None:
        raise CannotRun(f"{model} run {run_id} has no {name} artifact")
    payload = row[0]
    return cast(dict[str, Any], payload if isinstance(payload, dict) else json.loads(payload))


def rating_artifact(conn: psycopg.Connection[Any], name: str) -> dict[str, Any]:
    """The published feature-set version's artifact, not whichever ran last.

    Every run fits several versions under one model name, so the newest run id
    belongs to the last version fitted rather than the one the site reads.
    """
    row = conn.execute(
        "SELECT id FROM model_runs WHERE model = %s AND version = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (RATING_MODEL, player_rating.PUBLISHED_VERSION),
    ).fetchone()
    if row is None:
        raise CannotRun(f"no {RATING_MODEL} {player_rating.PUBLISHED_VERSION} run here")
    payload = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = %s",
        (row[0], name),
    ).fetchone()
    if payload is None:
        raise CannotRun(f"{RATING_MODEL} run {row[0]} has no {name} artifact")
    got = payload[0]
    return cast(dict[str, Any], got if isinstance(got, dict) else json.loads(got))


def rotation_failures(rollup: dict[str, Any], dynamics: dict[str, Any]) -> list[str]:
    """A title with no declared rotation is rated map by map and then dropped
    from every series rollup — silently, at the scale of an entire era. The
    counter read 1,633 series for two years before anybody read it.

    A title *measured* to have no rotation is a different thing and is counted
    separately: Advanced Warfare's third map splits Uplink against Capture the
    Flag, so there was no order to declare. Failing on that would push the next
    reader to invent one, which is the opposite of what this gate is for.
    """
    bad = []
    n = int(rollup["n_series_no_rotation"])
    if n:
        bad.append(f"map_elo: {n:,} series whose title declares no rotation (maplevel.THIRD_MAP)")
    n = int(dynamics["n_no_rotation"])
    if n:
        bad.append(f"series_dynamics: {n:,} series whose title declares no rotation")
    return bad


def cohort_failures(posterior: dict[str, Any]) -> list[str]:
    """τ² at zero means the cohort's players are indistinguishable, and the
    ratings that follow are 1.00 for everyone. The model now withholds those
    rather than publishing them, but a collapse is still a fit that failed and
    the release should stop on it rather than quietly lose a season."""
    bad = []
    for c in posterior["cohorts"]:
        if c["collapsed"]:
            bad.append(
                f"collapsed cohort: {c['year']} {c['title']} {c['mode']} (tau={c['tau']:.4f})"
            )
        if c["stalled"]:
            bad.append(f"EM ran out of iterations: {c['year']} {c['title']} {c['mode']}")
    return bad


def basis_failures(payload: dict[str, Any]) -> list[str]:
    """Axis names are assigned by what a component loads on, so a basis that
    moves renames axes on every player page that reads them. That is allowed to
    happen and not allowed to happen unnoticed."""
    bad = []
    fitted = {b["basis"]: b for b in payload["bases"]}
    for name in sorted(set(fitted) | set(PUBLISHED_BASES)):
        pinned = PUBLISHED_BASES.get(name)
        basis = fitted.get(name)
        if pinned is None:
            bad.append(f"basis '{name}' is published and not pinned in gates.PUBLISHED_BASES")
            continue
        if basis is None:
            bad.append(f"basis '{name}' is pinned and no longer published")
            continue
        axes = basis["axes"]
        if len(axes) != len(pinned):
            bad.append(f"basis '{name}': retains {len(axes)} components, pinned at {len(pinned)}")
            continue
        for i, (axis, (axis_name, column)) in enumerate(zip(axes, pinned, strict=True), start=1):
            top = marker_column(axis["loadings"][0]["column"])
            if top != column:
                bad.append(f"basis '{name}' axis {i}: loads hardest on {top}, pinned to {column}")
            elif axis["name"] != axis_name:
                bad.append(f"basis '{name}' axis {i}: named '{axis['name']}', pinned '{axis_name}'")
    return bad


def season_rapm_failures(
    payload: dict[str, Any], stored: list[tuple[str, str, int, float]]
) -> list[str]:
    """The season-varying plus-minus, checked against what permitted it.

    Three ways this can ship wrong and none of them raise on their own. The
    resolution can drift from the pre-flight verdict that allowed it, so a CWL
    season coefficient appears where the identification measurement said only an
    era is supportable. The filtered family can go missing, which leaves every
    forward test with nothing it is allowed to read while the smoothed rows sit
    there looking usable. And an era coefficient — stored against each season it
    covers — can stop being one number wearing several labels, at which point a
    reader counting rows is counting estimates that were never made.
    """
    bad = []
    if not payload.get("available"):
        return [f"season plus-minus did not fit: {payload.get('reason')}"]

    fitted = payload["resolution_by_league"]
    for cell in payload["by_cell"]:
        # An era cell names itself "<league> era"; a season cell "<year> <league>".
        league = cell["cell"].split()[0] if cell["resolution"] == "era" else None
        if league is not None and fitted.get(league) != "era":
            bad.append(f"cell '{cell['cell']}' is era-resolution and {league} is not pooled")

    scopes = {scope for scope, _resolution, _season, _coef in stored}
    if stored and "filtered" not in scopes:
        bad.append("no filtered coefficients stored: a forward test has nothing it may read")

    # One estimate, several season labels. Grouped by (scope, player) over the
    # era rows: more than one distinct coefficient means they were fitted apart.
    era_coefs: dict[tuple[str, int], set[float]] = defaultdict(set)
    for scope, resolution, player_id, coef in stored:
        if resolution == "era":
            era_coefs[(scope, player_id)].add(round(coef, 9))
    split = [key for key, values in era_coefs.items() if len(values) > 1]
    if split:
        bad.append(
            f"{len(split)} era coefficients differ across the seasons they are filed under, "
            "so the rows are being read as separate estimates"
        )
    return bad


def skill_prior_failures(
    power: dict[str, Any],
    fitted: dict[str, Any],
    pinned: dict[str, Any],
    remeasured: dict[str, Any] | None = None,
) -> list[str]:
    """The threshold a SKILL rating is judged against, and whether it has moved.

    A minimum detectable effect is only a threshold if it predates the number it
    judges. Nothing stops a floor from being computed in the same run that first
    reports a result, or from being recomputed once that result is known — and
    from the outside the two are indistinguishable, which is exactly the shape
    of failure the harness exists to prevent one phase earlier.

    **The first version of this checked run ids and could not have worked.** It
    asserted that the run which first carried a `skill_power` artifact preceded
    the run which first carried a `skill_prior` one. Both artifacts are rewritten
    by every pipeline run and `prune_superseded` deletes the runs before it, so
    after one run of the finished pipeline the only surviving pair is this run's
    — and within a run `skill_prior` is written by an earlier stage than
    `skill_power`, so the check would have failed permanently on stage order. It
    was written while the prior did not exist, which is why nothing failed: the
    condition is vacuous until there is a prior to judge.

    What is durable is the pin. The floor was computed at P5a and written into
    `evalspec.PUBLISHED_FIGURES`, in a commit that predates the prior's, and this
    gate checks the floor the harness recomputes still equals it. A floor that
    moves once a result is visible is the failure being guarded against, and
    moving it now requires editing a pinned constant in a diff someone reads.

    Three conditions on the prior itself sit beside it. The shrinkage diagnostic
    has to pass its declared threshold — a prior that loads on maps played harder
    than the target it predicts is a shrinkage map with a rating's name on it —
    and every arm of the declared ladder has to carry a verdict, either measured
    in this run or recorded from the run that measured it. An arm that quietly
    stops being mentioned is how a ladder becomes one fit with a story attached.
    """
    bad = []
    if fitted:
        loading = fitted.get("exposure_loading", {})
        if not loading.get("available"):
            bad.append(f"the prior published no shrinkage diagnostic: {loading.get('reason')}")
        elif not loading.get("passes"):
            bad.append(
                f"the prior loads {loading.get('prior_r2')} on exposure against the target's "
                f"own {loading.get('target_r2')} (ratio {loading.get('ratio')}, max "
                f"{loading.get('ratio_max')}): it is reporting shrinkage as skill"
            )
        arms = fitted.get("ladder", {}).get("arms", {})
        recorded = {entry.get("arm") for entry in fitted.get("ladder_history", [])}
        for name in ("random_forest", "lightgbm"):
            block = arms.get(name, {})
            if block.get("available"):
                if block.get("vs_ridge") is None:
                    bad.append(f"the {name} arm was fitted and never compared against the ridge")
            elif name not in recorded:
                bad.append(
                    f"the {name} arm published no verdict: it was neither fitted here nor "
                    "recorded as compared and dropped"
                )
    if not power.get("available"):
        # Not a failure while there is no prior to judge: the panel can be too
        # thin to score, and saying so is the artifact doing its job.
        if fitted:
            bad.append(
                f"a SKILL prior is published but its floor was never computed: "
                f"{power.get('reason')}"
            )
        return bad

    anchor = power.get("floors", {}).get("composite_measured", {}).get("mde80_clustered")
    if anchor is None:
        bad.append("the SKILL panel published no detectable-effect floor at the measured agreement")
        return bad
    if pinned.get("mde80_clustered") is None:
        return bad
    gap = abs(float(anchor) - float(pinned["mde80_clustered"]))
    if gap <= float(FLOOR_TOL):
        return bad
    # The floor moved. Where the move has been re-measured and written down with
    # its date and its reason, the release reports the distance and carries on;
    # the pinned value is still the threshold and is still what the gate tests
    # against. Where it has not, the move is silent and the gate fails.
    if remeasured is None:
        bad.append(
            f"the SKILL floor moved from the {pinned['mde80_clustered']} pinned before the "
            f"prior existed to {anchor}: a threshold recomputed once the result is visible is "
            "not a threshold declared in advance"
        )
        return bad
    if abs(float(anchor) - float(remeasured["mde80_clustered"])) > float(FLOOR_TOL):
        bad.append(
            f"the SKILL floor reads {anchor}, and neither the pinned "
            f"{pinned['mde80_clustered']} nor the re-measurement "
            f"{remeasured['mde80_clustered']} of {remeasured['on']} accounts for it"
        )
        return bad
    bad.append(
        f"{REPORTED}the SKILL floor is pinned at {pinned['mde80_clustered']} and the run "
        f"computes {anchor}, a gap of {gap:.4f}. Re-measured {remeasured['on']}: "
        f"{remeasured['why']}. The pinned value remains the threshold."
    )
    return bad


def aging_failures(payload: dict[str, Any]) -> list[str]:
    """A peak age must be an interval across all three fits, never one fit's point.

    The whole argument of the aging phase is that a single curve is biased by
    survivorship and that the three fits disagree by more than any one of their
    standard errors. A payload that published one fit's peak, or that published
    an interval narrower than the fits it claims to span, would have thrown that
    argument away while still passing every unit test — the fits would each be
    correct and the claim on top of them would not be.

    So three conditions. Every population that locates a peak must locate it in
    more than one fit; the published interval must contain every fit's own
    interval; and the naive fit must be present wherever any fit is, because it
    is the biased one and the size of the bias is only visible against it.
    """
    if not payload.get("available"):
        return []
    out: list[str] = []
    for key, block in sorted(payload.get("populations", {}).items()):
        fits = block.get("fits", {})
        interval = block.get("peak_interval", {})
        located = [name for name, fit in fits.items() if fit.get("peak") is not None]
        if not located:
            continue
        if len(located) < 2:
            out.append(
                f"{key}: a peak age is published from one fit ({located[0]}) alone; "
                "the interval has to span the fits that disagree"
            )
        if "naive" not in fits:
            out.append(
                f"{key}: the naive fit is missing, so the bias has nothing to be read against"
            )
        lo, hi = interval.get("lo"), interval.get("hi")
        if lo is None or hi is None:
            out.append(f"{key}: {len(located)} fits located a peak and no interval was published")
            continue
        for name, fit in sorted(fits.items()):
            fit_lo, fit_hi = fit.get("peak_lo"), fit.get("peak_hi")
            if fit_lo is not None and fit_lo < lo - 1e-9:
                out.append(f"{key}: the published interval starts above {name}'s own lower bound")
            if fit_hi is not None and fit_hi > hi + 1e-9:
                out.append(f"{key}: the published interval ends below {name}'s own upper bound")
    return out


def career_failures(payload: dict[str, Any]) -> list[str]:
    """Both credit rules ship, and a CWL total is never a sum.

    The credit rule was decided in the open and both columns were published
    because their orderings differ. A run that quietly produced one of them —
    which is what an empty `team_season_effect` does, since the shared rule
    drops every player it cannot find a team column for — would publish a
    settled question as though it had only one answer.
    """
    if not payload.get("available"):
        return []
    out: list[str] = []
    counts = payload.get("rows_by_key", {})
    for key in ("plus_minus.deviation.cdl", "plus_minus.deviation_plus_team.cdl"):
        if not counts.get(key):
            out.append(f"{key} published no careers; both credit rules have to ship together")
    rules = payload.get("credit_rules", {})
    if counts.get("plus_minus.deviation.cdl") and rules.get("rank_correlation") is None:
        out.append(
            "the two credit rules were not compared, so the ordering they disagree about "
            "is published without the disagreement"
        )
    return out


def error_control_failures(conn: psycopg.Connection[Any], payload: dict[str, Any]) -> list[str]:
    """Every finding is classified, and only the tests carry a test's numbers.

    The partition is the whole argument of the phase. A kind that drifts into
    being corrected without being declared would publish a q-value for a
    sentence nobody decided was a hypothesis; a kind that drifts out would go
    uncorrected in silence. Both are quiet failures that leave the feed looking
    exactly as it does when the phase works, so the check reads the rows rather
    than trusting the summary written beside them.
    """
    if not payload.get("available"):
        return []
    out: list[str] = []
    for kind in payload.get("unclassified", []):
        out.append(f"{kind} is published and the partition does not name it")

    run_id = payload.get("insights_run_id")
    if run_id is None:
        return [*out, "the payload does not say which insights run it corrected"]

    rows = conn.execute(
        "SELECT kind, finding_class, count(*),"
        " count(*) FILTER (WHERE p_value IS NULL),"
        " count(*) FILTER (WHERE q_bh IS NULL OR q_by IS NULL),"
        " count(*) FILTER (WHERE retracted)"
        " FROM insights WHERE run_id = %s GROUP BY kind, finding_class ORDER BY kind",
        (run_id,),
    ).fetchall()
    if not rows:
        return [*out, f"insights run {run_id} holds no findings to correct"]

    for kind, cls, n, no_p, no_q, retracted in rows:
        declared = errorcontrol.class_of(str(kind))
        if str(cls) != declared:
            out.append(f"{kind} is stored as {cls} and declared {declared}")
        if str(cls) == errorcontrol.TESTABLE:
            if no_p:
                out.append(f"{kind}: {no_p} of {n} testable findings carry no p-value")
            if no_q:
                out.append(f"{kind}: {no_q} of {n} testable findings were never corrected")
        else:
            if n - no_p:
                out.append(f"{kind} is {cls} and {n - no_p} of its findings carry a p-value")
            if retracted:
                out.append(f"{kind} is {cls} and {retracted} of its findings are retracted")

    threshold = payload.get("q_threshold")
    if threshold != errorcontrol.Q_THRESHOLD:
        out.append(
            f"the run corrected at q <= {threshold} and the declared threshold is "
            f"{errorcontrol.Q_THRESHOLD}"
        )
    if not payload.get("sensitivity"):
        out.append("the survivor count is published at one threshold only")
    return out


def role_failures(payload: dict[str, Any]) -> list[str]:
    """The weapon table is checked, the verdict follows its own rule, and no
    adjusted number ships alone.

    Three ways this phase could publish something it did not measure. A weapon
    table that drifts from the kill feed would relabel the population the
    recovery rate is scored against; a verdict written by hand would read a
    recovery rate the pre-registered rule does not; and an adjusted K/D without
    its raw value and the size of the adjustment beside it is an adjustment
    nobody can audit, which the phase was directed not to publish.
    """
    if not payload.get("available"):
        return []
    out: list[str] = []
    table = payload.get("weapon_table", {})
    for line in table.get("disagreements", []):
        out.append(f"weapon table disagrees with the kill feed: {line}")
    if not table.get("observed"):
        out.append("no weapon class was checked against the kill feed")

    rec = payload.get("recovery")
    if rec is not None:
        accuracy = rec.get("accuracy")
        if accuracy is None:
            out.append("a recovery block was published with no recovery rate in it")
        elif rec.get("verdict") != role.verdict_for(float(accuracy)):
            out.append(
                f"the recovery verdict is '{rec.get('verdict')}' and the declared rule "
                f"reads {accuracy:.3f} as '{role.verdict_for(float(accuracy))}'"
            )
        pinned = PUBLISHED_BASES.get("core CWL", ())
        if rec.get("n_axes") != len(pinned):
            out.append(
                f"recovery ran on {rec.get('n_axes')} axes and the CWL basis publishes "
                f"{len(pinned)}"
            )

    for block in payload.get("entry_cost", []):
        missing = [k for k in ("slope", "lo95", "hi95") if block.get(k) is None]
        if missing:
            out.append(f"{block.get('outcome')} ships without {', '.join(missing)}")

    split = payload.get("era_split", {})
    if split.get("recovery_era") == split.get("cost_era"):
        out.append(
            "the recovery and the entry cost claim the same era, and no era carries both "
            "a weapon label and a trade column"
        )
    return out


def validation_failures(payloads: dict[str, dict[str, Any]]) -> list[str]:
    """Four verdicts, each with its population, and no licensed value republished.

    The adversarial phase is the one with the most room to report a null it
    never had the power to find, and the one with a licence attached to half its
    inputs. So: every test says what it ran on, every null says whether the
    design could have resolved it, and the convergent test's published rows are
    checked field by field against the schema the module declares. A field added
    to a disagreement row without being added to `DISAGREEMENT_FIELDS` fails the
    release, which is the point of closing the schema at all.
    """
    out: list[str] = []
    for name, payload in sorted(payloads.items()):
        if not payload.get("verdict"):
            out.append(f"{name} published no verdict")
        population = payload.get("population")
        if not isinstance(population, dict) or not population:
            out.append(f"{name} published no population size")

    convergent = payloads.get("validation_convergent", {})
    if convergent.get("attribution") in (None, ""):
        out.append("the convergent test names no attribution for its source")
    allowed = set(validation.DISAGREEMENT_FIELDS)
    for row in convergent.get("disagreements", []):
        extra = sorted(set(row) - allowed)
        if extra:
            out.append(f"a named disagreement carries undeclared fields: {', '.join(extra)}")
            break

    shock = payloads.get("validation_shock", {})
    for block in (shock, shock.get("against", {})):
        if not block:
            continue
        if block.get("excludes_zero") is False and block.get("informative") is None:
            out.append(
                f"the roster shock null on {block.get('axis')} does not say whether the "
                f"design could have resolved it"
            )
        if block.get("detectable_slope") is None and block.get("slope") is not None:
            out.append(f"the roster shock on {block.get('axis')} reports no detectable effect")

    retro = payloads.get("validation_retrodiction", {})
    leaked = retro.get("one_sided_violations")
    if leaked:
        out.append(
            f"{leaked} filtered cells before the held-out season moved, and a one-sided "
            f"fit through season t cannot depend on t+1"
        )
    return out


def validation_payloads(conn: psycopg.Connection[Any]) -> dict[str, dict[str, Any]]:
    """The newest validation run's artifacts, keyed by name."""
    run_id = latest_run_id(conn, validation.MODEL)
    if run_id is None:
        raise CannotRun(f"no {validation.MODEL} run")
    rows = conn.execute(
        "SELECT name, payload FROM model_artifacts WHERE run_id = %s", (run_id,)
    ).fetchall()
    return {str(r[0]): cast(dict[str, Any], r[1]) for r in rows}


def season_rapm_rows(conn: psycopg.Connection[Any]) -> list[tuple[str, str, int, float]]:
    """(scope, resolution, player_id, coef) for the newest season-varying run."""
    run_id = latest_run_id(conn, statespace.MODEL)
    if run_id is None:
        raise CannotRun(f"no {statespace.MODEL} run in this database")
    rows = conn.execute(
        "SELECT scope, resolution, player_id, coef FROM player_rapm WHERE run_id = %s",
        (run_id,),
    ).fetchall()
    return [(str(r[0]), str(r[1]), int(cast(int, r[2])), float(cast(float, r[3]))) for r in rows]


def evaluation_failures(
    manifest: dict[str, Any],
    repro: dict[str, Any],
    primary: dict[str, Any],
    secondary: dict[str, Any],
    placebos: dict[str, Any],
) -> list[str]:
    """The harness, checked against the declaration it was supposed to be held to.

    Five ways a gate stops being one, none of which raise on their own. The
    manifest can be edited after the fact so the primary test becomes whichever
    one the model passed. The harness can stop recovering the numbers already
    published, at which point it is measuring something else. The threshold can
    go missing, and a comparison with no declared minimum detectable effect can
    be failed by a model that works. A forward test can read a coefficient
    family that has already seen the season it is predicting. And a placebo can
    start reporting structure in shuffled data while every headline number still
    looks fine.
    """
    bad = []
    if manifest.get("sha256") != manifest.get("pinned_sha256"):
        bad.append(
            "the evaluation manifest has changed since it was pinned: a test declared after "
            "the fact is not a test declared in advance"
        )
    if not manifest.get("matches_invariants_pin", True):
        bad.append(
            "the fixed half of the declaration moved — target, baseline, statistic, unit, "
            "seed or threshold rule — which is a different evaluation, not an extended one"
        )
    history = manifest.get("supersedes", [])
    declared = manifest.get("primary", {}).get("predictors", [])
    if history:
        previous = history[-1]
        dropped = [p for p in previous.get("predictors", []) if p not in declared]
        if dropped:
            bad.append(
                f"version {previous.get('version')} declared predictors this one does not "
                f"({', '.join(dropped)}): the list may be extended, never trimmed"
            )
        if any(entry.get("sha256") == manifest.get("sha256") for entry in history):
            bad.append(
                "the current declaration carries a digest already listed as superseded, so "
                "the history no longer says which version is in force"
            )
    if not repro.get("reproduces"):
        off = [c["what"] for c in repro.get("recomputed", []) if not c["matches"]]
        bad.append(
            f"the harness does not recover the published persistence test ({', '.join(off)})"
        )
    for check in repro.get("against_the_page", []):
        if not check["matches"]:
            bad.append(
                f"published figure drifted from /methodology — {check['what']}: "
                f"run {check['run']}, page {check['page']}"
            )
    if not primary.get("available"):
        bad.append(f"the primary test did not run: {primary.get('reason')}")
    else:
        computed = primary.get("power", {}).get("by_predictor", {})
        missing = [n for n, b in computed.items() if b.get("mde80_clustered") is None]
        if not computed or missing:
            bad.append(
                "the primary test published no minimum detectable effect for "
                + (", ".join(missing) if missing else "any predictor")
            )
        # Declared and unfitted is a legitimate state; declared and unaccounted
        # for is how a predictor that was going to be gated stops being gated.
        accounted = set(primary.get("scored_predictors", [])) | set(
            primary.get("not_yet_fitted", [])
        )
        unaccounted = [p for p in declared if p not in accounted]
        if unaccounted:
            bad.append(
                f"the declaration names predictors the primary test neither scored nor "
                f"reported as unfitted: {', '.join(unaccounted)}"
            )

    read = secondary.get("season_plusminus_persistence", {}).get("scope_read")
    if read != statespace.FILTERED:
        bad.append(
            f"a forward test read {read!r} coefficients, and only "
            f"{statespace.FILTERED!r} has not seen the season it predicts"
        )
    for name, block in placebos.get("placebos", {}).items():
        if block.get("available") and not block.get("passes"):
            bad.append(f"placebo '{name}' found structure in data that has none")
    return bad


def harness_failures(report: dict[str, Any], models: set[str]) -> list[str]:
    """A run set with no diff report is a release nobody measured.

    Every phase from here on changes published numbers, and the promise each of
    them makes is that the moves are named. That promise is only checkable if
    the harness ran over the same runs the release publishes.
    """
    bad = []
    covered = {run["model"] for run in report.get("current", {}).get("runs", [])}
    missing = sorted(models - covered - {METRIC_DIFF_MODEL})
    for model in missing:
        bad.append(f"{model} published a run the metric diff never snapshotted")
    if not report.get("available") and report.get("reason"):
        bad.append(f"no comparison was made: {report['reason']}")
    return bad


def population_failures(
    population: dict[str, Any], stamped: list[tuple[str, str | None]]
) -> list[str]:
    """The evaluation population is held fixed, and every run says which one.

    A version compared against the version before it on a different set of maps
    is a comparison of two rulers. The hash each run records is what makes that
    detectable from outside the model.
    """
    bad = []
    if not population.get("frozen"):
        return ["no evaluation population has been cut (metricdiff --freeze CUT)"]
    if not population.get("readable", True):
        return [f"evaluation population '{population.get('cut')}': {population.get('reason')}"]

    frozen_hash = population.get("sha256")
    for model, recorded in stamped:
        if recorded is None:
            bad.append(f"{model} recorded no evaluation-set hash")
        elif recorded != frozen_hash:
            bad.append(
                f"{model} was scored against evaluation set {recorded[:16]}, "
                f"frozen set is {str(frozen_hash)[:16]}"
            )
    return bad


def page_figure_failures(retrodiction: dict[str, Any], career_rank: dict[str, Any]) -> list[str]:
    """Two figures the page states, held against the run that computes them.

    Both were printed on /methodology with no artifact behind them: the count of
    cells the one-sided property was checked on, and how well the team-strength
    proxy agrees with map win rate. A number a page asserts and no run computes
    cannot be audited, which is the failure this whole harness exists to catch.
    They are compared here, and a move fails: read it, write it down, and update
    the page in the same commit.
    """
    bad: list[str] = []
    checks: list[tuple[str, Any, Any, float]] = [
        (
            "cells the one-sided property was checked on",
            retrodiction.get("cells_before_total"),
            evalspec.PUBLISHED_FIGURES.get("retrodiction_cells_before"),
            0.0,
        )
    ]
    proxy = career_rank.get("team_strength_proxy_check") or {}
    pinned = evalspec.PUBLISHED_FIGURES.get("team_strength_proxy") or {}
    for key, tol in (("n_team_seasons", 0.0), ("pearson", 5e-3), ("spearman", 5e-3)):
        checks.append((f"team-strength proxy {key}", proxy.get(key), pinned.get(key), tol))
    for what, got, _want in [(c[0], c[1], c[2]) for c in checks if c[2] is None]:
        bad.append(f"{REPORTED}{what} is not pinned yet; the run computes {got}")
    for what, got, want, tol in checks:
        if want is None:
            continue
        if got is None:
            bad.append(f"{what}: the run published nothing, page says {want}")
        elif abs(float(got) - float(want)) > tol:
            bad.append(
                f"published figure drifted from /methodology — {what}: run {got}, page {want}"
            )
    return bad


def career_population_failures(drift: dict[str, Any]) -> list[str]:
    """The career engine's population is held fixed the same way the map one is.

    Drift here is reported rather than failed: a career that qualifies after a
    load is expected, and the engine follows it only at an explicit re-cut.
    Having no cut at all is a failure, because then nothing is held fixed.
    """
    if not drift.get("frozen"):
        return ["no career population has been cut (career_rank --freeze CUT)"]
    if not drift.get("readable", True):
        return [f"career population '{drift.get('cut')}': {drift.get('reason')}"]
    if drift.get("matches"):
        return []
    return [
        f"{REPORTED}career population '{drift['cut']}' holds {drift['n_players']:,} players "
        f"and {drift['eligible_now']:,} qualify now "
        f"(+{drift['n_added']:,} / -{drift['n_removed']:,}, not applied)"
    ]


def evaluation_stamps(conn: psycopg.Connection[Any]) -> list[tuple[str, str | None]]:
    """Per model, the evaluation-set hash its newest run recorded."""
    rows = conn.execute(
        "SELECT DISTINCT ON (model) model, "
        "  params->'provenance'->'evaluation_set'->>'sha256' "
        "FROM model_runs WHERE NOT (model = ANY(%s)) ORDER BY model, created_at DESC, id DESC",
        (sorted(EXTERNAL_MODELS | {METRIC_DIFF_MODEL}),),
    ).fetchall()
    return [(str(r[0]), None if r[1] is None else str(r[1])) for r in rows]


def published_models(conn: psycopg.Connection[Any]) -> set[str]:
    rows = conn.execute("SELECT DISTINCT model FROM model_runs").fetchall()
    return {str(r[0]) for r in rows}


def mode_naming_failures(scored: list[tuple[str, int]], named: set[str]) -> list[str]:
    """A mode the metric layer scores and `game_modes` does not name reaches the
    site as its raw slug, in a list of properly named ones. The site derives
    every mode label from that table, so an unnamed mode is not a missing label
    on one page — it is a missing label everywhere, and nothing else reports it."""
    return [
        f"{slug}: {rows:,} scored rows and no name in game_modes"
        for slug, rows in scored
        if slug not in named
    ]


def mode_vocabulary_failures(played: list[tuple[str, int]], rated: set[str]) -> list[str]:
    """A feature set is a dictionary keyed by mode slug, and a lookup that misses
    returns an empty tuple, so a mode nobody named is a mode nobody rates and
    nothing reports it. Ghosts Domination and Blitz sat unrated for four years
    and Black Ops 7 Overload for a season, each of them a third of its year's
    maps, under an all-modes label. This is the `TITLE_ORDER` treatment for
    modes: what the archive plays is the vocabulary, and the code follows it."""
    return [
        f"{slug}: {maps:,} maps and no feature set in player_rating.VERSIONS"
        for slug, maps in played
        if slug not in rated
    ]


def rated_cohort_failures(
    played: list[tuple[int, str, int]], cohorts: set[tuple[int, str]]
) -> list[str]:
    """A (season x mode) with box scores and no cohort publishes nothing, and the
    all-modes rating above it is a blend over whatever did fit. 2014 had no
    rating at all, and 2013, 2015 and 2016 published one with Search and Destroy
    missing from it - a third of those years' maps - because a denominator was
    declared and never available.

    The floor is `era.MIN_MAPS`, the count a player needs to be qualified: below
    it there is no cohort to fit rather than a cohort that was lost."""
    return [
        f"{year} {slug}: {maps:,} decided maps and no rating cohort"
        for year, slug, maps in sorted(played)
        if (year, slug) not in cohorts and maps >= MIN_MAPS
    ]


def played_modes(conn: psycopg.Connection[Any]) -> tuple[list[tuple[str, int]], set[str]]:
    """Modes the archive holds box scores for, and the modes a version rates.

    A mode counts as rated only where every feature-set version names it. The
    versions are fitted together and compared against each other, so one of them
    rating fewer modes than the rest is the same defect one step further in."""
    rows = conn.execute(
        "SELECT gm.slug, count(DISTINCT g.id) FROM game_player_stats gps"
        " JOIN games g ON g.id = gps.game_id"
        " JOIN game_modes gm ON gm.id = g.mode_id"
        " GROUP BY gm.slug"
    ).fetchall()
    if not rows:
        raise CannotRun("no box scores in this database")
    rated = set.intersection(*(set(spec) for spec in player_rating.VERSIONS.values()))
    return [(str(r[0]), int(r[1])) for r in rows], rated


def played_cohorts(conn: psycopg.Connection[Any]) -> list[tuple[int, str, int]]:
    """Every (season x mode) the archive holds decided box-score maps for."""
    rows = conn.execute(
        "SELECT s.year, gm.slug, count(DISTINCT g.id) FROM game_player_stats gps"
        " JOIN games g ON g.id = gps.game_id"
        " JOIN game_modes gm ON gm.id = g.mode_id"
        " JOIN series se ON se.id = g.series_id"
        " JOIN events e ON e.id = se.event_id"
        " JOIN seasons s ON s.id = e.season_id"
        " WHERE g.winner_team_id IS NOT NULL"
        " GROUP BY s.year, gm.slug"
    ).fetchall()
    if not rows:
        raise CannotRun("no box scores in this database")
    return [(int(r[0]), str(r[1]), int(r[2])) for r in rows]


def rated_cohorts(posterior: dict[str, Any], slug_of: dict[str, str]) -> set[tuple[int, str]]:
    """The (year, slug) pairs the published fit produced a cohort for. The
    posterior names a mode by its display name, so the mode table translates."""
    return {(int(c["year"]), slug_of[str(c["mode"])]) for c in posterior["cohorts"]}


def mode_slugs_by_name(conn: psycopg.Connection[Any]) -> dict[str, str]:
    rows = conn.execute("SELECT name, slug FROM game_modes WHERE name IS NOT NULL").fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def scored_modes(conn: psycopg.Connection[Any]) -> tuple[list[tuple[str, int]], set[str]]:
    """Modes carrying rows in the newest metric layer run, and the named set."""
    run_id = latest_run_id(conn, metrics.MODEL)
    if run_id is None:
        raise CannotRun(f"no {metrics.MODEL} run in this database")
    rows = conn.execute(
        "SELECT gm.slug, count(*) FROM ("
        "  SELECT mode_id FROM player_metric_season WHERE run_id = %s AND mode_id IS NOT NULL"
        "  UNION ALL"
        "  SELECT mode_id FROM team_metric_season WHERE run_id = %s AND mode_id IS NOT NULL"
        ") s JOIN game_modes gm ON gm.id = s.mode_id GROUP BY gm.slug",
        (run_id, run_id),
    ).fetchall()
    named = conn.execute(
        "SELECT slug FROM game_modes WHERE name IS NOT NULL AND name <> ''"
    ).fetchall()
    return [(str(r[0]), int(r[1])) for r in rows], {str(r[0]) for r in named}


# The site's read side, as source. A page that asks for an artifact no model
# writes renders its empty state and returns 200, so nothing in either test
# suite sees it — the site is a separate language from the models, and the two
# sets of names have no compiler between them. This is that compiler.
SITE_ANALYTICS = Path(__file__).resolve().parents[3] / "web" / "lib" / "analytics.ts"

_NAME_EQ = re.compile(r"name\s*=\s*'([a-z0-9_]+)'")
_NAME_IN = re.compile(r"name\s+IN\s*\(([^)]*)\)")
_SINGLE_QUOTED = re.compile(r"'([a-z0-9_]+)'")
_ARTIFACT_CALL = re.compile(r'artifactPayload<[^>]*>\(\s*[A-Za-z_.]+\s*,\s*"([a-z0-9_]+)"', re.S)
_BY_NAME = re.compile(r'byName\.get\("([a-z0-9_]+)"\)')
_META_LIST = re.compile(r"META_ARTIFACT_NAMES\s*=\s*\[(.*?)\]", re.S)
_DOUBLE_QUOTED = re.compile(r'"([a-z0-9_]+)"')


def artifact_names_read(source: str) -> set[str]:
    """Every model_artifacts.name the site asks for, read out of its source.

    Four shapes reach the database: an equality in a SQL template, an IN list,
    the artifactPayload helper, and a whole-run read picked apart by name."""
    names = set(_NAME_EQ.findall(source))
    for group in _NAME_IN.findall(source):
        names.update(_SINGLE_QUOTED.findall(group))
    names.update(_ARTIFACT_CALL.findall(source))
    names.update(_BY_NAME.findall(source))
    for group in _META_LIST.findall(source):
        names.update(_DOUBLE_QUOTED.findall(group))
    return names


def site_source() -> str:
    if not SITE_ANALYTICS.exists():
        raise CannotRun(f"no site source at {SITE_ANALYTICS}")
    return SITE_ANALYTICS.read_text()


def written_artifact_names(conn: psycopg.Connection[Any]) -> set[str]:
    rows = conn.execute("SELECT DISTINCT name FROM model_artifacts").fetchall()
    if not rows:
        raise CannotRun("no artifacts in this database")
    return {str(row[0]) for row in rows}


def site_read_failures(read: set[str], written: set[str]) -> list[str]:
    """Every name the site reads has to be a name some run wrote. The reverse is
    allowed: an artifact no page reads yet is a plan, not a defect."""
    return [f"the site reads '{name}', which no run has written" for name in sorted(read - written)]


def run_gates(conn: psycopg.Connection[Any]) -> list[tuple[str, list[str]]]:
    """Every gate against the newest run of its model. The predicates above take
    payloads rather than a connection, so the same conditions are exercised on
    fabricated artifacts in the test suite — a gate nothing has ever failed is
    indistinguishable from a gate that cannot fail."""
    return [
        (
            "rotation",
            rotation_failures(
                artifact(conn, maplevel.MODEL, "series_rollup"),
                artifact(conn, DYNAMICS_MODEL, "series_dynamics"),
            ),
        ),
        ("cohort", cohort_failures(rating_artifact(conn, "rating_posterior"))),
        (
            "rated cohorts",
            rated_cohort_failures(
                played_cohorts(conn),
                rated_cohorts(rating_artifact(conn, "rating_posterior"), mode_slugs_by_name(conn)),
            ),
        ),
        ("basis", basis_failures(artifact(conn, STYLE_MODEL, "player_style"))),
        ("mode naming", mode_naming_failures(*scored_modes(conn))),
        ("mode vocabulary", mode_vocabulary_failures(*played_modes(conn))),
        (
            "season plus-minus",
            season_rapm_failures(
                artifact(conn, statespace.MODEL, "rapm_season"), season_rapm_rows(conn)
            ),
        ),
        (
            "evaluation harness",
            evaluation_failures(
                artifact(conn, evalspec.MODEL, "evaluation_manifest"),
                artifact(conn, evalspec.MODEL, "evaluation_reproduction"),
                artifact(conn, evalspec.MODEL, "evaluation_primary"),
                artifact(conn, evalspec.MODEL, "evaluation_secondary"),
                artifact(conn, evalspec.MODEL, "evaluation_placebo"),
            ),
        ),
        (
            "skill prior",
            skill_prior_failures(
                artifact(conn, evalspec.MODEL, "skill_power"),
                artifact(conn, prior.MODEL, "skill_prior"),
                evalspec.PUBLISHED_FIGURES["skill_panel"],
                evalspec.PUBLISHED_FIGURES.get("skill_panel_remeasured"),
            ),
        ),
        ("aging", aging_failures(artifact(conn, aging.MODEL, "aging"))),
        ("career value", career_failures(artifact(conn, career.MODEL, "career_value"))),
        ("role", role_failures(artifact(conn, role.MODEL, "role"))),
        (
            "finding error control",
            error_control_failures(conn, artifact(conn, errorcontrol.MODEL, "error_control")),
        ),
        ("validation", validation_failures(validation_payloads(conn))),
        (
            "metric diff",
            harness_failures(
                artifact(conn, METRIC_DIFF_MODEL, REPORT_ARTIFACT), published_models(conn)
            ),
        ),
        (
            "site reads",
            site_read_failures(artifact_names_read(site_source()), written_artifact_names(conn)),
        ),
        (
            "evaluation population",
            population_failures(
                artifact(conn, METRIC_DIFF_MODEL, POPULATION_ARTIFACT), evaluation_stamps(conn)
            ),
        ),
        ("career population", career_population_failures(career_evalpop.drift(conn))),
        (
            "page figures",
            page_figure_failures(
                validation_payloads(conn).get("validation_retrodiction", {}),
                artifact(conn, career_rank.MODEL, career_rank.ARTIFACT_NAME),
            ),
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cdlhub_analytics.gates", description=__doc__)
    parser.add_argument("--dsn")
    args = parser.parse_args(argv)

    try:
        conn = connect(args.dsn)
    except psycopg.Error as exc:
        print(f"gates did not run: no database ({exc.__class__.__name__})", file=sys.stderr)
        return EXIT_CANNOT_RUN

    with conn:
        try:
            results = run_gates(conn)
        except CannotRun as exc:
            print(f"gates did not run: {exc}", file=sys.stderr)
            return EXIT_CANNOT_RUN

    failures: list[str] = []
    for name, found in results:
        bad = [line for line in found if not line.startswith(REPORTED)]
        for line in found:
            if line.startswith(REPORTED):
                print(f"{name}: {line.removeprefix(REPORTED)}")
            else:
                print(f"{name}: {line}", file=sys.stderr)
        failures.extend(bad)
        if not bad:
            print(f"{name}: ok")

    return EXIT_FAILED if failures else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
