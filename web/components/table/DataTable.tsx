"use client";

import { useMemo, type ReactNode } from "react";
import type { Per } from "@/lib/paging";
import { TableControls } from "@/components/table/TableControls";
import {
  type SortSpec,
  type SortState,
  useTableState,
} from "@/components/table/tableState";

export type Column<T> = {
  id: string;
  header: ReactNode;
  align?: "left" | "right";
  headerClassName?: string;
  // Chrome rendered inside the header cell after the sort button, so a table
  // can hang a per-column menu off its header without it stealing the click
  // that sorts. It receives the table's sort so the menu can set it.
  headerMenu?: (api: HeaderApi) => ReactNode;
  // Chrome rendered after the sort button, such as a remove control.
  headerEnd?: ReactNode;
  cellClassName?: string;
  // A sortable column supplies the value the sort reads and the direction it
  // starts in. Omit for a static column (links, sparklines, bars).
  sortable?: boolean;
  sortDir?: "asc" | "desc";
  sortValue?: (row: T) => number | string | null;
  render: (row: T, absoluteIndex: number) => ReactNode;
};

export type HeaderApi = {
  sort: SortState;
  setSort: (sort: { id: string; dir: "asc" | "desc" }) => void;
};

/**
 * A client table that owns its own paging and sort. It renders the same markup
 * the server-rendered tables used — the only change a reader sees is that
 * paging and sorting happen instantly, without a page reload.
 */
export function DataTable<T>({
  rows,
  columns,
  rowKey,
  rank = false,
  rowClassName,
  unit = "rows",
  defaultPer,
  initialPer,
  initialPage,
  initialSort = null,
  defaultSort = null,
  syncUrl = true,
  onSortChange,
  headerRowClassName,
  trailingHeader,
  trailingHeaderClassName,
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T, index: number) => string;
  rank?: boolean;
  rowClassName?: (row: T) => string;
  unit?: string;
  defaultPer?: Per;
  initialPer?: Per;
  initialPage?: number;
  initialSort?: SortState;
  defaultSort?: SortState;
  syncUrl?: boolean;
  /** Called after a header click changes the sort. */
  onSortChange?: (sort: SortState) => void;
  /** Extra classes on the header `<tr>` — the hook a table needs to drive
   *  row-wide hover states across its header cells. */
  headerRowClassName?: string;
  /** A final header cell after every column, with an empty body cell per row.
   *  The report builder puts its "add a column" affordance here, where the next
   *  column would appear. */
  trailingHeader?: ReactNode;
  trailingHeaderClassName?: string;
}) {
  const sortSpecs = useMemo(() => {
    const specs: Record<string, SortSpec<T>> = {};
    for (const c of columns) {
      if (c.sortable && c.sortValue) {
        specs[c.id] = { value: c.sortValue, dir: c.sortDir ?? "asc" };
      }
    }
    return specs;
  }, [columns]);

  const state = useTableState<T>({
    rows,
    defaultPer,
    initialPer,
    initialPage,
    initialSort,
    defaultSort,
    sortSpecs,
    syncUrl,
    onSortChange,
  });

  return (
    <div>
      <TableControls
        per={state.per}
        setPer={state.setPer}
        page={state.page}
        setPage={state.setPage}
        pageCount={state.pageCount}
        total={state.total}
        offset={state.offset}
        visibleCount={state.visible.length}
        unit={unit}
      />
      <div className="overflow-x-auto">
        {/* Positioned, so every cell's offsetLeft is measured from the table
            whether or not the cell itself is positioned. */}
        <table className="relative w-full text-left text-sm">
          <thead>
            <tr
              className={`border-b border-hairline text-xs text-ink-muted ${headerRowClassName ?? ""}`}
            >
              {rank && <th className="py-2 pr-3 font-normal">#</th>}
              {columns.map((c) => {
                const active = state.sort?.id === c.id;
                const alignCls = c.align === "right" ? "text-right" : "";
                if (c.sortable && c.sortValue) {
                  const sortButton = (
                    <button
                      type="button"
                      onClick={() => state.toggleSort(c.id)}
                      className={active ? "text-ink hover:text-accent" : "hover:text-ink"}
                    >
                      {c.header}
                      <span aria-hidden="true" className="ml-1 text-accent">
                        {active ? (state.sort?.dir === "asc" ? "▲" : "▼") : ""}
                      </span>
                    </button>
                  );
                  return (
                    <th
                      key={c.id}
                      data-col-id={c.id}
                      className={`py-2 pr-4 font-normal ${alignCls} ${c.headerClassName ?? ""}`}
                      aria-sort={
                        active
                          ? state.sort?.dir === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                      }
                    >
                      {c.headerMenu || c.headerEnd ? (
                        // One inline-flex line, so the menu button can never
                        // wrap away from the label in a narrow cell.
                        <span className="inline-flex items-center whitespace-nowrap">
                          {c.headerMenu?.({ sort: state.sort, setSort: state.setSort })}
                          {sortButton}
                          {c.headerEnd}
                        </span>
                      ) : (
                        sortButton
                      )}
                    </th>
                  );
                }
                return (
                  <th
                    key={c.id}
                    data-col-id={c.id}
                    className={`py-2 pr-4 font-normal ${alignCls} ${c.headerClassName ?? ""}`}
                  >
                    {c.header}
                  </th>
                );
              })}
              {trailingHeader !== undefined && (
                <th
                  className={`py-2 font-normal ${trailingHeaderClassName ?? ""}`}
                >
                  {trailingHeader}
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {state.visible.map((row, i) => {
              const absIndex = state.offset + i;
              return (
                <tr
                  key={rowKey(row, absIndex)}
                  className={`border-b border-hairline/60 ${rowClassName?.(row) ?? ""}`}
                >
                  {rank && (
                    <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                      {absIndex + 1}
                    </td>
                  )}
                  {columns.map((c) => (
                    <td
                      key={c.id}
                      data-col-id={c.id}
                      className={`py-1.5 pr-4 ${c.align === "right" ? "text-right" : ""} ${c.cellClassName ?? ""}`}
                    >
                      {c.render(row, absIndex)}
                    </td>
                  ))}
                  {trailingHeader !== undefined && <td aria-hidden="true" />}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
