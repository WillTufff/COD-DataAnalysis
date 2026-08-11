"""The metric-diff harness: what moved between two runs, and by how much.

Every published number is flattened to one `key -> value` pair under a key that
names the thing rather than the row that held it — a player's handle, not their
`player_id`; a metric's name, not its `run_id`. Two snapshots taken that way are
comparable across a refit, across a schema change, and across an identity merge
that renumbers half the database.

`snapshot` builds and stores one. `compare` merges two and reports the moves.
`evalpop` freezes the map population the comparison is scored on.
"""

from __future__ import annotations

MODEL = "metric_diff"
VERSION = "1.0.0"

# The noise floor. A difference counts as a move when
# |new - old| > ATOL + RTOL * |old|.
#
# Published numbers are stored in Postgres `real`, which carries about seven
# significant digits, so a relative tolerance of 1e-6 sits just above the
# storage floor and just below anything a refit can legitimately produce. It
# suppresses representation noise and nothing else: a real move is always
# counted and always named.
RTOL = 1e-6
ATOL = 1e-9

# Movers listed by name in the report, largest relative move first. The counts
# above them are over every move, not just these.
TOP_MOVERS = 60
