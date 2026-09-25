"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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

/** How long a displaced or dropped column takes to slide into place. */
const SLIDE_MS = 180;
const SLIDE = `transform ${SLIDE_MS}ms cubic-bezier(0.2, 0.8, 0.2, 1)`;

/** Where a drag started, in the table's own coordinates. */
type DragOrigin = {
  key: string;
  startX: number;
  startLeft: number;
  scroller: HTMLElement | null;
  startScroll: number;
};

function clearCellStyle(el: HTMLElement) {
  el.style.transform = "";
  el.style.transition = "";
  el.style.position = "";
  el.style.zIndex = "";
  el.style.background = "";
  el.style.boxShadow = "";
}

/**
 * The report, with its own column editing built into the header row.
 *
 * A click on a metric header sorts it. A mouse press that travels past a few
 * pixels drags the column instead, and the click that ends the drag is
 * swallowed so it does not also sort. The info icon beside each label opens the column
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

  const tableRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragOrigin | null>(null);
  const pointerXRef = useRef(0);
  // Each column's on-screen left before a reorder, for the slide that follows.
  const flipRef = useRef<Map<string, number> | null>(null);

  const headerOf = useCallback(
    (key: string) =>
      tableRef.current?.querySelector<HTMLElement>(
        `th[data-col-id="${CSS.escape(key)}"]`,
      ) ?? null,
    [],
  );
  const cellsOf = useCallback(
    (key: string) => [
      ...(tableRef.current?.querySelectorAll<HTMLElement>(
        `[data-col-id="${CSS.escape(key)}"]`,
      ) ?? []),
    ],
    [],
  );

  // The dragged column follows the pointer, clamped to the metric columns.
  // When its leading edge crosses a neighbour's centre the two swap, and the
  // neighbour slides over in the layout effect below.
  const applyDrag = useCallback(
    (clientX: number) => {
      const d = dragRef.current;
      const cur = orderRef.current;
      if (!d || !cur) return;
      pointerXRef.current = clientX;
      const th = headerOf(d.key);
      const first = headerOf(cur[0]);
      const last = headerOf(cur[cur.length - 1]);
      if (!th || !first || !last) return;
      const scrolled = d.scroller ? d.scroller.scrollLeft - d.startScroll : 0;
      const pos = Math.min(
        Math.max(d.startLeft + clientX - d.startX + scrolled, first.offsetLeft),
        last.offsetLeft + last.offsetWidth - th.offsetWidth,
      );
      for (const el of cellsOf(d.key)) {
        el.style.transition = "none";
        el.style.transform = `translateX(${pos - th.offsetLeft}px)`;
        el.style.position = "relative";
        el.style.zIndex = "2";
        el.style.background = "var(--color-surface)";
        el.style.boxShadow =
          "inset 1px 0 var(--color-hairline), inset -1px 0 var(--color-hairline)";
      }

      const i = cur.indexOf(d.key);
      const left = i > 0 ? headerOf(cur[i - 1]) : null;
      const right = i < cur.length - 1 ? headerOf(cur[i + 1]) : null;
      let to = i;
      if (left && pos < left.offsetLeft + left.offsetWidth / 2) to = i - 1;
      else if (
        right &&
        pos + th.offsetWidth > right.offsetLeft + right.offsetWidth / 2
      )
        to = i + 1;
      if (to === i) return;

      const before = new Map<string, number>();
      for (const k of cur) {
        const h = headerOf(k);
        if (h) before.set(k, h.getBoundingClientRect().left);
      }
      flipRef.current = before;
      const next = [...cur];
      next.splice(i, 1);
      next.splice(to, 0, d.key);
      orderRef.current = next;
      setPreview({ base: selected, order: next });
    },
    [headerOf, cellsOf, selected],
  );

  // After a swap: each displaced column starts where it was drawn and slides
  // to its new place. Measured from the drawn position, so a swap that lands
  // mid-slide carries on from there.
  useLayoutEffect(() => {
    const before = flipRef.current;
    if (!before) return;
    flipRef.current = null;
    for (const [key, x] of before) {
      if (key === dragRef.current?.key) continue;
      const th = headerOf(key);
      if (!th) continue;
      const cells = cellsOf(key);
      for (const el of cells) {
        el.style.transition = "none";
        el.style.transform = "";
      }
      const dx = x - th.getBoundingClientRect().left;
      if (dx === 0) continue;
      for (const el of cells) el.style.transform = `translateX(${dx}px)`;
      void th.offsetWidth;
      for (const el of cells) {
        el.style.transition = SLIDE;
        el.style.transform = "";
      }
    }
    if (dragRef.current) applyDrag(pointerXRef.current);
  }, [order, headerOf, cellsOf, applyDrag]);

  const endDrag = useCallback(() => {
    const dropped = orderRef.current;
    const d = dragRef.current;
    dragRef.current = null;
    setDragKey(null);
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    // The dragged column settles into its slot, then drops its lifted look.
    if (d) {
      const cells = cellsOf(d.key);
      for (const el of cells) {
        el.style.transition = SLIDE;
        el.style.transform = "";
      }
      setTimeout(() => cells.forEach(clearCellStyle), SLIDE_MS + 20);
    }
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
  }, [selected, setColumns, cellsOf]);

  // The drag listens on the window, not the grip: the first reorder moves the
  // header's DOM node, and reinsertion cancels pointer capture.
  useEffect(() => {
    if (dragKey === null) return;
    const onMove = (e: PointerEvent) => applyDrag(e.clientX);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", endDrag);
      window.removeEventListener("pointercancel", endDrag);
    };
  }, [dragKey, applyDrag, endDrag]);

  // A mouse press on a metric header waits to see whether it is a click (sort)
  // or a drag (reorder). Touch never drags here: a horizontal swipe on the
  // header has to keep scrolling the table, and the column menu covers moves.
  const onHeaderPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.pointerType !== "mouse" || e.button !== 0) return;
      const target = e.target as HTMLElement;
      if (target.closest("[data-col-menu]")) return;
      const th = target.closest<HTMLElement>("th[data-col-id]");
      const key = th?.dataset.colId;
      if (!th || !key || !selected.includes(key)) return;
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
        const scroller = th.closest<HTMLElement>(".overflow-x-auto");
        dragRef.current = {
          key,
          startX: x0,
          startLeft: th.offsetLeft,
          scroller,
          startScroll: scroller?.scrollLeft ?? 0,
        };
        orderRef.current = order;
        document.body.style.cursor = "grabbing";
        document.body.style.userSelect = "none";
        setPreview({ base: selected, order });
        setDragKey(key);
        applyDrag(ev.clientX);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", cleanup);
    },
    [order, selected, applyDrag],
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
      cols.push({
        id: col.key,
        header: (
          <span
            title={definitionLine(col)}
            className={col.unavailable ? "text-ink-muted" : ""}
          >
            {col.label}
          </span>
        ),
        align: "right",
        cellClassName: "font-mono tabular-nums",
        headerClassName: `group/th select-none ${dragKey ? "" : "cursor-grab"}`,
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
        headerEnd: (
          <button
            type="button"
            data-col-menu
            aria-label={`Remove ${col.label}`}
            title="Remove column"
            onClick={() => removeColumn(col.key)}
            className={`ml-1 hidden items-center text-ink-muted opacity-0 transition-opacity hover:text-accent focus-visible:opacity-100 motion-reduce:transition-none print:hidden [@media(hover:hover)]:flex ${
              dragKey ? "" : "group-hover/th:opacity-100"
            }`}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 16 16"
              className="h-3 w-3"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            >
              <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" />
            </svg>
          </button>
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
      />
      <div ref={tableRef} onPointerDownCapture={onHeaderPointerDown}>
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
