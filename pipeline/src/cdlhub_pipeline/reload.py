"""Reload both sources from the snapshots already on disk. No network calls.

    uv run python -m cdlhub_pipeline.reload [--dsn DSN] [--events ndjson]

This is what an edit to `aliases.json` needs: the loaders are idempotent and
converge, so replaying them applies the new identity mappings without fetching
anything. The order is the same one `refresh` uses — the cito load re-nulls the
map winners the LPDB series fixes filled, so `lpdb load` always runs after it.
"""

from __future__ import annotations

import argparse
import os

from . import events
from .cito.__main__ import run_load as cito_run_load
from .lpdb.__main__ import run_load as lpdb_run_load

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cdlhub_pipeline.reload", description=__doc__)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    ap.add_argument("--skip-lpdb", action="store_true", help="cito only")
    events.add_argument(ap)
    args = ap.parse_args(argv)
    progress = events.StageEvents(args.events == "ndjson")

    progress.stage("cito load")
    print("== cito load ==")
    cito_run_load(args.dsn)

    if not args.skip_lpdb:
        progress.stage("lpdb load")
        print("\n== lpdb load ==")
        lpdb_run_load(args.dsn)
    progress.done()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
