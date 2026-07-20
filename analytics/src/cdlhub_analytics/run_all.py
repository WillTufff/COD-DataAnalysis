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

from . import backtest, era, insights, metrics
from .db import connect
from .ratings import comparison, fit, player_rating, winprob

ELO_K = 32.0
GLICKO_TAU = 0.5


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=None)
    args = ap.parse_args(argv)

    from .writeback import open_run

    with connect(args.dsn) as conn:
        series = fit.load_series(conn)
        if not series:
            print("no decided series in database — import data first", file=sys.stderr)
            return 1
        through = fit.data_through(series)
        print(f"{len(series)} decided series through {through}")

        era_run = open_run(conn, "era_adjust", "1.0.0", {"min_maps": era.MIN_MAPS}, through)
        n = era.compute_and_write(conn, era_run)
        print(f"era_adjust run {era_run}: {n} player-season-mode rows")

        metric_run = open_run(
            conn,
            metrics.MODEL,
            metrics.VERSION,
            {
                "min_maps": era.MIN_MAPS,
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

        elo_run = open_run(conn, "elo", "1.0.0", {"k": ELO_K, "level": "series"}, through)
        preds = fit.fit_elo(conn, elo_run, series, k=ELO_K)
        report = backtest.evaluate(preds)
        backtest.write(conn, elo_run, report)
        print(
            f"elo run {elo_run}: {len(preds)} predictions, "
            f"brier {report.brier:.4f}, log-loss {report.log_loss:.4f}, "
            f"accuracy {report.accuracy:.3f}"
        )

        glicko_run = open_run(
            conn, "glicko2", "1.0.0", {"tau": GLICKO_TAU, "period": "series"}, through
        )
        preds = fit.fit_glicko2(conn, glicko_run, series, tau=GLICKO_TAU)
        report = backtest.evaluate(preds)
        backtest.write(conn, glicko_run, report)
        print(
            f"glicko2 run {glicko_run}: {len(preds)} predictions, "
            f"brier {report.brier:.4f}, log-loss {report.log_loss:.4f}, "
            f"accuracy {report.accuracy:.3f}"
        )

        # Every feature-set version is fitted and backtested; the published one
        # is named in player_rating, never inferred from insertion order.
        rating_rows, rating_coverage = player_rating.load(conn)
        rating_runs: dict[str, int] = {}
        for version in player_rating.ALL_VERSIONS:
            run = open_run(
                conn,
                "player_rating",
                version,
                {
                    "l2": player_rating.L2,
                    "shrink_maps": player_rating.SHRINK_MAPS,
                    "rating_scale": player_rating.RATING_SCALE,
                    "min_train_games": player_rating.MIN_TRAIN_GAMES,
                    "bootstrap_b": player_rating.BOOTSTRAP_B,
                    "published": version == player_rating.PUBLISHED_VERSION,
                },
                through,
            )
            rating_runs[version] = run
            n, pr_preds, _ = player_rating.compute_and_write(
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

        wp_run = open_run(
            conn,
            "winprob",
            "1.0.0",
            {
                "l2": winprob.L2,
                "min_train": winprob.MIN_TRAIN,
                "refit_every": winprob.REFIT_EVERY,
                "form_window": winprob.FORM_WINDOW,
            },
            through,
        )
        wp_preds, wp_artifact = winprob.fit_walk_forward(series)
        winprob.write_artifact(conn, wp_run, wp_artifact)
        report = backtest.evaluate(wp_preds)
        backtest.write(conn, wp_run, report)
        print(
            f"winprob run {wp_run}: {len(wp_preds)} predictions, "
            f"brier {report.brier:.4f}, log-loss {report.log_loss:.4f}, "
            f"accuracy {report.accuracy:.3f}"
        )

        ins_run = open_run(
            conn,
            "insights",
            "1.2.0",
            {
                "era_run_id": era_run,
                "elo_run_id": elo_run,
                "glicko_run_id": glicko_run,
                "player_rating_run_id": pr_run,
                "winprob_run_id": wp_run,
                "metric_run_id": metric_run,
            },
            through,
        )
        n = insights.generate(
            conn, ins_run, era_run, elo_run, pr_run, wp_run, glicko_run, metric_run
        )
        print(f"insights run {ins_run}: {n} atoms")

        conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
