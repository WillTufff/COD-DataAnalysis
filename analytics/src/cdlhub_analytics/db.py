"""Connection helper shared by all analytics modules."""

from __future__ import annotations

import os

import psycopg

DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"


def connect(dsn: str | None = None) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(dsn or os.environ.get("DATABASE_URL", DEFAULT_DSN))
