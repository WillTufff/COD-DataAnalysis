"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { PctlBar } from "@/components/PctlBar";
import { type Column, DataTable } from "@/components/table/DataTable";
import type { Per } from "@/lib/paging";
import type { SortState } from "@/components/table/tableState";
import type { ReportColumn, ReportRow } from "@/lib/analytics";
import type { ReportEntity } from "@/lib/reports/resolve";
import { AddColumnMenu, type MetricOption } from "./AddColumnMenu";
import { type CellMode, ReportToolbar } from "./ReportToolbar";
import { type ModeCatalog, modeLabel } from "./cohortLabel";
import { useReportUrl } from "./reportUrl";

// Mirrors the single-metric table's formatting: shares render as percentages,
// everything else scales its precision to its magnitude.
function formatValue(v: number, unit: string): string {
  if (unit.startsWith("share")) return `${(v * 100).toFixed(1)}%`;
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 10) return v.toFixed(2);
  return v.toFixed(3);
}

/** One metric cell, honouring the display mode and greying below-minimum samples. */
function Cell({
  cell,
  col,
  mode,
}: {
  cell: ReportRow["cells"][string] | undefined;
  col: ReportColumn;
  mode: CellMode;
}) {
  // An absent cell — this column does not cover the row's season/mode.
  if (!cell) return <span className="text-ink-muted">—</span>;

  let body: React.ReactNode;
  if (mode === "pctl") {
    body = cell.pctl !== null ? <PctlBar pctl={cell.pctl} /> : "—";
  } else if (mode === "z") {
    body =
      cell.z !== null ? `${cell.z >= 0 ? "+" : ""}${cell.z.toFixed(2)}σ` : "—";
  } else {
    body = formatValue(cell.value, col.unit);
  }

  // Below its own sample minimum: shown, but greyed and scored against the
  // qualified cohort rather than trusted on its own.
  return (
    <span
      className={cell.qualified ? "" : "text-ink-muted"}
      title={
        cell.qualified
          ? undefined
          : `Below the ${col.minDenom} ${col.denomKind} minimum`
      }
    >
      {body}
    </span>
  );
}

/**
 * The report, with its own column editing built into the header row.
 *
 * The header row *is* the column manager — there is no separate strip repeating
 * what the headers already say. Each metric header carries three hit zones that
 * never overlap: a ✕ to drop the column, a ⠿ grip that is the only thing drag
 * listens to, and the label, which keeps its click-to-sort. Splitting drag onto
 * its own glyph is what lets sort and reorder share one control without either
 * stealing the other's gesture.
 *
 * Add, remove and reorder all rewrite the ordered `metrics` CSV on the URL and
 * navigate, exactly as the old chips row did — the column set is server-queried,
 * so an edit is a new report, and the server re-validates (notably, falling the
 * sort back to a default when the sorted column has just been removed).
 */
