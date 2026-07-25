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
  cellClassName?: string;
  // A sortable column supplies the value the sort reads and the direction it
  // starts in. Omit for a static column (links, sparklines, bars).
  sortable?: boolean;
  sortDir?: "asc" | "desc";
  sortValue?: (row: T) => number | string | null;
  render: (row: T, absoluteIndex: number) => ReactNode;
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
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-hairline text-xs text-ink-muted">
              {rank && <th className="py-2 pr-3 font-normal">#</th>}
              {columns.map((c) => {
                const active = state.sort?.id === c.id;
                const alignCls = c.align === "right" ? "text-right" : "";
                if (c.sortable && c.sortValue) {
                  return (
                    <th
                      key={c.id}
                      className={`py-2 pr-4 font-normal ${alignCls} ${c.headerClassName ?? ""}`}
                      aria-sort={
                        active
                          ? state.sort?.dir === "asc"
                            ? "ascending"
                            : "descending"
                          : "none"
                      }
                    >
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
                    </th>
                  );
                }
                return (
                  <th
                    key={c.id}
                    className={`py-2 pr-4 font-normal ${alignCls} ${c.headerClassName ?? ""}`}
                  >
                    {c.header}
                  </th>
                );
              })}
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
                      className={`py-1.5 pr-4 ${c.align === "right" ? "text-right" : ""} ${c.cellClassName ?? ""}`}
                    >
                      {c.render(row, absIndex)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
