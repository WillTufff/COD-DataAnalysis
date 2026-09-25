// The report's row gate and sort, as pure functions. The server runs them for
// the export and the client runs them for the on-screen table, so both read the
// same rows in the same order for the same URL.

import type { ReportRow } from "@/lib/analytics";
import { type SearchParams, one } from "@/lib/paging";

/** Which field of a cell the table shows, and so which one it sorts on. */
export type ReportView = "value" | "pctl" | "z";

export const REPORT_VIEWS: { id: ReportView; label: string }[] = [
  { id: "value", label: "Value" },
  { id: "pctl", label: "Percentile" },
  { id: "z", label: "z-score" },
];

/** The view a URL without `?view=` shows. */
export const DEFAULT_VIEW: ReportView = "pctl";

/** `?view=value|z`; anything else, including absent, is the percentile. */
export function parseView(sp: SearchParams): ReportView {
  const raw = one(sp, "view");
  return raw === "value" || raw === "z" ? raw : DEFAULT_VIEW;
}

/** The number a row sorts on for a column in a view, or null when absent. */
export function sortReading(
  row: ReportRow,
  key: string,
  view: ReportView,
): number | null {
  const cell = row.cells[key];
  if (!cell) return null;
  return view === "value" ? cell.value : cell[view];
}

/**
 * "Qualified only": a row is ranked on a column only when its cell in that
 * column cleared the sample minimum. With no metric sort there is nothing to
 * gate on. `active` is false when the flag is off or an explicit player or
 * team filter is set, since asking for a player means seeing that player.
 */
export function gateReportRows(
  rows: ReportRow[],
  sort: string,
  keys: string[],
  active: boolean,
): ReportRow[] {
  if (!active || !keys.includes(sort)) return rows;
  return rows.filter((row) => row.cells[sort]?.qualified === true);
}

/**
 * Sort on a column in a view. Absent readings sink to the bottom in either
 * direction, and the name breaks ties so paging is stable.
 */
export function sortReportRows(
  rows: ReportRow[],
  sort: string,
  dir: "asc" | "desc",
  view: ReportView,
  keys: string[],
): ReportRow[] {
  const sortIsMetric = keys.includes(sort);
  const factor = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (!sortIsMetric) return factor * a.handle.localeCompare(b.handle);
    const av = sortReading(a, sort, view);
    const bv = sortReading(b, sort, view);
    if (av === null && bv === null) return a.handle.localeCompare(b.handle);
    if (av === null) return 1;
    if (bv === null) return -1;
    if (av === bv) return a.handle.localeCompare(b.handle);
    return factor * (av - bv);
  });
}