export function ReportTable({
  entity,
  columns,
  rows,
  catalog,
  categoryLabels,
  modeCatalog,
  showMode,
  qualifiedOnly,
  initialPer,
  initialPage,
  initialSort,
  defaultSort,
}: {
  entity: ReportEntity;
  columns: ReportColumn[];
  rows: ReportRow[];
  catalog: MetricOption[];
  categoryLabels: Record<string, string>;
  modeCatalog: ModeCatalog;
  showMode: boolean;
  qualifiedOnly: boolean;
  initialPer: Per;
  initialPage: number;
  initialSort: SortState;
  defaultSort: SortState;
}) {
  const push = useReportUrl();
  const [cellMode, setCellMode] = useState<CellMode>("value");
  // The column being dragged, and the in-flight column order it is being
  // dragged through. The preview reorders live under the pointer; the URL is
  // only rewritten on release. Each preview remembers which `selected` it was
  // derived from: it renders instead of the server order only while that
  // identity holds — covering the gap between commit and navigation so the
  // columns don't snap back — and goes inert by itself once new columns land.
  const [dragKey, setDragKey] = useState<string | null>(null);
  const [preview, setPreview] = useState<{
    base: string[];
    order: string[];
  } | null>(null);
  const orderRef = useRef<string[] | null>(null);

  const selected = useMemo(() => columns.map((c) => c.key), [columns]);
  const byKey = useMemo(
    () => new Map(columns.map((c) => [c.key, c])),
    [columns],
  );
  const order = preview && preview.base === selected ? preview.order : selected;

  const setColumns = useCallback(
    (next: string[]) => {
      // Hand-edited columns are no longer a preset; `sort` deliberately stays,
      // for the server to keep or fall back from.
      push({
        metrics: next.length > 0 ? next.join(",") : null,
        preset: null,
      });
    },
    [push],
  );

  const addColumn = useCallback(
    (key: string) => {
      if (selected.includes(key)) return;
      setColumns([...selected, key]);
    },
    [selected, setColumns],
  );

  const removeColumn = useCallback(
    (key: string) => {
      const next = selected.filter((k) => k !== key);
      // The server would fall back on its own, but leaving a dead `sort=` on
      // the URL makes the link claim a column it no longer has. Dropping the
      // sort here is not guessing — removing the ranked column is what made it
      // stale.
      const staleSort = initialSort?.id === key;
      push({
        metrics: next.length > 0 ? next.join(",") : null,
        preset: null,
        ...(staleSort ? { sort: null, dir: null } : {}),
      });
    },
    [selected, push, initialSort],
  );

  /** Lift a column out of the order and drop it back at `to`. */
  const moveColumn = useCallback(
    (key: string, to: number) => {
      const from = selected.indexOf(key);
      if (from < 0 || to < 0 || to >= selected.length || to === from) return;
      const next = [...selected];
      next.splice(from, 1);
      next.splice(to, 0, key);
      setColumns(next);
    },
    [selected, setColumns],
  );

  const endDrag = useCallback(() => {
    const dropped = orderRef.current;
    setDragKey(null);
    if (dropped && dropped.join(",") !== selected.join(",")) {
      // Commit, but leave the preview rendered: clearing it now would snap the
      // columns back to the old order until the navigation lands.
      setColumns(dropped);
    } else {
      setPreview(null);
      orderRef.current = null;
    }
  }, [selected, setColumns]);

  // The drag listens on the window, not the grip. Pointer capture would be the
  // idiomatic choice, but the first live reorder moves the grip's DOM node —
  // React reinserts the reordered header cells — and reinsertion silently
  // cancels capture, killing the drag one swap in. Window listeners survive
  // any amount of header churn under the pointer.
  useEffect(() => {
    if (dragKey === null) return;
    const onMove = (e: PointerEvent) => {
      const cur = orderRef.current;
      if (cur === null) return;
      // Hit-test the header cells rather than tracking offsets. Crossing
      // another metric header reorders the preview immediately — the column
      // follows the pointer instead of waiting for the drop.
      const under = document
        .elementFromPoint(e.clientX, e.clientY)
        ?.closest<HTMLElement>("th[data-col-id]");
      const id = under?.dataset.colId;
      if (!id || id === dragKey || !cur.includes(id)) return;
      const from = cur.indexOf(dragKey);
      const to = cur.indexOf(id);
      if (from === to) return;
      const next = [...cur];
      next.splice(from, 1);
      next.splice(to, 0, dragKey);
      orderRef.current = next;
      setPreview({ base: selected, order: next });
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
    };
  }, [dragKey, selected, endDrag]);

  const tableColumns = useMemo<Column<ReportRow>[]>(() => {
    const cols: Column<ReportRow>[] = [
      {
        // The id stays "player" for both entities — it is the URL's name for
        // "the name column" (`?sort=player`), and links must keep meaning what
        // they said when they were copied.
        id: "player",
        header: entity === "teams" ? "Team" : "Player",
        sortable: true,
        sortDir: "asc",
        sortValue: (r) => r.handle,
        render: (r) => (
          <Link
            href={`/${entity === "teams" ? "teams" : "players"}/${r.slug}`}
            className="font-medium hover:text-accent"
          >
            {r.handle}
          </Link>
        ),
      },
      {
        id: "season",
        header: "Season",
        cellClassName: "whitespace-nowrap text-ink-secondary",
        render: (r) => `${r.year} ${r.title}`,
      },
    ];
    if (showMode) {
      cols.push({
        id: "mode",
        header: "Mode",
        cellClassName: "text-ink-secondary",
        render: (r) => modeLabel(modeCatalog, r.mode, "All"),
      });
    }
    for (const key of order) {
      const col = byKey.get(key);
      if (!col) continue;
      const index = order.indexOf(key);
      const dragging = dragKey === col.key;
      cols.push({
        id: col.key,
        header: (
          <span title={col.higherIsBetter ? undefined : "Lower is better"}>
            {col.label}
            {col.higherIsBetter ? "" : " ↓"}
          </span>
        ),
        align: "right",
        cellClassName: "font-mono tabular-nums",
        headerClassName: `rh-col ${dragging ? "opacity-50" : ""}`,
        headerPrefix: (
          <span className="inline-flex items-center print:hidden">
            <button
              type="button"
              aria-label={`Remove the ${col.label} column`}
              title={`Remove the ${col.label} column`}
              onClick={() => removeColumn(col.key)}
              className="rh-x px-0.5 text-[0.68rem] leading-none"
            >
              ✕
            </button>
            <span
              role="button"
              tabIndex={0}
              aria-label={`Reorder the ${col.label} column, position ${index + 1} of ${selected.length}. Use the arrow keys to move it.`}
              data-dragging={dragging ? "true" : undefined}
              onPointerDown={(e) => {
                if (e.button !== 0) return;
                e.preventDefault();
                setDragKey(col.key);
                orderRef.current = order;
                setPreview({ base: selected, order });
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft") {
                  e.preventDefault();
                  moveColumn(col.key, index - 1);
                } else if (e.key === "ArrowRight") {
                  e.preventDefault();
                  moveColumn(col.key, index + 1);
                }
              }}
              className="rh-grip inline-block w-4 touch-none select-none text-center text-[0.66rem] leading-none tracking-tighter"
            >
              ⠿
            </span>
          </span>
        ),
        sortable: true,
        // Best-first on first click: descending when higher is better.
        sortDir: col.higherIsBetter ? "desc" : "asc",
        sortValue: (r) => r.cells[col.key]?.value ?? null,
        render: (r) => <Cell cell={r.cells[col.key]} col={col} mode={cellMode} />,
      });
    }
    return cols;
  }, [
    byKey,
    entity,
    modeCatalog,
    order,
    selected,
    showMode,
    cellMode,
    dragKey,
    moveColumn,
    removeColumn,
  ]);

  return (
    <div className="mt-4">
      <ReportToolbar
        rowCount={rows.length}
        columnCount={columns.length}
        qualifiedOnly={qualifiedOnly}
        cellMode={cellMode}
        setCellMode={setCellMode}
      />
      <p className="mb-1 mt-2 text-right font-mono text-[0.66rem] text-ink-muted print:hidden">
        <span className="tracking-tighter">⠿</span> drag to reorder · click to
        sort · ✕ remove · + add metric
      </p>
      <DataTable
        // The table owns its sort as client state, seeded once from the URL. A
        // column edit is a navigation, and the server may have moved the sort
        // underneath it — most obviously by falling back when the sorted column
        // was the one just removed — so re-seed whenever the resolved sort
        // differs from what this table was mounted with.
        key={initialSort ? `${initialSort.id}:${initialSort.dir}` : "unsorted"}
        rows={rows}
        columns={tableColumns}
        rowKey={(r) => `${r.playerId}-${r.year}-${r.mode ?? "all"}`}
        rank
        initialPer={initialPer}
        initialPage={initialPage}
        initialSort={initialSort}
        defaultSort={defaultSort}
        headerRowClassName="rh-row"
        trailingHeaderClassName="relative w-9 text-center print:hidden"
        trailingHeader={
          <AddColumnMenu
            catalog={catalog}
            selected={selected}
            categoryLabels={categoryLabels}
            onAdd={addColumn}
          />
        }
      />
    </div>
  );
}
