// The report's result filters and sort, as pure functions. The server runs them
// for the export and the client runs them for the on-screen table, so both read
// the same rows in the same order for the same URL.

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

/** A value threshold: `<metric> <field> ≥ or ≤ n`, with n in display units. */
export type Threshold = {
  metric: string;
  field: ReportView;
  op: "gte" | "lte";
  n: number;
};

/** The filters that hide rows of the finished table without changing a number. */
export type ResultFilters = {
  minMaps: number; // 0 = any
  where: Threshold[];
  top: number | null;
};

/** Upper bound on `?top=` and `?minmaps=`, so a hand-edited URL stays sane. */
const MAX_COUNT = 10_000;

function count(raw: string): number | null {
  if (!/^\d+$/.test(raw)) return null;
  const n = Number(raw);
  return n <= MAX_COUNT ? n : null;
}

/**
 * `?minmaps=N`. Absent or malformed means the default: the published maps
 * floor, or any when a player or team is picked, since asking for a player
 * means seeing that player. The legacy `?all=1` reads as any.
 */
export function parseMinMaps(
  sp: SearchParams,
  floor: number,
  picked: boolean,
): number {
  const n = count(one(sp, "minmaps"));
  if (n !== null) return n;
  if (one(sp, "all") === "1" || picked) return 0;
  return floor;
}

/** `?top=N`, a positive count; anything else means no cut. */
export function parseTop(sp: SearchParams): number | null {
  const n = count(one(sp, "top"));
  return n !== null && n > 0 ? n : null;
}

const FIELDS = new Set<string>(["value", "pctl", "z"]);

/**
 * `?where=kd:value:gte:1.2,kills_p10:pctl:gte:90`. A threshold on a column that
 * is not on screen, or one that does not parse, is dropped. A later threshold
 * on the same metric, field and direction replaces an earlier one.
 */
export function parseWhere(sp: SearchParams, keys: string[]): Threshold[] {
  const raw = one(sp, "where");
  if (!raw) return [];
  const byId = new Map<string, Threshold>();
  for (const part of raw.split(",")) {
    const [metric, field, op, n, ...rest] = part.trim().split(":");
    if (rest.length > 0 || !keys.includes(metric) || !FIELDS.has(field)) continue;
    if (op !== "gte" && op !== "lte") continue;
    if (n === undefined || n.trim() === "" || !Number.isFinite(Number(n))) continue;
    byId.set(`${metric}:${field}:${op}`, {
      metric,
      field: field as ReportView,
      op,
      n: Number(n),
    });
  }
  return [...byId.values()];
}

/** The `?where=` value for a set of thresholds, or null for none. */
export function serializeWhere(where: Threshold[]): string | null {
  if (where.length === 0) return null;
  return where.map((t) => `${t.metric}:${t.field}:${t.op}:${t.n}`).join(",");
}

/** A threshold's reading of a row, in the units the URL states. */
function thresholdReading(row: ReportRow, t: Threshold): number | null {
  const r = sortReading(row, t.metric, t.field);
  if (r === null) return null;
  // Rounded so a stored 0.9 reads as exactly 90, not 90.00000000000001.
  return t.field === "pctl" ? Math.round(r * 1e6) / 1e4 : r;
}

/** Min maps and thresholds. A row with no reading fails a threshold. */
export function filterReportRows(
  rows: ReportRow[],
  f: Pick<ResultFilters, "minMaps" | "where">,
): ReportRow[] {
  return rows.filter((row) => {
    if (f.minMaps > 0 && (row.maps ?? 0) < f.minMaps) return false;
    for (const t of f.where) {
      const r = thresholdReading(row, t);
      if (r === null) return false;
      if (t.op === "gte" ? r < t.n : r > t.n) return false;
    }
    return true;
  });
}

/**
 * Every result filter in order: min maps, thresholds, the sort, then top N of
 * what is left. Top N follows the sort, so a new sort re-cuts it.
 */
export function applyResultFilters(
  rows: ReportRow[],
  f: ResultFilters,
  sort: string,
  dir: "asc" | "desc",
  view: ReportView,
  keys: string[],
): ReportRow[] {
  const sorted = sortReportRows(filterReportRows(rows, f), sort, dir, view, keys);
  return f.top !== null ? sorted.slice(0, f.top) : sorted;
}
