"""Shared fake-connection helper for career_rank tests.

The career_rank modules take a live psycopg connection and call
`conn.execute(sql)` (sometimes iterated directly, sometimes `.fetchall()`-ed).
None of the SQL text in those modules is parameterised on anything a unit
test needs to vary, so a fake connection that hands back canned rows for
each successive `execute()` call is enough to exercise the pure logic
around the query without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]


class FakeCursor:
    def __init__(self, rows: Sequence[tuple[Any, ...]]) -> None:
        self._rows = list(rows)

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def __iter__(self) -> Any:
        return iter(self._rows)


class FakeConn:
    """Returns rows from a queue, one batch per `execute()` call, in order."""

    def __init__(self, *batches: Sequence[tuple[Any, ...]]) -> None:
        self._batches = list(batches)
        self._calls = 0

    def execute(self, _sql: str, _params: object = None) -> FakeCursor:
        rows = self._batches[self._calls] if self._calls < len(self._batches) else []
        self._calls += 1
        return FakeCursor(rows)


def as_conn(fake: FakeConn) -> Conn:
    """Type-narrowing shim: the career_rank modules only call `.execute()`,
    which `FakeConn` implements, but they are typed against a real psycopg
    connection. Casting here, once, keeps every test call site honest about
    what it is actually passing.
    """
    return cast(Conn, fake)
