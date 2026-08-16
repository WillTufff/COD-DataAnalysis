"""The shape of the project as a graph: sources, jobs, tables, models, pages.

Nothing here is a drawing of how it was meant to fit together. Every node
carries a count read from the database or the filesystem, and every edge is
either a row count on a join that exists (a source's rows in a table, a model's
rows in an output table) or a route's reference to a table in the web source.
An edge that stops being true stops being drawn.

The one thing declared rather than derived is which job loads which source and
which job fits the models — that is the job catalog's own knowledge.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

from . import REPO_ROOT, Conn, rows_as_dicts
from .runs import OUTPUT_TABLES

# Tables carrying a data_source column: the rows a load actually writes.
SOURCED_TABLES = ("series", "games", "game_player_stats", "event_placements")
# The rest of the row-level record. Counted, but not attributed to a source.
SHARED_TABLES = ("players", "teams", "events", "roster_stints", "transfers")
# Tables a fit reads. The models are fit from the box scores and the results,
# so these are the three that feed the fit job.
FIT_INPUTS = ("series", "games", "game_player_stats")

SOURCE_LOADERS = {
    "cwl_archive": "cwl_reset",
    "cito": "refresh",
    "lpdb": "refresh",
    "codwiki": "codwiki_load",
}
SOURCE_LABELS = {
    "cwl_archive": "CWL archive",
    "cito": "Cito",
    "lpdb": "Liquipedia",
    "codwiki": "CoD Esports Wiki",
}
FIT_JOB = "run_all"

WEB_DIR = REPO_ROOT / "web"
SNAPSHOTS_SUBDIR = {
    "cito": "cito",
    "lpdb": "lpdb",
    "cwl_archive": "cwl_archive",
    "codwiki": "codwiki",
}


def _node(node_id: str, kind: str, label: str, **fields: Any) -> dict[str, Any]:
    return {"id": node_id, "kind": kind, "label": label, **fields}


def _edge(tail: str, head: str, **fields: Any) -> dict[str, Any]:
    return {"from": tail, "to": head, **fields}


def _counts(conn: Conn, tables: tuple[str, ...]) -> dict[str, int]:
    if not tables:
        return {}
    union = " UNION ALL ".join(f"SELECT '{t}' AS name, count(*) AS rows FROM {t}" for t in tables)  # noqa: S608
    return {cast(str, r["name"]): cast(int, r["rows"]) for r in rows_as_dicts(conn.execute(union))}


def _rows_by_source(conn: Conn) -> dict[str, dict[str, int]]:
    """Rows per (table, data_source), which is what a load leaves behind."""
    union = " UNION ALL ".join(
        f"SELECT '{t}' AS name, data_source, count(*) AS rows FROM {t} GROUP BY data_source"  # noqa: S608
        for t in SOURCED_TABLES
    )
    out: dict[str, dict[str, int]] = {}
    for row in rows_as_dicts(conn.execute(union)):
        out.setdefault(cast(str, row["data_source"]), {})[cast(str, row["name"])] = cast(
            int, row["rows"]
        )
    return out


def _source_dates(conn: Conn) -> dict[str, dict[str, Any]]:
    return {
        cast(str, row.pop("data_source")): row
        for row in rows_as_dicts(
            conn.execute(
                "SELECT data_source, min(played_at)::date AS first_date, "
                "max(played_at)::date AS last_date FROM series GROUP BY data_source"
            )
        )
    }


def _output_rows_by_model(conn: Conn) -> dict[str, dict[str, int]]:
    """Rows each model owns in each output table, through its runs."""
    out: dict[str, dict[str, int]] = {}
    for table in OUTPUT_TABLES:
        sql = (
            f"SELECT r.model, count(*) AS rows FROM {table} t "  # noqa: S608
            "JOIN model_runs r ON r.id = t.run_id GROUP BY r.model"
        )
        for row in rows_as_dicts(conn.execute(sql)):
            out.setdefault(cast(str, row["model"]), {})[table] = cast(int, row["rows"])
    return out


def _models(conn: Conn) -> list[dict[str, Any]]:
    return rows_as_dicts(
        conn.execute(
            "SELECT DISTINCT ON (model) model, version, created_at "
            "FROM model_runs ORDER BY model, created_at DESC, id DESC"
        )
    )


def _snapshot_files(snapshots: Path, source: str) -> int | None:
    directory = snapshots / SNAPSHOTS_SUBDIR.get(source, source)
    if not directory.is_dir():
        return None
    return sum(1 for path in directory.rglob("*") if path.is_file())


def _camel(table: str) -> str:
    head, *rest = table.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _referenced(text: str, tables: tuple[str, ...]) -> set[str]:
    return {table for table in tables if re.search(rf"\b{_camel(table)}\b", text)}


def _route(page: Path) -> str:
    relative = page.parent.relative_to(WEB_DIR / "app")
    return "/" if str(relative) == "." else "/" + str(relative)


_EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:function|const)\s+(\w+)", re.MULTILINE)
_IMPORT = re.compile(r'import\s*(?:type\s*)?\{([^}]*)\}\s*from\s*"@/([^"]+)"', re.DOTALL)


def _exported_symbols(text: str, tables: tuple[str, ...]) -> dict[str, set[str]]:
    """Tables each exported function or constant of a module touches.

    Whole-module attribution is useless here: every page imports something from
    `lib/analytics`, so it would draw every page against every table. What a
    page reads is what the functions it actually imports read.
    """
    marks = list(_EXPORT.finditer(text))
    symbols: dict[str, set[str]] = {}
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        symbols[mark.group(1)] = _referenced(text[mark.start() : end], tables)
    return symbols


def _pages(tables: tuple[str, ...]) -> list[dict[str, Any]]:
    """Each route with the tables it reads, directly or through what it imports."""
    app = WEB_DIR / "app"
    if not app.is_dir():
        return []

    modules: dict[str, dict[str, set[str]]] = {}
    for module in (WEB_DIR / "lib").rglob("*.ts"):
        if module.name.endswith(".test.ts"):
            continue
        key = str(module.relative_to(WEB_DIR).with_suffix(""))
        modules[key] = _exported_symbols(module.read_text(encoding="utf-8"), tables)

    pages: list[dict[str, Any]] = []
    for page in sorted(app.rglob("page.tsx")):
        text = page.read_text(encoding="utf-8")
        used = _referenced(text, tables)
        for names, module in _IMPORT.findall(text):
            symbols = modules.get(module, {})
            for name in names.split(","):
                symbol = name.strip().removeprefix("type ").split(" as ")[0].strip()
                used |= symbols.get(symbol, set())
        pages.append({"route": _route(page), "tables": sorted(used)})
    return pages


def report(conn: Conn, snapshots: Path) -> dict[str, Any]:
    core = SOURCED_TABLES + SHARED_TABLES
    table_rows = _counts(conn, core)
    output_rows = _counts(conn, OUTPUT_TABLES)
    by_source = _rows_by_source(conn)
    dates = _source_dates(conn)
    by_model = _output_rows_by_model(conn)
    models = _models(conn)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    jobs_used = {SOURCE_LOADERS[source] for source in by_source} | {FIT_JOB}
    for job in sorted(jobs_used):
        nodes.append(_node(f"job:{job}", "job", job, job_id=job))

    for source, tables in sorted(by_source.items()):
        node_id = f"source:{source}"
        nodes.append(
            _node(
                node_id,
                "source",
                SOURCE_LABELS.get(source, source),
                source=source,
                rows=tables.get("game_player_stats", 0),
                series=tables.get("series", 0),
                snapshot_files=_snapshot_files(snapshots, source),
                first_date=dates.get(source, {}).get("first_date"),
                last_date=dates.get(source, {}).get("last_date"),
            )
        )
        loader = SOURCE_LOADERS.get(source, FIT_JOB)
        edges.append(_edge(node_id, f"job:{loader}", rows=sum(tables.values())))
        for table, rows in sorted(tables.items()):
            edges.append(_edge(f"job:{loader}", f"table:{table}", rows=rows, source=source))

    for table in core:
        nodes.append(_node(f"table:{table}", "table", table, rows=table_rows.get(table, 0)))
    for table in FIT_INPUTS:
        edges.append(_edge(f"table:{table}", f"job:{FIT_JOB}", rows=table_rows.get(table, 0)))

    for model in models:
        name = cast(str, model["model"])
        nodes.append(
            _node(
                f"model:{name}",
                "model",
                name,
                model=name,
                version=model["version"],
                created_at=model["created_at"],
            )
        )
        edges.append(_edge(f"job:{FIT_JOB}", f"model:{name}"))
        for table, rows in sorted(by_model.get(name, {}).items()):
            edges.append(_edge(f"model:{name}", f"table:{table}", rows=rows))

    for table in OUTPUT_TABLES:
        nodes.append(_node(f"table:{table}", "output", table, rows=output_rows.get(table, 0)))

    known = {node["id"] for node in nodes}
    for page in _pages(SOURCED_TABLES + SHARED_TABLES + OUTPUT_TABLES):
        route = cast(str, page["route"])
        nodes.append(_node(f"page:{route}", "page", route, route=route))
        for table in cast(list[str], page["tables"]):
            if f"table:{table}" in known:
                edges.append(_edge(f"table:{table}", f"page:{route}"))

    return {"nodes": nodes, "edges": edges}
