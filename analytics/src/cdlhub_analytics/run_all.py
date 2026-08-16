"""Fit every model and regenerate insights, all through model_runs.

    uv run python -m cdlhub_analytics.run_all [--dsn DSN]

Order matters: era adjustment and ratings first, then insights read their
outputs. Each model gets its own versioned run; reruns with identical
(model, version, data_through) replace the previous run's outputs wholesale.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import cast

import psycopg

from . import (
    aging,
    backtest,
    career,
    cohort,
    era,
    errorcontrol,
    events,
    insights,
    metrics,
    role,
    roundwp,
    segmentwp,
    seriesdyn,
    style,
    validation,
)
from .career_rank import engine as career_rank
from .db import connect
from .metricdiff import run as metricdiff
from .ratings import (
    admission,
    comparison,
    context,
    evalspec,
    evaluate,
    fit,
    hierarchical,
    holdout,
    leakage,
    maplevel,
    opponent,
    player_rating,
    preflight,
    prior,
    rapm,
    significance,
    simleague,
    skillbase,
    statespace,
    sweep,
    winprob,
)

Conn = psycopg.Connection[tuple[object, ...]]

ELO_K = 32.0
GLICKO_TAU = 0.5
# A CWL event is a few days of dense play then weeks of nothing, which is the
# shape Glicko-2's rating periods assume. See fit.PERIODS and the sweep artifact.
GLICKO_PERIOD = fit.DEFAULT_PERIOD


# Roster-stint roles that make a slot a substitute or a coach. Matched on the
# loaded `roster_stints.role`, which is LPDB's free text, so the test is a
# substring rather than a set — "Head Coach", "SnD Coach" and "Temp Sub" are
# all real values.
SUBSTITUTE_ROLES = ("sub", "stand-in")
COACH_ROLES = ("coach",)

_SUB_MATCH = " OR ".join(f"lower(rs.role) LIKE '%%{term}%%'" for term in SUBSTITUTE_ROLES)
_COACH_MATCH = " OR ".join(f"lower(role) LIKE '%%{term}%%'" for term in COACH_ROLES)

# Slots a substitute actually filled: the stint has to cover the map's date, so
# a player who once stood in for someone does not have their whole career
# flagged. An open-ended stint runs to today.
_SUBSTITUTE_SLOTS_SQL = f"""
SELECT DISTINCT gps.game_id, gps.player_id
FROM game_player_stats gps
JOIN games g       ON g.id = gps.game_id
JOIN series s      ON s.id = g.series_id
JOIN roster_stints rs ON rs.player_id = gps.player_id
                     AND s.played_at::date >= rs.start_date
                     AND s.played_at::date <= COALESCE(rs.end_date, CURRENT_DATE)
