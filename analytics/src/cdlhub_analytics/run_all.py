"""Fit every model and regenerate insights, all through model_runs.

    uv run python -m cdlhub_analytics.run_all [--dsn DSN]

Order matters: era adjustment and ratings first, then insights read their
outputs. Each model gets its own versioned run; reruns with identical
(model, version, data_through) replace the previous run's outputs wholesale.
"""

from __future__ import annotations

import argparse
import sys

from . import backtest, era, insights
from .db import connect
from .ratings import fit

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

        ins_run = open_run(
            conn,
            "insights",
            "1.0.0",
            {"era_run_id": era_run, "elo_run_id": elo_run},
            through,
        )
        n = insights.generate(conn, ins_run, era_run, elo_run)
        print(f"insights run {ins_run}: {n} atoms")

        conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
