// The one place URL params become a report. The page and the export route both
// call this, so a downloaded file always matches the table it was exported from
// — the cohort, the column order, the sort, and the qualified gate are resolved
// exactly once.

import {
  type MetricCatalogEntry,
  type ReportQuery,
  getReportScope,
} from "@/lib/analytics";
import { type SearchParams, one } from "@/lib/paging";
import {
  type ReportPreset,
  presetById,
  sanitizePresetMetrics,
} from "./presets";

export type ReportScope = { years: number[]; modes: string[]; allModes: boolean };

const EMPTY_SCOPE: ReportScope = { years: [], modes: [], allModes: false };

export type ResolvedReport = {
  selected: string[];
  selectedEntries: MetricCatalogEntry[];
  activePreset?: ReportPreset;
  scope: ReportScope; // union across columns — what the dropdowns offer
  rankedScope: ReportScope; // ranked column's own coverage — the default cohort
  year?: number;
  modeSlug?: string;
  qualifiedOnly: boolean;
  sort: string;
  dir: "asc" | "desc";
  defaultSortKey: string;
  defaultDir: "asc" | "desc";
  query: ReportQuery;
};

/** The ordered column CSV, tolerant of the legacy single-metric `?metric=` key. */
export function parseMetrics(sp: SearchParams): string[] {
  const raw = one(sp, "metrics") || one(sp, "metric");
  if (!raw) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const k of raw.split(",").map((s) => s.trim())) {
    if (k && !seen.has(k)) {
      seen.add(k);
      out.push(k);
    }
  }
  return out;
}

/**
 * Resolve the report for a set of URL params. `metrics` is the catalog's
 * chartable entries (title coverage > 0), already in display order. When no
 * columns are named the result carries `selected: []` and the caller shows the
 * empty state / returns a 400 rather than querying an empty cohort.
 */
export async function resolveReport(
  runId: number,
  sp: SearchParams,
  metrics: MetricCatalogEntry[],
  opts: {
    /** Preset to fall back to when the URL names no report at all — a bare
     *  first visit. Deliberately not applied when a column key is present but
     *  empty, so clearing every column still reaches the blank slate. */
    fallbackPreset?: string;
  } = {},
): Promise<ResolvedReport> {
  const byKey = new Map(metrics.map((m) => [m.key, m]));
  const knownKeys = new Set(byKey.keys());

  // Explicit `metrics` (or the legacy single `metric`) always wins; a `preset`
  // only seeds columns when none were named, so editing a preset's columns —
  // which writes explicit `metrics` and drops `preset` — is respected.
  const explicit = parseMetrics(sp).filter((k) => byKey.has(k));
  const untouched =
    !("metrics" in sp) && !("metric" in sp) && !("preset" in sp);
  const presetId =
    one(sp, "preset") || (untouched ? (opts.fallbackPreset ?? "") : "");
  const activePreset =
    explicit.length === 0 && presetId ? presetById(presetId) : undefined;
  const selected =
    explicit.length > 0
      ? explicit
      : activePreset
        ? sanitizePresetMetrics(activePreset, knownKeys)
        : [];
  const selectedEntries = selected.map((k) => byKey.get(k)!);

  const qualifiedOnly = one(sp, "all") !== "1";

  if (selected.length === 0) {
    return {
      selected,
      selectedEntries,
      activePreset,
      scope: EMPTY_SCOPE,
      rankedScope: EMPTY_SCOPE,
      qualifiedOnly,
      sort: "player",
      dir: "asc",
      defaultSortKey: "",
      defaultDir: "asc",
      query: { metrics: [], qualifiedOnly, sort: "player", dir: "asc" },
    };
  }

  // Sort defaults to the preset's ranked column when active and still present,
  // else the first column, best-first. A stale `?sort=` falls back here.
  const presetSortKey =
    activePreset?.defaultSort && selected.includes(activePreset.defaultSort)
      ? activePreset.defaultSort
      : undefined;
  const defaultSortKey = presetSortKey ?? selected[0];
  const defaultDir = byKey.get(defaultSortKey)!.higher_is_better ? "desc" : "asc";
  const sortRaw = one(sp, "sort");
  const sort =
    sortRaw === "player" || selected.includes(sortRaw) ? sortRaw : defaultSortKey;
  const dirRaw = one(sp, "dir");
  const dir: "asc" | "desc" =
    dirRaw === "asc" || dirRaw === "desc"
      ? dirRaw
      : sort === "player"
        ? "asc"
        : byKey.get(sort)!.higher_is_better
          ? "desc"
          : "asc";

  // Two scopes: the union across all columns drives which seasons/modes the
  // dropdowns offer; the ranked column's own coverage drives the default cohort
  // so a fresh report lands on a populated table.
  const rankedKey = sort === "player" ? defaultSortKey : sort;
  const [scope, rankedScope] = await Promise.all([
    getReportScope(runId, selected),
    getReportScope(runId, [rankedKey]),
  ]);

  const yearRaw = Number(one(sp, "year"));
  const year = scope.years.includes(yearRaw) ? yearRaw : undefined;

  // The cohort fixes a single mode. An active preset seeds its mode; otherwise
  // "all modes combined" is the default only when the ranked column has
  // all-modes rows, else that column's first real mode.
  const modeRaw = one(sp, "mode");
  const presetMode =
    activePreset?.defaultMode && scope.modes.includes(activePreset.defaultMode)
      ? activePreset.defaultMode
      : undefined;
  const modeDefault =
    presetMode ?? (rankedScope.allModes ? undefined : rankedScope.modes[0]);
  const modeSlug = scope.modes.includes(modeRaw)
    ? modeRaw
    : modeRaw === "" && rankedScope.allModes && !presetMode
      ? undefined
      : modeDefault;

  return {
    selected,
    selectedEntries,
    activePreset,
    scope,
    rankedScope,
    year,
    modeSlug,
    qualifiedOnly,
    sort,
    dir,
    defaultSortKey,
    defaultDir,
    query: { metrics: selected, year, modeSlug, qualifiedOnly, sort, dir },
  };
}