WHERE rs.role IS NOT NULL AND ({_SUB_MATCH})
"""  # noqa: S608 (the match clause is built from the literals above)

# Coach-only people: every stint they have is a coaching one. A player who
# later coached is not one of these, and should not be.
_COACH_ONLY_SQL = f"""
SELECT player_id FROM roster_stints
GROUP BY player_id
HAVING count(*) FILTER (WHERE role IS NOT NULL AND ({_COACH_MATCH})) = count(*)
"""  # noqa: S608 (as above)


def rapm_identity(conn: Conn) -> rapm.Identity:
    """Who the plus-minus columns are, per PD item 6's rule.

    Unresolved means the handle is still an open merge candidate in the ops
    identity queue — a question nobody has answered, not a question nobody
    asked. The count travels with the coefficients so a reader can see how much
    of the leaderboard rests on it.
    """
    from .ops import identity as identity_queue

    open_candidates: set[int] = set()
    for pair in identity_queue.candidates(conn):
        for side in ("left", "right"):
            open_candidates.add(int(pair[side]["player_id"]))
    substitutes = {
        (cast(int, r[0]), cast(int, r[1])) for r in conn.execute(_SUBSTITUTE_SLOTS_SQL).fetchall()
    }
    coaches = {cast(int, r[0]) for r in conn.execute(_COACH_ONLY_SQL).fetchall()}
    return rapm.Identity(
        unresolved_players=frozenset(open_candidates),
        substitute_slots=frozenset(substitutes),
        coach_only_players=frozenset(coaches),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None)
    events.add_argument(ap)
    args = ap.parse_args(argv)
    progress = events.StageEvents(args.events == "ndjson")

    from .writeback import open_run, prune_superseded

    with connect(args.dsn) as conn:
        series = fit.load_series(conn)
        if not series:
            print("no decided series in database — import data first", file=sys.stderr)
            return 1
        through = fit.data_through(series)
        lineage = fit.load_lineage(conn)
        n_merged = sum(1 for team, founder in lineage.items() if team != founder)
        print(f"{len(series)} decided series through {through}")
        print(f"org lineage: {n_merged} teams rated under an earlier brand")

        progress.stage("era_adjust")
        era_run = open_run(
            conn,
            "era_adjust",
            "1.1.0",
            {"min_maps": era.MIN_MAPS, "min_z_cohort": cohort.MIN_Z_COHORT},
            through,
        )
        n = era.compute_and_write(conn, era_run)
        print(f"era_adjust run {era_run}: {n} player-season-mode rows")

        progress.stage("metric_layer")
        metric_run = open_run(
            conn,
            metrics.MODEL,
            metrics.VERSION,
            {
                "min_maps": era.MIN_MAPS,
                "min_z_cohort": cohort.MIN_Z_COHORT,
                "min_snd_rounds": metrics.MIN_SND_ROUNDS,
                "min_ctrl_rounds": metrics.MIN_CTRL_ROUNDS,
                "min_shots": metrics.MIN_SHOTS,
                "min_kills": metrics.MIN_KILLS,
                "trade_window_ms": metrics.TRADE_WINDOW_MS,
                "min_feed_deaths": metrics.MIN_FEED_DEATHS,
                "min_clutch_attempts": metrics.MIN_CLUTCH_ATTEMPTS,
                "n_metrics": len(metrics.CATALOG),
            },
            through,
        )
        n = metrics.compute_and_write(conn, metric_run)
        print(f"metric_layer run {metric_run}: {n} player-metric rows")

        # The event tier's own model: P(win round | survivors), and the win
        # probability each kill added. Independent of everything below it —
        # nothing in the ratings reads it, deliberately, for the reason the
        # reliability block gives.
        progress.stage("round_wp")
        round_run = open_run(
            conn,
            roundwp.MODEL,
            roundwp.VERSION,
            roundwp.params(),
            through,
        )
        round_arts = roundwp.build_artifacts(conn)
        for name, payload in round_arts.items():
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (round_run, name, json.dumps(payload)),
            )
        if round_arts:
            rwp = round_arts["round_win_prob"]
            bt = rwp["backtest"]
            print(
                f"round_wp run {round_run}: {rwp['table']['n_rounds']} SnD rounds, "
                f"{len(rwp['table']['cells'])} states"
            )
            if bt["available"]:
                scored = {m["model"]: m["brier"] for m in bt["models"]}
                print(
                    f"  walk-forward over {bt['n_rounds']} rounds in "
                    f"{bt['n_events_scored']} events: state table brier "
                    f"{scored['state_table']:.5f} vs coin flip {scored['coin_flip']:.5f}"
                )
                for p in bt["nested"]:
                    verdict = "resolved" if p["excludes_zero"] else "no difference"
                    print(
                        f"  {p['a']} − {p['b']}: {p['delta']:+.6f} "
                        f"[{p['lo']:+.6f}, {p['hi']:+.6f}] p={p['dm_p']:.3f} "
                        f"({verdict}; detectable at {p['mde80']:.6f})"
                    )
            tl = round_arts["round_timeline"]
            lat = tl["trade_latency"]
            print(
                f"  timeline: {tl['n_rounds_spanned']} rounds on a "
                f"{tl['bin_ms'] // 1000}s grid "
                f"({tl['n_rounds_span_conflict']} dropped for a span the feed contradicts)"
            )
            for d in tl["leader_drift"]:
                verdict = "resolves" if d["excludes_zero"] else "no difference"
                print(
                    f"  the side ahead at {d['t_s']}s: won {d['observed']:.3f} against the "
                    f"table's {d['model']:.3f}, gap {d['gap']:+.4f} ±{d['se']:.4f} ({verdict})"
                )
            print(
                f"  trade answers: {lat['n_answered']} of {lat['n_deaths']} deaths ever "
                f"answered, median {lat['median_ms'] / 1000:.1f}s, "
                f"{lat['within_of_answered']:.3f} of them inside the "
                f"{lat['window_ms'] // 1000}s window"
            )
            rel = round_arts["round_wpa"]["reliability"]
            if rel["available"]:
                resid = next(c for c in rel["cells"] if c["key"] == "wpa_resid")
                print(
                    f"  WPA per round correlates {rel['corr_wpa_kills']:.3f} with kill rate; "
                    f"with kill rate removed it repeats at r={resid['r']:+.3f} "
                    f"[{resid['lo']:+.3f}, {resid['hi']:+.3f}] over {rel['n_players']} players "
                    f"(detectable at {rel['r_detectable']:.3f}) — not a rating input"
                )
        else:
            print(f"round_wp run {round_run}: no kill feed in this database, nothing to fit")

        # The same question one level up, for the era the kill feed never
        # covered: P(win the map | the score state), from the segments migration
        # 0016 parsed out of teamGameStats. Placed next to round_wp because it is
        # the same object at a coarser resolution, and read by nothing below it.
        progress.stage("segment_wp")
        seg_run = open_run(
            conn,
            segmentwp.MODEL,
            segmentwp.VERSION,
            segmentwp.params(),
            through,
        )
        seg_arts = segmentwp.build_artifacts(conn)
        for name, payload in seg_arts.items():
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (seg_run, name, json.dumps(payload)),
            )
        if seg_arts:
            swp = seg_arts["segment_win_prob"]
            rules = swp["anomaly_rules"]
            kept = ", ".join(f"{k} {n}" for k, n in sorted(rules["maps_kept"].items()))
            print(f"segment_wp run {seg_run}: {kept} maps")
            print(
                f"  {rules['maps_truncated']} maps truncated for "
                f"{rules['segments_dropped']} unscorable segments; dropped "
                + ", ".join(
                    f"{kind} {sum(reasons.values())}"
                    for kind, reasons in sorted(rules["maps_dropped"].items())
                )
            )
            for kind, block in swp["by_mode"].items():
                bt = block["backtest"]
                if not bt["available"]:
                    print(f"  {kind}: {bt['reason']}")
                    continue
                brier = {m["model"]: m["brier"] for m in bt["models"]}
                gap = bt["pairs"][0]
                verdict = "resolves" if gap["excludes_zero"] else "no difference"
                print(
                    f"  {kind}: {bt['n_maps']} maps, table brier "
                    f"{brier['state_table']:.5f} against the race baseline's "
                    f"{brier['race_baseline']:.5f} ({gap['delta']:+.5f}, {verdict}; "
                    f"detectable at {gap['detectable']:.5f})"
                )
            two_era = swp["two_era_snd"]
            worst = two_era["largest_disagreement"]
            if worst is not None:
                print(
                    f"  SnD across two sources: {two_era['modern']['n_maps']} CDL maps "
                    f"against {two_era['feed']['n_maps']} kill-feed maps over "
                    f"{len(two_era['cells'])} states, widest gap {worst['delta']:+.4f} at "
                    f"{worst['own']}-{worst['opp']} (z={worst['z']:.2f}); "
                    f"{two_era['feed']['excluded_for_a_different_race']} "
                    f"2017 maps left out for a shorter race"
                )
        else:
            print(f"segment_wp run {seg_run}: no segments in this database, nothing to fit")

        progress.stage("elo")
        elo_run = open_run(
            conn,
            "elo",
            "1.0.0",
            {"k": ELO_K, "level": "series", "lineage_merged_teams": n_merged},
            through,
        )
        elo_preds = fit.fit_elo(conn, elo_run, series, k=ELO_K, lineage=lineage)
        report = backtest.evaluate(elo_preds)
        backtest.write(conn, elo_run, report)
        print(
            f"elo run {elo_run}: {len(elo_preds)} predictions, "
            f"brier {report.brier:.4f}, log-loss {report.log_loss:.4f}, "
            f"accuracy {report.accuracy:.3f}"
        )

        progress.stage("glicko2")
        glicko_run = open_run(
            conn,
            "glicko2",
            "1.1.0",
            {
                "tau": GLICKO_TAU,
                "period": GLICKO_PERIOD,
                "lineage_merged_teams": n_merged,
            },
            through,
        )
        glicko_preds = fit.fit_glicko2(
            conn, glicko_run, series, tau=GLICKO_TAU, lineage=lineage, period=GLICKO_PERIOD
        )
        report = backtest.evaluate(glicko_preds)
        backtest.write(conn, glicko_run, report)
        print(
            f"glicko2 run {glicko_run}: {len(glicko_preds)} predictions, "
            f"brier {report.brier:.4f}, log-loss {report.log_loss:.4f}, "
            f"accuracy {report.accuracy:.3f}"
        )

        # Sensitivity, stored against the Glicko run: how much K, tau and the
        # period granularity actually move the numbers. Never used to pick them.
        grid = sweep.sweep(
            series, lineage, elo_k=ELO_K, glicko_tau=GLICKO_TAU, glicko_period=GLICKO_PERIOD
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (glicko_run, "hyperparameter_sweep", json.dumps(grid)),
        )
        best_g, best_e = grid["best_by_brier"]["glicko2"], grid["best_by_brier"]["elo"]
        print(
            f"sweep: best elo K={best_e['k']:g} brier {best_e['brier']:.5f}; "
            f"best glicko tau={best_g['tau']:g} period={best_g['period']} "
            f"brier {best_g['brier']:.5f} (published settings kept)"
        )

        # Every feature-set version is fitted and backtested; the published one
        # is named in player_rating, never inferred from insertion order.
        progress.stage("player_rating")
        rating_rows, rating_coverage = player_rating.load(conn)
        rating_runs: dict[str, int] = {}
        for version in player_rating.ALL_VERSIONS:
            run = open_run(
                conn,
                "player_rating",
                version,
                {
                    "l2": player_rating.L2,
                    "estimator": player_rating.PUBLISHED_ESTIMATOR,
                    "shrink_fallback": player_rating.SHRINK_FALLBACK,
                    "rating_scale": player_rating.RATING_SCALE,
                    "min_train_games": player_rating.MIN_TRAIN_GAMES,
                    "bootstrap_b": player_rating.BOOTSTRAP_B,
                    "em_tol": hierarchical.EM_TOL,
                    "published": version == player_rating.PUBLISHED_VERSION,
                },
                through,
            )
            rating_runs[version] = run
            n, pr_preds, pr_artifacts = hierarchical.compute_and_write(
                conn, run, version, rating_rows, rating_coverage
            )
            report = backtest.evaluate(pr_preds)
            backtest.write(conn, run, report)
            flag = " (published)" if version == player_rating.PUBLISHED_VERSION else ""
            print(
                f"player_rating {version} run {run}{flag}: {n} rating rows; "
                f"map-level walk-forward: {report.n} predictions, "
                f"brier {report.brier:.4f}, accuracy {report.accuracy:.3f}"
            )
            shrink = pr_artifacts["rating_shrinkage"]
            print(
                f"  shrinkage k estimated per cohort: median {shrink['median']:g}, "
                f"range {shrink['min']:g}-{shrink['max']:g} "
                f"(fallback {shrink['fallback']:g} used by {shrink['n_fell_back']})"
            )
            post = pr_artifacts["rating_posterior"]
            moved = post["vs_z_shrink"]
            print(
                f"  posterior fit over {post['n_cohorts']} cohorts: median k "
                f"{post['median_k']:g}, {post['n_collapsed']} with no measurable spread, "
                f"{post['n_fell_back']} fell back; signal share "
                f"{min(c['signal_share'] for c in post['cohorts']):.2f}-"
                f"{max(c['signal_share'] for c in post['cohorts']):.2f}"
            )
            if moved["available"]:
                print(
                    f"  vs z-and-shrink: rating moves {moved['mean_abs_delta']:.4f} on average "
                    f"({moved['max_abs_delta']:.4f} at most), rank corr "
                    f"{moved['spearman']:.4f}, {moved['top10_unchanged']}/10 of the top ten kept; "
                    f"intervals x{moved['interval']['median']:.2f} the bootstrap's"
                )
            check = post["calibration"]
            if check["available"]:
                print(
                    f"  observation variance calibrated over {check['n_player_seasons']} "
                    f"player-seasons: resampled SD is {check['median']:.3f}x the assumed "
                    f"sigma/sqrt(m) (cohort range {check['min']:.3f}-{check['max']:.3f})"
                )

        pr_run = rating_runs[player_rating.PUBLISHED_VERSION]
        versus = comparison.compare(
            conn,
            player_rating.ALL_VERSIONS,
            player_rating.PUBLISHED_VERSION,
            rating_rows,
            rating_coverage,
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (pr_run, "rating_model_comparison", json.dumps(versus)),
        )
        best = versus["overall"][player_rating.PUBLISHED_VERSION]
        base = versus["overall"][versus["baseline"]]
        print(
            f"rating comparison over {versus['common_maps']} common maps: "
            f"{versus['baseline']} brier {base['brier']:.4f} -> "
            f"{player_rating.PUBLISHED_VERSION} brier {best['brier']:.4f}"
        )

        # How much of that accuracy is the scoreboard predicting itself. Every
        # version is measured, onto its own run, and not only the published one:
        # a version that is fitted but not yet published is exactly the one
        # whose new columns have not faced the sign rule, which is the moment
        # the review is worth having. The site reads this per rating run, so it
        # keeps seeing the published version's copy.
        for version, run in rating_runs.items():
            leak = leakage.measure(conn, version, rating_rows, rating_coverage)
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (run, "feature_sign_baseline", json.dumps(leak)),
            )
            worst = min(leak["by_cohort"], key=lambda c: c["model_gain"])
            print(
                f"sign-rule baseline {version}: {worst['year']} {worst['title']} "
                f"{worst['mode']} best column {worst['best_feature']['key']} alone picks "
                f"{worst['best_feature']['accuracy']:.1%} vs model "
                f"{worst['model_accuracy']:.1%} ({worst['model_gain'] * 100:+.1f} pt)"
            )

            # The same maps read per column rather than per cohort: which titles
            # carry it, what it costs in dropped sides, whether it already knows
            # the winner and whether it points the same way on every title.
            admitted = admission.measure(conn, version, rating_rows, rating_coverage)
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (run, "feature_admission", json.dumps(admitted)),
            )
            print(
                f"  admission {version}: {admitted['n_features']} columns, "
                f"{admitted['n_leaky']} at or above {admission.LEAKY_ACCURACY:.0%} on the sign "
                f"rule alone, {admitted['n_not_portable']} pointing different ways across titles"
            )

        # The two tests the rating can fail: does it persist for a player, and
        # does a roster forecast future map wins. Whatever they say gets stored.
        persist = holdout.persistence(conn, pr_run, era_run)
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (pr_run, "rating_persistence", json.dumps(persist)),
        )
        kd_cell, r_cell = persist["cells"]["kd->kd"], persist["cells"]["rating->kd"]
        verdict = "separates" if persist["contrasts"]["kd"]["excludes_zero"] else "no difference"
        print(
            f"persistence over {persist['n_pairs']} season pairs: predicting next "
            f"K/D z, rating r={r_cell['r']:.3f} vs K/D z r={kd_cell['r']:.3f} "
            f"({verdict})"
        )

        # Player value measured in wins rather than box scores. Stored with the
        # published rating because its whole purpose is to be read against it.
        usable_rows = [r for r in rating_rows if player_rating.usable(r)]
        published_ratings = holdout.fit_prefix(
            usable_rows, rating_coverage, player_rating.PUBLISHED_VERSION
        )
        forecast = holdout.roster_forecast(
            conn, player_rating.PUBLISHED_VERSION, glicko_run, rating_rows, rating_coverage
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (pr_run, "roster_forecast", json.dumps(forecast)),
        )
        if forecast["common"]["available"]:
            cp = forecast["common"]["predictors"]
            print(f"roster forecast over {forecast['common']['n_maps']} future maps:")
            vs_flip = forecast["contrasts"].get("vs_coin_flip", {})
            for name, score in sorted(cp.items(), key=lambda kv: kv[1]["brier"]):
                gap = vs_flip.get(name)
                tail = ""
                if gap:
                    verdict = "resolved" if gap["excludes_zero"] else "spans zero"
                    tail = (
                        f" — vs coin flip {gap['delta']:+.5f} "
                        f"[{gap['lo']:+.5f}, {gap['hi']:+.5f}] ({verdict}"
                        + (
                            f", detectable at {gap['mde80']:.5f})"
                            if gap.get("mde80") is not None
                            else ")"
                        )
                    )
                print(f"  {name:14s} brier {score['brier']:.5f} acc {score['accuracy']:.4f}{tail}")
            # The one contrast this run exists to settle: the published estimator
            # against the one it replaced, on identical maps.
            swap = forecast["contrasts"].get("brier", {}).get("rating_zshrink")
            if swap:
                verdict = "resolved" if swap["excludes_zero"] else "spans zero"
                print(
                    f"  posterior − z-and-shrink: {swap['delta']:+.5f} "
                    f"[{swap['lo']:+.5f}, {swap['hi']:+.5f}] ({verdict}"
                    + (
                        f"; detectable at {swap['mde80']:.5f})"
                        if swap.get("mde80") is not None
                        else ")"
                    )
                )

        # Declared as its own stage: it is minutes of work inside a stage that
        # claims to be finished, so the ops app's progress bar was reporting the
        # run further along than it was.
        progress.stage("rapm")
        identity = rapm_identity(conn)
        rapm_art = rapm.artifact(usable_rows, published_ratings, identity=identity)
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (pr_run, "rapm", json.dumps(rapm_art)),
        )
        if rapm_art["available"]:
            print(
                f"rapm over {rapm_art['n_maps']} maps, {rapm_art['n_players']} players: "
                f"{rapm_art['n_resolved']} coefficients exceed 1.96 SE; "
                f"{rapm_art['n_concentrated']} players never play apart from one teammate "
                f"(median concentration {rapm_art['concentration_median']:.2f})"
            )
            lineup = rapm_art["lineup"]
            print(
                f"  lineup rule: {lineup['maps_dropped']} maps dropped for an "
                f"incomplete or unequal side"
            )
            ident = rapm_art["identity"]
            if ident:
                print(
                    f"  identity: {ident['unresolved_players']} handles still open merge "
                    f"candidates, touching {ident['maps_with_an_unresolved_slot']} maps "
                    f"({ident['share_of_maps_unresolved']:.1%}); "
                    f"{ident['substitute_slots']} substitute slots; "
                    f"{ident['coach_slots']} coach slots"
                )
        # The artifact above is a top-and-bottom-forty. Player pages need the
        # whole fit, so it also lands in a table keyed by player.
        rapm_rows = rapm.player_rows(usable_rows)
        if rapm_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO player_rapm "
                    "(run_id, player_id, maps, coef, se, teammate_concentration) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [(pr_run, *r) for r in rapm_rows],
                )
            print(f"  {len(rapm_rows)} player_rapm rows written")

        # winprob's baseline features are only a baseline if they come from the
        # same fits as the rows it is tabled against, so the rating settings are
        # handed over here rather than restated inside the module.
        progress.stage("winprob")
        wp_run = open_run(
            conn,
            "winprob",
            "1.1.0",
            {
                "l2": winprob.L2,
                "min_train": winprob.MIN_TRAIN,
                "refit_every": winprob.REFIT_EVERY,
                "form_window": winprob.FORM_WINDOW,
                "k": ELO_K,
                "tau": GLICKO_TAU,
                "period": GLICKO_PERIOD,
                "lineage_merged_teams": n_merged,
            },
            through,
        )
        wp_preds, wp_artifact, wp_trace = winprob.fit_walk_forward(
            series,
            lineage=lineage,
            period=GLICKO_PERIOD,
            elo_k=ELO_K,
            glicko_tau=GLICKO_TAU,
        )
        winprob.write_artifact(conn, wp_run, wp_artifact)
        report = backtest.evaluate(wp_preds)
        backtest.write(conn, wp_run, report)
        print(
            f"winprob run {wp_run}: {len(wp_preds)} predictions, "
            f"brier {report.brier:.4f}, log-loss {report.log_loss:.4f}, "
            f"accuracy {report.accuracy:.3f}"
        )

        # The gaps in the backtest table, with intervals, and what size of form
        # effect this archive could have found. Stored against winprob because it
        # is winprob's null the pair of them defends.
        gaps = significance.model_gaps(
            {"elo": elo_preds, "glicko2": glicko_preds, "winprob_v1": wp_preds}
        )
        gaps["form_power"] = significance.form_power(wp_trace)
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (wp_run, "model_gaps", json.dumps(gaps)),
        )
        if gaps["available"]:
            resolved = [p for p in gaps["pairs"] if p["excludes_zero"]]
            print(
                f"model gaps over {gaps['n_series']} series: "
                f"{len(resolved)}/{len(gaps['pairs'])} Brier differences exclude zero"
            )
            for p in gaps["pairs"]:
                print(
                    f"  {p['a']} − {p['b']}: {p['delta']:+.5f} "
                    f"[{p['lo']:+.5f}, {p['hi']:+.5f}] "
                    f"DM t={p['dm_t']:+.2f} p={p['dm_p']:.3f}, "
                    f"detectable at {p['mde80']:.5f}"
                )
            fp = gaps["form_power"]
            if fp["available"] and fp["beta_detectable"] is not None:
                print(
                    f"  form power: a coefficient of {fp['beta_detectable']:.2f} on form_diff "
                    f"(a 10-0 vs 0-10 swing worth {fp['swing_pp']:.1f} pt of win probability) "
                    f"would have been detectable; the fit found "
                    f"{wp_artifact['final_weights']['form_diff']:+.2f}"
                )

        # The same teams rated on the 5,087 maps underneath those series, and
        # once per mode. Fitted after winprob because its series rollup is
        # compared against the whole series-level table, not scored alone.
        progress.stage("map_elo")
        maps = maplevel.load_maps(conn)
        map_run = open_run(conn, maplevel.MODEL, maplevel.VERSION, maplevel.params(), through)
        map_arts, map_series_preds = maplevel.build_artifacts(
            conn,
            maps,
            lineage,
            {"elo": elo_preds, "glicko2": glicko_preds, "winprob_v1": wp_preds},
        )
        for name, payload in map_arts.items():
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (map_run, name, json.dumps(payload)),
            )
        # The backtest row is the published arm's *series* rollup, so this model
        # lands in the same table as Elo and Glicko-2 on the same units.
        report = backtest.evaluate(map_series_preds)
        backtest.write(conn, map_run, report)
        mb = map_arts["map_backtest"]
        print(
            f"map_elo run {map_run}: {mb['n_maps']} maps, "
            + ", ".join(f"{arm} brier {mb['arms'][arm]['brier']:.5f}" for arm in maplevel.ARMS)
            + f" (coin flip {mb['coin_flip_brier']:.5f})"
        )
        if mb["gaps"]["available"]:
            for p in mb["gaps"]["pairs"]:
                verdict = "resolved" if p["excludes_zero"] else "no difference"
                print(
                    f"  map {p['a']} − {p['b']}: {p['delta']:+.5f} "
                    f"[{p['lo']:+.5f}, {p['hi']:+.5f}] p={p['dm_p']:.3f} "
                    f"({verdict}; detectable at {p['mde80']:.5f})"
                )
        sp = map_arts["mode_specialization"]
        if sp["available"]:
            verdict = "exceeds the null" if sp["exceeds_null"] else "inside the null"
            print(
                f"  mode specialization over {sp['n_cells']} (team, mode) cells: "
                f"spread {sp['observed_sd']:.1f} pts vs permuted {sp['null_mean_sd']:.1f} "
                f"[{sp['null_lo']:.1f}, {sp['null_hi']:.1f}] p={sp['p_value']:.4f} ({verdict})"
            )
        ro = map_arts["series_rollup"]
        print(
            f"  series rollup over {ro['n_series']} series: "
            + ", ".join(f"{k} brier {v['brier']:.5f}" for k, v in ro["arms"].items())
        )
        if ro["gaps"]["available"]:
            for p in ro["gaps"]["pairs"]:
                if not p["a"].startswith("map_") and not p["b"].startswith("map_"):
                    continue
                verdict = "resolved" if p["excludes_zero"] else "no difference"
                print(
                    f"  series {p['a']} − {p['b']}: {p['delta']:+.5f} "
                    f"[{p['lo']:+.5f}, {p['hi']:+.5f}] p={p['dm_p']:.3f} ({verdict})"
                )

        # Whether the next phase is available at all. The published plus-minus
        # has one coefficient per career; giving it one per season is only worth
        # writing if the record identifies it, and that is measurable before
        # anything is fitted. Placed after map_elo because the simulation is
        # calibrated to that model's own map accuracy — a synthetic league has to
        # be as hard to predict as this one or its recovery curve is a statement
        # about the generator.
        progress.stage("preflight")
        seasons = preflight.load_seasons(conn)
        identification = preflight.measure(usable_rows, seasons)
        published_arm = mb["arms"][mb["published_arm"]]
        recovery = simleague.artifact(
            map_accuracy=float(published_arm["accuracy"]),
            n_persistence_pairs=int(persist["n_pairs"]),
            baseline_r=float(persist["cells"]["kd->kd"]["r"]),
        )
        fork = preflight.verdict(identification, recovery["recovery_closed"])
        pc_run = open_run(
            conn,
            "rapm_preflight",
            "1.0.0",
            {
                "penalty_lambda": preflight.PENALTY_LAMBDA,
                "thin_column_maps": preflight.THIN_COLUMN_MAPS,
                "stop_rule": fork["thresholds"],
                "design_hash": identification.design_hash,
                "simulation_seed": simleague.SEED,
                "simulation_replicates": recovery["replicates"],
            },
            through,
        )
        for name, payload in (
            ("rapm_identification", identification.payload()),
            ("rapm_recovery", recovery),
            ("rapm_preflight_verdict", fork),
        ):
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (pc_run, name, json.dumps(payload)),
            )
        expanded = identification.payload()["season_expanded"]
        print(
            f"rapm_preflight run {pc_run}: season-expanded design {expanded['columns']} columns, "
            f"rank {expanded['rank']} against a schedule ceiling of {expanded['rank_ceiling']} "
            f"({expanded['distinct_lineups']} distinct lineups)"
        )
        for era_row in fork["by_era"]:
            print(
                f"  {era_row['league']}: {era_row['median_effective_lineups']} effective "
                f"lineups per team-season, rank {era_row['rank_share']:.0%} of player columns, "
                f"simulated teammate recovery r={era_row['recovery_within_team']} "
                f"-> {era_row['branch']}"
            )
        print(
            f"  borrowed at zero churn: r={recovery['borrowing']['open']} with transfers, "
            f"r={recovery['borrowing']['closed']} without; smoothing inflates a forward test "
            f"by {recovery['smoothing']['inflation']:+.4f}; persistence MDE80 "
            f"{recovery['power']['mde80']}"
        )
        print(f"  fork: {fork['branch']}")

        # The model the pre-flight above cleared, at the resolution it cleared
        # per era: one coefficient per player per season for the CDL, pooled to
        # the era for 2017-2019, both as deviations from an explicit
        # team-season effect. Placed here because the resolution is read from
        # that verdict rather than declared, so this stage cannot drift from the
        # measurement that permits it.
        progress.stage("season_rapm")
        how = statespace.resolutions(fork)
        admitted_games, _lineup_rule = rapm.admitted_maps(usable_rows)
        season_art, season_rows, team_rows = statespace.artifact(
            admitted_games, seasons, how, [(row[0], row[2]) for row in rapm_rows]
        )
        ss_run = open_run(
            conn,
            statespace.MODEL,
            statespace.VERSION,
            {
                "resolution_by_league": how,
                "preflight_branch": fork["branch"],
                "preflight_run_id": pc_run,
                "admission_maps": statespace.ADMISSION_MAPS,
                "publication_maps": statespace.MIN_MAPS_PUBLISH,
                "response": statespace.MARGIN,
                "bootstrap_b": statespace.BOOTSTRAP_B,
                "bootstrap_seed": statespace.BOOTSTRAP_SEED,
                "design_hash": season_art.get("design_hash"),
                "penalties": season_art.get("penalties"),
            },
            through,
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (ss_run, "rapm_season", json.dumps(season_art)),
        )
        if season_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO player_rapm "
                    "(run_id, scope, player_id, season_id, resolution, maps, coef, se, "
                    "teammate_concentration, penalty_share) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            ss_run,
                            c.scope,
                            c.player_id,
                            c.season_id,
                            c.resolution,
                            c.maps,
                            c.coef,
                            c.se,
                            c.teammate_concentration,
                            c.penalty_share,
                        )
                        for c in season_rows
                    ],
                )
        # The column a player coefficient is a deviation from. Stored because a
        # career total has to say whether it credited the team term, and the
        # answer is not recoverable from the player rows alone.
        if team_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO team_season_effect "
                    "(run_id, team_id, season_id, scope, resolution, coef) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [
                        (ss_run, t.team_id, t.season_id, statespace.FILTERED, t.resolution, t.coef)
                        for t in team_rows
                    ],
                )
        if season_art["available"]:
            cols, pen = season_art["columns"], season_art["penalties"]
            print(
                f"season_rapm run {ss_run}: {cols['player_cells']} player cells, "
                f"{cols['team_seasons']} team-seasons and {cols['replacement_buckets']} "
                f"replacement buckets over {season_art['n_maps_fitted']} maps "
                f"(λ0={pen['lambda0']}, λw={pen['lambda_walk']} by GCV, "
                f"{pen['effective_df']} effective df)"
            )
            print(
                "  resolution: "
                + ", ".join(f"{k} {v}" for k, v in season_art["resolution_by_league"].items())
                + f"; {len(season_rows)} rows written over two scopes"
            )
            share = season_art["penalty_share"]
            print(
                f"  {share['dominated_share']:.0%} of player cells are penalty dominated "
                f"against a reference of {share['reference']} (k/(k+1)); median teammate "
                f"concentration inside a cell "
                f"{season_art['teammate_concentration']['median']}"
            )
            rel = season_art["reliability"]
            if rel["available"]:
                print(
                    f"  split-half reliability within a cell over {rel['n_player_cells']} "
                    f"player-cells: r={rel['r']} [{rel['lo']}, {rel['hi']}], "
                    f"Spearman-Brown {rel['spearman_brown']}"
                )
            for arm in season_art["against_published"].get("arms", []):
                print(
                    f"  vs the published career fit, {arm['arm']}: "
                    f"rank corr {arm['rank_corr']} over {arm['n_players']} players"
                )
        else:
            print(f"season_rapm run {ss_run}: {season_art['reason']}")

        # What the box score owed to who was across from it. The plus-minus
        # already conditions on opposition, so nothing here touches it; what is
        # adjusted is the per-map rate every published per-player statistic is
        # built from. Placed after glicko2 because the cheapest rung reads that
        # rating, and the ladder above it reads nothing but the lineups.
        progress.stage("opponent_adjust")
        seasons_by_id, modes_by_id = player_rating.label_context(conn)
        events_by_id = {
            cast(int, r[0]): cast(str, r[1])
            for r in conn.execute("SELECT id, name FROM events").fetchall()
        }
        panels = opponent.build_panels(
            usable_rows,
            player_rating.build_cohorts(
                usable_rows, rating_coverage, player_rating.PUBLISHED_VERSION
            ),
            opponent.load_ratings(conn),
        )
        opponent_art = opponent.artifact(panels, seasons_by_id, modes_by_id, events_by_id)
        oa_run = open_run(
            conn,
            opponent.MODEL,
            opponent.VERSION,
            {
                "feature_set_version": player_rating.PUBLISHED_VERSION,
                "adopted_rung": opponent_art["stop_rule"]["adopted"],
                **opponent_art["params"],
                "bootstrap_seed": opponent.BOOTSTRAP_SEED,
                "bootstrap_b": opponent.BOOTSTRAP_B,
            },
            through,
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (oa_run, "opponent_adjustment", json.dumps(opponent_art)),
        )
        adopted = opponent_art["stop_rule"]["adopted"]
        print(
            f"opponent_adjust run {oa_run}: {len(opponent_art['by_cohort'])} cohort-features "
            f"over {len(panels)} cohorts; adopted {adopted}"
        )
        for rung, stats in opponent_art["ladder"].items():
            print(
                f"  {rung}: median leaderboard move {stats['mean_abs_dz_median']} sd, "
                f"placebo ratio {stats['placebo_ratio_median']}, reliability gain "
                f"{stats['reliability_gain_median']} "
                f"({stats['reliability_gain_positive']}/{stats['reliability_measured']} up)"
            )
        blind = opponent_art["coverage"]
        print(
            f"  the team rating is blind on {blind['blind_share']:.1%} of lines "
            f"({blind['opponent_at_prior']} still at the 1500 prior, "
            f"{blind['opponent_missing']} unrated)"
        )
        control = opponent_art["controls"]["positive"]
        print(
            f"  positive control recovers a planted opponent effect at "
            f"r={control['correlation']}, slope {control['slope']}"
        )
        for era_name, stats in opponent_art["schedule"].get("by_era", {}).items():
            print(
                f"  {era_name}: mean opposition worth {stats['mean_delta_z']} sd per line, "
                f"95th percentile |{stats['p95_abs_delta_z']}|"
            )

        # What the box score owed to the circumstances of the map: the venue,
        # the stage, the map itself. Placed after the opponent ladder because
        # the opponent block sits in the same design, so a context coefficient
        # is what survives holding the opposition fixed.
        progress.stage("match_context")
        match_ctx = context.load_context(conn)
        players_by_id = {
            cast(int, r[0]): cast(str, r[1])
            for r in conn.execute("SELECT id, handle FROM players").fetchall()
        }
        context_art = context.artifact(panels, match_ctx, seasons_by_id, modes_by_id, players_by_id)
        mc_run = open_run(
            conn,
            context.MODEL,
            context.VERSION,
            {
                "feature_set_version": player_rating.PUBLISHED_VERSION,
                **context_art["params"],
            },
            through,
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (mc_run, "match_context", json.dumps(context_art)),
        )
        print(
            f"match_context run {mc_run}: {len(context_art['per_cohort'])} cohort-features "
            f"over {len(panels)} cohorts"
        )
        for family, verdict in context_art["ablation"]["verdicts"].items():
            stats = context_art["ablation"]["by_family"][family]
            move = (stats.get("leaderboard_move") or {}).get("median")
            delta = (stats.get("oof_rmse_delta") or {}).get("median")
            print(
                f"  {family}: {verdict}; median move {move} sd, "
                f"out-of-fold RMSE {delta:+} on "
                f"{stats.get('cohorts_improved')}/{stats.get('cohorts_measured')} cohorts"
            )
        venue_finding = context_art["venue_effect"]
        print(
            f"  per-player venue effect: {venue_finding['n_clearing_interval']} of "
            f"{len(venue_finding['players'])} player-cohort-features clear their interval "
            f"against the cohort's common effect, over {venue_finding['n_players']} players"
        )
        host_finding = context_art["host_effect"]
        print(
            f"  home-market effect: {host_finding['n_clearing_interval']} of "
            f"{len(host_finding['per_cohort'])} cohort-features clear their interval"
        )

        # The baseline the persistence gate is declared against: an online
        # player rating that knows who was on the server and nothing else. It
        # runs here as a stage with its own run, artifact and backtest row
        # rather than as a script, because a baseline in a hard gate that
        # cannot be reproduced makes the gate unenforceable.
        progress.stage("openskill")
        os_fit = skillbase.fit_walk_forward(usable_rows)
        os_run = open_run(
            conn,
            skillbase.MODEL,
            skillbase.VERSION,
            {
                "mu": skillbase.MU,
                "sigma": skillbase.SIGMA,
                "min_maps_season": skillbase.MIN_MAPS_SEASON,
                "model": "plackett_luce",
            },
            through,
        )
        player_names = {
            cast(int, r[0]): cast(str, r[1])
            for r in conn.execute("SELECT id, handle FROM players").fetchall()
        }
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (os_run, "openskill_baseline", json.dumps(skillbase.artifact(os_fit, player_names))),
        )
        os_report = backtest.evaluate(list(os_fit.predictions.values()))
        backtest.write(conn, os_run, os_report)
        print(
            f"openskill run {os_run}: {os_fit.n_maps} maps, {os_fit.n_players} players, "
            f"{len(os_fit.season_skill)} published player-seasons; map-level walk-forward "
            f"brier {os_report.brier:.5f}, accuracy {os_report.accuracy:.4f}"
        )

        # The box score asked the question the composite never asks: not what a
        # line is worth on the scoreboard, but whose presence preceded a moved
        # margin. Placed between the baseline and the harness because it is the
        # third predictor the harness scores and the floor it is judged against
        # was computed by an earlier run of that harness.
        progress.stage("skill_prior")
        prior_cohorts = player_rating.build_cohorts(usable_rows, rating_coverage, prior.FEATURE_SET)
        design = prior.build(
            prior.load_targets(conn, ss_run, seasons_by_id),
            prior_cohorts,
            player_rating.aggregate_players(usable_rows, prior_cohorts),
        )
        prior_w, tau2 = prior.weights(design)
        signal = prior.target_signal(design)
        arms = prior.ladder(design, prior_w)
        published_arm = arms["published_arm"]
        # Refitted at the full draw count once the arm is chosen. The ladder
        # compares at a reduced one so three arms cost what one would; the
        # intervals that get stored are not read off a comparison.
        predictions = prior.walk_forward(design, prior_w, arm=published_arm)
        exposure = prior.exposure_loading(design, predictions)
        variance = prior.prior_variance(design, predictions)
        skills = (
            prior.blend_all(design, predictions, float(variance["value"]), published_arm)
            if variance.get("available")
            else []
        )
        prior_art = prior.artifact(
            design,
            tau2,
            signal,
            arms,
            exposure,
            variance,
            skills,
            prior.style_correlations(conn, skills),
            prior.coefficients(design, prior_w),
        )
        sp_run = open_run(
            conn,
            prior.MODEL,
            prior.VERSION,
            {**prior.params(design, tau2, published_arm), "season_rapm_run_id": ss_run},
            through,
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (sp_run, "skill_prior", json.dumps(prior_art)),
        )
        if skills:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO player_skill (run_id, player_id, season_id, prior_mean, "
                    "prior_sd, coef, se, skill, skill_sd, weight_prior, scope, model) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            sp_run,
                            s.player_id,
                            s.season_id,
                            s.prior_mean,
                            s.prior_sd,
                            s.coef,
                            s.se,
                            s.skill,
                            s.skill_sd,
                            s.weight_prior,
                            prior.TARGET_SCOPE,
                            s.model,
                        )
                        for s in skills
                    ],
                )
        print(f"skill_prior run {sp_run}: {prior.headline(prior_art)}; {len(skills)} rows written")
        for arm_name, block in prior_art["ladder"]["arms"].items():
            if not block.get("available"):
                print(f"  {arm_name}: not fitted here — {block.get('reason')}")
                continue
            against = block.get("vs_ridge")
            tail = (
                ""
                if against is None
                else (
                    f"; vs ridge {against['delta_r']:+.4f} "
                    f"[{against['lo']:+.4f}, {against['hi']:+.4f}] "
                    + ("ships" if against["ships"] else "does not ship")
                )
            )
            print(f"  {arm_name}: out-of-fold r={block['out_of_fold_r']}{tail}")
        print(
            f"  exposure loading {exposure.get('prior_r2')} against the target's "
            f"{exposure.get('target_r2')} (ratio {exposure.get('ratio')}, "
            + ("passes" if exposure.get("passes") else "FAILS")
            + ")"
        )
        print(f"  {prior_art['statement']}")

        # The harness that scores all of it, and the one part of this pipeline
        # that was built before the thing it judges. Its own gate comes first:
        # an independent recomputation of the published persistence test, which
        # nothing below is read until it matches.
        progress.stage("evaluate")
        eval_arts = evaluate.artifacts(
            conn,
            pr_run,
            era_run,
            ss_run,
            usable_rows,
            os_fit,
            persist,
            forecast,
            admitted_games,
            {(s.player_id, s.season_id): s.skill for s in skills},
        )
        ev_run = open_run(
            conn,
            evalspec.MODEL,
            evalspec.VERSION,
            {
                "manifest_sha256": evalspec.sha256(),
                "invariants_sha256": evalspec.invariants_sha256(),
                "primary_test": evalspec.PRIMARY.name,
                "scope_permitted_forward": evalspec.SCOPE,
                "bootstrap_b": evalspec.BOOTSTRAP_B,
                "bootstrap_seed": evalspec.BOOTSTRAP_SEED,
                "openskill_run_id": os_run,
                "season_rapm_run_id": ss_run,
                "rating_run_id": pr_run,
                "era_run_id": era_run,
            },
            through,
        )
        for name, payload in eval_arts.items():
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (ev_run, name, json.dumps(payload)),
            )
        repro = eval_arts["evaluation_reproduction"]
        print(
            f"evaluate run {ev_run}: reproduction "
            + ("recovers" if repro["reproduces"] else "FAILS to recover")
            + f" the published persistence test ({repro['n_mismatched']} of "
            f"{len(repro['recomputed'])} cells off); {repro['n_page_mismatched']} of "
            f"{len(repro['against_the_page'])} published figures disagree with /methodology"
        )
        print(f"  {evaluate.headline(eval_arts)}")
        power = eval_arts["evaluation_primary"].get("power", {})
        if power:
            unit = eval_arts["evaluation_primary"]["resampling"]["unit"]
            print(
                f"  detectable, after a design effect of {power['design_effect']} from "
                f"clustering on the {unit}: "
                + ", ".join(
                    f"{name} {block['mde80_clustered']} (from {block['mde80_independent']} "
                    f"at agreement {block['predictor_agreement']})"
                    for name, block in power["by_predictor"].items()
                )
            )
        floor = eval_arts["skill_power"]
        if floor["available"]:
            wider = floor["wider_panel"]
            print(
                f"  the SKILL panel is {floor['n_eligible']} of {floor['n_panel']} transitions "
                f"over {floor['clusters']} players ({wider['n']} if era-resolution rows "
                f"counted, which they do not); {floor['statement']}"
            )
        else:
            print(f"  skill_power: {floor['reason']}")
        pla = eval_arts["evaluation_placebo"]
        print(
            f"  placebos: {pla['n_run']} run, {pla['n_failed']} failed; "
            + ", ".join(
                f"{name} {'ok' if block.get('passes') else 'FAILED'}"
                for name, block in pla["placebos"].items()
                if block.get("available")
            )
        )

        # What a series is, as opposed to what its teams are: the value of a 1-0
        # lead, how often a race to three sweeps or goes the distance, and
        # whether any of it is memory rather than the two teams being further
        # apart than the rating said. Fitted after map_elo because it freezes the
        # same map-level ratings at the start of each series.
        progress.stage("series_dynamics")
        sd_run = open_run(conn, seriesdyn.MODEL, seriesdyn.VERSION, seriesdyn.params(), through)
        sd_arts = seriesdyn.build_artifacts(conn, lineage)
        for name, payload in sd_arts.items():
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (sd_run, name, json.dumps(payload)),
            )
        if sd_arts:
            dyn, mom = sd_arts["series_dynamics"], sd_arts["series_momentum"]
            print(f"series_dynamics run {sd_run}: {dyn['n_series']} best-of-five series")
            print(f"  {seriesdyn.headline(dyn, mom)}")
            for row in dyn["rates"]:
                rating, quality = row["vs"]["rating"], row["vs"]["quality"]
                print(
                    f"  {row['event']:16s} {row['observed']:.3f} observed; "
                    f"vs rating {rating['delta']:+.3f} "
                    f"[{rating['lo']:+.3f}, {rating['hi']:+.3f}], "
                    f"vs quality {quality['delta']:+.3f} "
                    f"[{quality['lo']:+.3f}, {quality['hi']:+.3f}]"
                )
            q = mom["quality"]
            naive = next(
                t
                for s in mom["map2"]["specs"]
                if s["spec"] == "strength_prev"
                for t in s["terms"]
                if t["term"] == "prev"
            )
            print(
                f"  winning map 1 is worth {naive['swing_pp']:+.1f} pt on map 2 against the "
                f"rating alone; against a series quality offset of "
                f"{q['full']['sigma']:.2f} logits it is {q['swing_pp']:+.1f} pt "
                f"[{q['swing_pp_lo']:+.1f}, {q['swing_pp_hi']:+.1f}] "
                f"(p={q['p']:.3f}, detectable at {q['mde80_swing_pp']:.1f} pt)"
            )
            first3 = q["first_three"]
            print(
                f"  on maps 1-3, the one panel with no stopping rule: "
                f"{first3['swing_pp']:+.1f} pt "
                f"[{first3['swing_pp_lo']:+.1f}, {first3['swing_pp_hi']:+.1f}]"
            )
        else:
            print(f"series_dynamics run {sd_run}: no usable series, nothing to fit")

        # Every season number above, added up. Placed after the fits it reads
        # and before the interpretation layer, because a career total is an
        # aggregation of published quantities rather than a new estimate: it
        # opens no design matrix and fits nothing.
        progress.stage("career")
        career_rows, career_art = career.build(conn, pr_run, ss_run)
        cv_run = open_run(
            conn,
            career.MODEL,
            career.VERSION,
            {
                **career.params(),
                "rating_run_id": pr_run,
                "season_rapm_run_id": ss_run,
            },
            through,
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (cv_run, "career_value", json.dumps(career_art)),
        )
        if career_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO player_career (run_id, player_id, axis, credit, era_scope, "
                    "seasons, maps, replacement, total, total_sd, peak, peak_season_id, "
                    "best_three, best_three_start_season_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            cv_run,
                            r.player_id,
                            r.axis,
                            r.credit,
                            r.era_scope,
                            r.seasons,
                            r.maps,
                            r.replacement,
                            r.total,
                            r.total_sd,
                            r.peak,
                            r.peak_season_id,
                            r.best_three,
                            r.best_three_start_season_id,
                        )
                        for r in career_rows
                    ],
                )
        print(f"career run {cv_run}: {career.headline(career_art)}")
        for key, count in sorted(career_art["rows_by_key"].items()):
            block = career_art["separation"][key]
            share = block["share_clear"]
            clears = "no interval" if share is None else f"{share:.1%} clear of zero"
            print(f"  {key}: {count} careers, {clears}")
        rules = career_art["credit_rules"]
        if rules["rank_correlation"] is not None:
            print(
                f"  the two credit rules agree at rho {rules['rank_correlation']:.3f} over "
                f"{rules['n_players']} players, sharing {rules['top_ten_overlap']} of the top ten"
            )

        # A second, independent career axis: peak/best-three/total over the
        # gold-tier metric basket (plus award credit) instead of over VALUE or
        # SKILL. Full archive, not the frozen evaluation population — the
        # frozen set exists to keep the formula honest during development
        # (see ai/career-rank-preregistration.md), not to gate what the site
        # publishes.
        progress.stage("career_rank")
        cr_rows, cr_payload, cr_run = career_rank.write(conn)
        if cr_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO player_season_rank (run_id, player_id, season_id, score, "
                    "sd, net_of_teammates, opponent_strength) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            cr_run,
                            row.player_id,
                            season_id,
                            score,
                            row.season_sd.get(season_id),
                            row.net_of_teammates.get(season_id),
                            row.opponent_strength.get(season_id),
                        )
                        for row in cr_rows
                        for season_id, score in row.seasons.items()
                    ],
                )
                cur.executemany(
                    "INSERT INTO player_career_rank (run_id, player_id, qualified, n_seasons, "
                    "total, total_sd, peak, peak_season_id, best_three, "
                    "best_three_start_season_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            cr_run,
                            row.player_id,
                            row.career.qualified,
                            row.career.n_seasons,
                            row.career.total,
                            row.career.total_sd,
                            row.career.peak,
                            row.career.peak_season_id,
                            row.career.best_three,
                            row.career.best_three_start_season_id,
                        )
                        for row in cr_rows
                    ],
                )
        print(
            f"career_rank run {cr_run}: {cr_payload['n_players_scored']} players scored, "
            f"{cr_payload['career']['n_qualified']} qualified"
        )

        # The same seasons on an age axis, and the selection problem that makes
        # any single curve of them wrong. Three fits, published together,
        # because the disagreement between them is the measurement.
        progress.stage("aging")
        curve_rows, aging_art = aging.build(conn, pr_run, era_run, ss_run)
        ag_run = open_run(
            conn,
            aging.MODEL,
            aging.VERSION,
            {
                **aging.params(),
                "rating_run_id": pr_run,
                "era_run_id": era_run,
                "season_rapm_run_id": ss_run,
            },
            through,
        )
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (ag_run, "aging", json.dumps(aging_art)),
        )
        if curve_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO career_curves (run_id, player_id, population, fit, component, "
                    "x_is_age, age_or_seq, fitted, lo95, hi95) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            ag_run,
                            r.player_id,
                            r.population,
                            r.fit,
                            r.component,
                            r.x_is_age,
                            r.age_or_seq,
                            r.fitted,
                            r.lo95,
                            r.hi95,
                        )
                        for r in curve_rows
                    ],
                )
        print(f"aging run {ag_run}: {aging.headline(aging_art)}; {len(curve_rows)} curve rows")
        for key, block in sorted(aging_art["populations"].items()):
            interval = block["peak_interval"]
            if interval["lo"] is None:
                print(
                    f"  {key}: {block['n_observations']} player-seasons, no fit located "
                    "an interior peak"
                )
                continue
            fits = ", ".join(
                f"{name} {payload['peak']}"
                for name, payload in sorted(block["fits"].items())
                if payload["peak"] is not None
            )
            print(
                f"  {key}: peak {interval['lo']}-{interval['hi']} over "
                f"{block['n_players']} players ({fits})"
            )

        # What a player is, as opposed to how good they are: the continuous
        # style axes left in the metric layer once the composite rating is
        # projected out of it, and the archetype taxonomy that does not survive
        # a cloud with no clusters in it. Fitted after the ratings because it
        # residualises against the published composite.
        progress.stage("player_style")
        style_run = open_run(conn, style.MODEL, style.VERSION, style.params(), through)
        orientation = {m.key: m.higher_is_better for m in metrics.CATALOG}
        style_arts, style_fits = style.build_artifacts(conn, metric_run, pr_run, orientation)
        for name, payload in style_arts.items():
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (style_run, name, json.dumps(payload)),
            )
        if style_fits:
            # One fit per era; a player-season belongs to exactly one of them,
            # so the axis rows of the two never collide.
            rows = [r for f in style_fits for r in style.axis_rows(f)]
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO player_style_season "
                    "(run_id, player_id, season_id, axis, score, pctl) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    [(style_run, *r) for r in rows],
                )
            print(f"player_style run {style_run}: {len(rows)} player-season-axis rows")
            for style_fit in style_fits:
                print(f"  {style.headline(style_fit)}")
                payload = next(
                    b
                    for b in style_arts["player_style"]["bases"]
                    if b["basis"] == style_fit.basis.name
                )
                for axis in payload["axes"]:
                    print(
                        f"    axis {axis['index']} {axis['name']}: {axis['share']:.1%} of residual"
                    )
        else:
            print(f"player_style run {style_run}: too few complete rows, nothing fitted")

        # Who takes the opening fight and what it goes with. After the style
        # axes because the recovery test scores them, and on two eras because
        # the weapon label and the trade columns never share one.
        progress.stage("role")
        role_run = open_run(conn, role.MODEL, role.VERSION, role.params(), through)
        _, role_costs, role_rows, role_art = role.build(conn, style_run)
        role.write(conn, role_run, role_rows)
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (role_run, "role", json.dumps(role_art)),
        )
        print(f"role run {role_run}: {role.headline(role_art)}, {len(role_rows)} player-seasons")
        for cost in role_costs:
            print(
                f"  {cost.outcome}: {cost.slope:+.3f} SD "
                f"[{cost.lo95:+.3f}, {cost.hi95:+.3f}] over {cost.n_seasons} seasons"
            )

        progress.stage("validate")
        val_run = open_run(conn, validation.MODEL, validation.VERSION, validation.params(), through)
        val_arts = validation.build(
            conn,
            pr_run,
            sp_run,
            admitted_games,
            seasons,
            how,
            (
                float(season_art["penalties"]["lambda0"]),
                float(season_art["penalties"]["lambda_walk"]),
            ),
        )
        for name, payload in val_arts.items():
            conn.execute(
                "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
                (val_run, name, json.dumps(payload)),
            )
        print(f"validation run {val_run}:")
        for name, payload in val_arts.items():
            print(f"  {name}: {validation.headline(payload)}")

        progress.stage("insights")
        ins_run = open_run(
            conn,
            "insights",
            "1.3.0",
            {
                "era_run_id": era_run,
                "elo_run_id": elo_run,
                "glicko_run_id": glicko_run,
                "player_rating_run_id": pr_run,
                "winprob_run_id": wp_run,
                "metric_run_id": metric_run,
                "map_elo_run_id": map_run,
                "series_dynamics_run_id": sd_run,
            },
            through,
        )
        n = insights.generate(
            conn, ins_run, era_run, elo_run, pr_run, wp_run, glicko_run, metric_run, map_run, sd_run
        )
        print(f"insights run {ins_run}: {n} atoms")

        # What each of those claims is worth once the search behind it is
        # counted. Runs after the feed exists because it corrects the feed, and
        # before the prune so its own run is the one kept.
        progress.stage("error_control")
        ec_run = open_run(
            conn,
            errorcontrol.MODEL,
            errorcontrol.VERSION,
            {**errorcontrol.params(), "insights_run_id": ins_run},
            through,
        )
        corrected, ec_art = errorcontrol.build(conn, ins_run)
        errorcontrol.write(conn, corrected)
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (ec_run, "error_control", json.dumps(ec_art)),
        )
        print(f"error_control run {ec_run}: {ec_art['statement']}")
        for family in ec_art["families"]:
            if family["class"] != errorcontrol.TESTABLE:
                continue
            print(
                f"  {family['kind']}: {family['fails_threshold']} of {family['tested']} "
                f"retracted, median q {family['q_bh']['median']:.3f}"
            )

        # Everything above is what this run publishes. Older runs of the same
        # models — earlier insight and metric-layer versions in particular — are
        # never read again, so they go rather than accumulating one copy of the
        # league per rerun. The rating versions all survive: they are published
        # baselines, and all three ids are handed over here.
        removed = prune_superseded(
            conn,
            {
                "era_adjust": [era_run],
                "metric_layer": [metric_run],
                roundwp.MODEL: [round_run],
                segmentwp.MODEL: [seg_run],
                "elo": [elo_run],
                "glicko2": [glicko_run],
                "player_rating": list(rating_runs.values()),
                "rapm_preflight": [pc_run],
                statespace.MODEL: [ss_run],
                skillbase.MODEL: [os_run],
                prior.MODEL: [sp_run],
                evalspec.MODEL: [ev_run],
                "winprob": [wp_run],
                maplevel.MODEL: [map_run],
                seriesdyn.MODEL: [sd_run],
                style.MODEL: [style_run],
                career.MODEL: [cv_run],
                career_rank.MODEL: [cr_run],
                aging.MODEL: [ag_run],
                "insights": [ins_run],
                errorcontrol.MODEL: [ec_run],
                role.MODEL: [role_run],
            },
        )
        if removed:
            detail = ", ".join(f"{m} x{n}" for m, n in sorted(removed.items()))
            print(f"pruned superseded runs: {detail}")

        # What this run changed about every number above, against the run
        # before it. Last, because it snapshots what the others just wrote.
        progress.stage("metric_diff")
        diff = metricdiff.execute(conn)
        print(metricdiff.headline(diff))
        print(metricdiff.population_line(diff["population"]))
        for fam in diff["families"]:
            if fam["moved"] or fam["flipped"] or fam["added"] or fam["removed"]:
                print(
                    f"  {fam['family']:16s} {fam['moved']:>8,} moved  "
                    f"{fam['flipped']:>6,} flipped  {fam['added']:>6,} new  "
                    f"{fam['removed']:>6,} gone  max |delta| {fam['max_abs_delta']:g}"
                )
        for mover in diff["movers"][:20]:
            print(f"  {mover['key']}: {mover['old']:g} -> {mover['new']:g} ({mover['delta']:+g})")
        if diff["movers_omitted"]:
            print(f"  ... and {diff['movers_omitted']:,} more moves not named here")

        progress.done()
        conn.commit()
        pruned = metricdiff.publish(diff)
        if pruned:
            print(f"pruned {pruned} superseded snapshot{'s' if pruned != 1 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
