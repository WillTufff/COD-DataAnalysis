"""Stage transitions as NDJSON on stdout, one object per line.

Enabled with `--events ndjson`. Each line is::

    {"stage": "cito load", "status": "start", "elapsed_ms": 41230}
    {"stage": "cito load", "status": "done", "elapsed_ms": 62880, "stage_ms": 21650}

`elapsed_ms` counts from the moment the job started; `stage_ms` is the stage's
own duration and appears on `done` only. The lines are interleaved with the
ordinary output, which is unchanged, so a reader that does not understand them
sees log text.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any


class StageEvents:
    """Emits one line per stage transition when enabled, nothing when not."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._started = time.monotonic()
        self._open: str | None = None
        self._open_at = self._started

    def stage(self, name: str) -> None:
        """Close the stage in flight, if any, and open this one."""
        self.done()
        self._emit(name, "start")
        self._open = name
        self._open_at = time.monotonic()

    def done(self) -> None:
        """Close the stage in flight. A no-op when none is open."""
        if self._open is None:
            return
        self._emit(self._open, "done", stage_ms=self._ms_since(self._open_at))
        self._open = None

    def _ms_since(self, mark: float) -> int:
        return int((time.monotonic() - mark) * 1000)

    def _emit(self, stage: str, status: str, stage_ms: int | None = None) -> None:
        if not self.enabled:
            return
        event: dict[str, Any] = {
            "stage": stage,
            "status": status,
            "elapsed_ms": self._ms_since(self._started),
        }
        if stage_ms is not None:
            event["stage_ms"] = stage_ms
        json.dump(event, sys.stdout)
        sys.stdout.write("\n")
        sys.stdout.flush()


def add_argument(parser: Any) -> None:
    """Declare `--events ndjson` on a job's argument parser."""
    parser.add_argument(
        "--events",
        choices=["ndjson"],
        default=None,
        help="emit one NDJSON line per stage transition alongside the normal output",
    )
