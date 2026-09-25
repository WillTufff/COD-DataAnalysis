"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { PctlBar } from "@/components/PctlBar";
import { type Column, DataTable } from "@/components/table/DataTable";
import type { Per } from "@/lib/paging";
import type { SortState } from "@/components/table/tableState";
import type { ReportColumn, ReportRow } from "@/lib/analytics";
import type { ReportEntity } from "@/lib/reports/resolve";
import {
  DEFAULT_VIEW,
  type ReportView,
  type ResultFilters,
  applyResultFilters,
  sortReading,
} from "@/lib/reports/rows";
import { AddColumnMenu, type MetricOption } from "./AddColumnMenu";
import { ColumnMenu, definitionLine } from "./ColumnMenu";
import { formatValue, formatZ } from "./format";
import { ReportToolbar } from "./ReportToolbar";
import { useReportUrl } from "./reportUrl";

/** One metric cell, honouring the display mode and greying below-minimum samples. */
function Cell({
  cell,
  col,
  view,
}: {
  cell: ReportRow["cells"][string] | undefined;
  col: ReportColumn;
  view: ReportView;
}) {
  // An absent cell — this column does not cover the row's season/mode.
  if (!cell) return <span className="text-ink-muted">—</span>;

  let body: React.ReactNode;
  if (view === "pctl") {
    body = cell.pctl !== null ? <PctlBar pctl={cell.pctl} /> : "—";
  } else if (view === "z") {
    body = cell.z !== null ? formatZ(cell.z) : "—";
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

/** How far a mouse has to travel on a header before a press becomes a drag. */
const DRAG_THRESHOLD = 6;

/**
 * The report, with its own column editing built into the header row.
 *
 * A click on a metric header sorts it. A mouse press that travels past a few
 * pixels drags the column instead, and the click that ends the drag is
 * swallowed so it does not also sort. The ▾ beside each label opens the column
 * menu, which holds the same edits for touch and keyboard.
 *
 * Add, remove and reorder all rewrite the ordered `metrics` CSV on the URL and
 * navigate. The column set is server-queried, so an edit is a new report, and
 * the server re-validates (notably, falling the sort back to a default when the
 * sorted column has just been removed).
 */
export function ReportTable({
  entity,
  columns,
  rows,
  catalog,
  filters,
  aggregated,
  span,
  initialView,
  initialPer,
  initialPage,
  initialSort,
  defaultSort,
}: {
  entity: ReportEntity;
  columns: ReportColumn[];
  rows: ReportRow[];
  catalog: MetricOption[];
  filters: ResultFilters;
  /** The numbers were re-aggregated from maps and scored within this pick. */
  aggregated: boolean;
  span: boolean;
  initialView: ReportView;
  initialPer: Per;
  initialPage: number;
  initialSort: SortState;
  defaultSort: SortState;
}) {
  const push = useReportUrl();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [view, setViewState] = useState<ReportView>(initialView);

  // The view only changes how cells read and sort, so it rewrites the URL in
  // place rather than asking the server for the same rows again.
  const setView = useCallback(
    (next: ReportView) => {
      setViewState(next);
      const params = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_VIEW) params.delete("view");
      else params.set("view", next);
      params.sort();
      const qs = params.toString();
      window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname);
    },
    [pathname, searchParams],
  );

  // The sort the table is showing. A header click re-sorts on the client, and
  // the result filters are re-applied under it here (top N follows the sort),
  // so the rows on screen are the rows the same URL gives on a reload or an
  // export. A new server sort (after a column
  // edit) replaces a client one.
  const serverSortKey = initialSort ? `${initialSort.id}:${initialSort.dir}` : "";
  const [clientSort, setClientSort] = useState<{
    seed: string;
    sort: SortState;
  } | null>(null);
  const activeSort =
    clientSort && clientSort.seed === serverSortKey ? clientSort.sort : initialSort;
  const onSortChange = useCallback(
    (sort: SortState) => setClientSort({ seed: serverSortKey, sort }),
    [serverSortKey],
  );
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
  const swallowRef = useRef<((e: MouseEvent) => void) | null>(null);

  const selected = useMemo(() => columns.map((c) => c.key), [columns]);
  const shownRows = useMemo(
    () =>
      applyResultFilters(
        rows,
        filters,
        activeSort?.id ?? "player",
        activeSort?.dir ?? "asc",
        view,
        selected,
      ),
    [rows, filters, activeSort, view, selected],
  );
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
      const staleSort = activeSort?.id === key;
      push({
        metrics: next.length > 0 ? next.join(",") : null,
        preset: null,
        ...(staleSort ? { sort: null, dir: null } : {}),
      });
    },
    [selected, push, activeSort],
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
    // The swallow is for the click this release produces, which fires before
    // any timer; if the release lands off the header no click comes, and the
    // listener must not linger to eat the next real one.
    const swallow = swallowRef.current;
    if (swallow) {
      setTimeout(() => window.removeEventListener("click", swallow, true), 0);
      swallowRef.current = null;
    }
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

  // A mouse press on a metric header waits to see whether it is a click (sort)
  // or a drag (reorder). Touch never drags here: a horizontal swipe on the
  // header has to keep scrolling the table, and the column menu covers moves.
  const onHeaderPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.pointerType !== "mouse" || e.button !== 0) return;
      const target = e.target as HTMLElement;
      if (target.closest("[data-col-menu]")) return;
      const key = target.closest<HTMLElement>("th[data-col-id]")?.dataset.colId;
      if (!key || !selected.includes(key)) return;
      const x0 = e.clientX;
      const y0 = e.clientY;
      const cleanup = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", cleanup);
      };
      const onMove = (ev: PointerEvent) => {
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < DRAG_THRESHOLD) return;
        cleanup();
        const swallow = (c: MouseEvent) => {
          c.stopPropagation();
          c.preventDefault();
        };
        swallowRef.current = swallow;
        window.addEventListener("click", swallow, { capture: true, once: true });
        orderRef.current = order;
        setPreview({ base: selected, order });
        setDragKey(key);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", cleanup);
    },
    [order, selected],
  );

  const tableColumns = useMemo<Column<ReportRow>[]>(() => {
    const cols: Column<ReportRow>[] = [
      {
        // The id stays "player" for both entities — it is the URL's name for
        // "the name column" (`?sort=player`), and links must keep meaning what
        // they said when they were copied.
        id: "player",
        header: entity === "teams" ? "Team" : "Player",
        // Pinned, so the name stays beside its numbers when the table scrolls
        // sideways on a narrow screen.
        headerClassName: "sticky left-0 z-[1] bg-background",
        cellClassName: "sticky left-0 z-[1] bg-background",
        sortable: true,
        sortDir: "asc",
        sortValue: (r) => r.handle,
        render: (r) => (
          <Link
            href={`/${entity === "teams" ? "teams" : "players"}/${r.slug}`}
            className="whitespace-nowrap font-medium hover:text-accent"
          >
            {r.handle}
          </Link>
        ),
      },
      {
        id: "season",
        header: "Season",
        cellClassName: "whitespace-nowrap text-ink-secondary",
        render: (r) =>
          r.seasonLabel ? (
            <span className="font-mono tabular-nums">{r.seasonLabel}</span>
          ) : (
            <>
              <span className="sm:hidden">{r.title}</span>
              <span className="max-sm:hidden">
                {r.year} {r.title}
              </span>
            </>
          ),
      },
    ];
    for (const key of order) {
      const col = byKey.get(key);
      if (!col) continue;
      const index = order.indexOf(key);
      const dragging = dragKey === col.key;
      cols.push({
        id: col.key,
        header: (
          <span
            title={definitionLine(col)}
            className={col.unavailable ? "text-ink-muted" : ""}
          >
            {col.label}
            {col.higherIsBetter ? "" : (
              <span className="ml-0.5 text-ink-secondary" aria-hidden="true">
                ↓
              </span>
            )}
          </span>
        ),
        align: "right",
        cellClassName: "font-mono tabular-nums",
        headerClassName: `group/th select-none ${dragging ? "opacity-50" : ""} ${
          dragKey ? "cursor-grabbing" : ""
        }`,
        headerMenu: (api) => (
          <ColumnMenu
            col={col}
            index={index}
            count={order.length}
            api={api}
            onMove={(to) => moveColumn(col.key, to)}
            onRemove={() => removeColumn(col.key)}
          />
        ),
        sortable: true,
        // Best-first on first click: descending when higher is better.
        sortDir: col.higherIsBetter ? "desc" : "asc",
        sortValue: (r) => sortReading(r, col.key, view),
        render: (r) => <Cell cell={r.cells[col.key]} col={col} view={view} />,
      });
    }
    return cols;
  }, [
    byKey,
    entity,
    order,
    view,
    dragKey,
    moveColumn,
    removeColumn,
  ]);

  return (
    <div className="mt-4">
      <ReportToolbar
        rowCount={shownRows.length}
        columnCount={columns.length}
        view={view}
        setView={setView}
        aggregated={aggregated}
        span={span}
        setSpan={(on) => push({ rows: on ? "span" : null })}
        lowerIsBetter={columns.some((c) => !c.higherIsBetter)}
      />
      <div onPointerDownCapture={onHeaderPointerDown}>
      <DataTable
        // The table owns its sort as client state, seeded once from the URL. A
        // column edit is a navigation, and the server may have moved the sort
        // underneath it — most obviously by falling back when the sorted column
        // was the one just removed — so re-seed whenever the resolved sort
        // differs from what this table was mounted with.
        key={initialSort ? `${initialSort.id}:${initialSort.dir}` : "unsorted"}
        rows={shownRows}
        columns={tableColumns}
        onSortChange={onSortChange}
        rowKey={(r) => `${r.playerId}-${r.year}-${r.mode ?? "all"}`}
        rank
        initialPer={initialPer}
        initialPage={initialPage}
        initialSort={initialSort}
        defaultSort={defaultSort}
        trailingHeaderClassName="relative w-9 text-center print:hidden"
        trailingHeader={
          <AddColumnMenu
            catalog={catalog}
            selected={selected}
            onAdd={addColumn}
          />
        }
      />
      </div>
    </div>
  );
}
