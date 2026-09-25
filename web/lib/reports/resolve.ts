// The one place URL params become a report. The page and the export route both
// call this, so a downloaded file always matches the table it was exported from
// — the cohort, the column order, the sort, and the result filters are resolved
// exactly once.

import {
  type MetricCatalogEntry,
  type ReportQuery,
  type ScopeSeason,
  getReportScope,
  getTeamReportScope,
} from "@/lib/analytics";
import { type SearchParams, one } from "@/lib/paging";
import type { AggregateQuery } from "./aggregate";
import { type ContentFilters, hasContent, parseContent } from "./content";
import {
  DEFAULT_PRESET,
  DEFAULT_TEAM_PRESET,
  type ReportPreset,
  presetById,
  sanitizePresetMetrics,
} from "./presets";
import {
  type ReportView,
  type ResultFilters,
  type Threshold,
  parseMinMaps,
  parseTop,
  parseView,
  parseWhere,
} from "./rows";

export type ReportScope = {
  years: number[];
  seasons: ScopeSeason[];
  modes: string[];
  allModes: boolean;
};

const EMPTY_SCOPE: ReportScope = {
  years: [],
  seasons: [],
  modes: [],
  allModes: false,
};

/** What a row of the report is. Players are the default; teams are the mirror. */
export type ReportEntity = "players" | "teams";

/** The row entity: `?entity=teams` switches the builder; anything else is players. */
export function parseEntity(sp: SearchParams): ReportEntity {
  return one(sp, "entity") === "teams" ? "teams" : "players";
}

export type ResolvedReport = {
  entity: ReportEntity;
  selected: string[];
  selectedEntries: MetricCatalogEntry[];
  activePreset?: ReportPreset;
  scope: ReportScope; // union across columns — what the pickers offer
  rankedScope: ReportScope; // ranked column's own coverage — the default cohort
  years: number[]; // empty = every covered season
  playerSlugs: string[]; // empty = everyone
  teamSlugs: string[]; // empty = every team
  modeSlug?: string;
  /** Two or more modes combined into one row; empty otherwise. */
  modeMix: string[];
  /** One row per player over every picked season (`?rows=span`). */
  span: boolean;
  /** Tier, venue, stage, map and date filters on the maps; any one forces aggregation. */
  content: ContentFilters;
  /** Numbers re-aggregated from map rows rather than read from season rows. */
  aggregated: boolean;
  /** The published maps floor: the min maps a bare URL applies. */
  mapsFloor: number;
  minMaps: number;
  where: Threshold[];
  top: number | null;
  filters: ResultFilters;
  sort: string;
  dir: "asc" | "desc";
  view: ReportView;
  defaultSortKey: string;
  defaultDir: "asc" | "desc";
  query: ReportQuery;
  /** Set when the numbers are re-aggregated from map rows. */
  aggregate?: AggregateQuery;
};

/**
 * The season filter: a CSV of years on `?years=`, tolerant of the legacy
 * single-season `?year=`. Returns only the listed years; `?years=all` (or an
 * empty key) is read by `seasonsAll`, and an absent key means the latest season.
 */
export function parseYears(sp: SearchParams): number[] {
  const raw = one(sp, "years") || one(sp, "year");
  if (!raw) return [];
  const seen = new Set<number>();
  for (const part of raw.split(",")) {
    const n = Number(part.trim());
    if (Number.isInteger(n)) seen.add(n);
  }
  return [...seen].sort((a, b) => a - b);
}

/** `?years=all`, or the key present and empty: every covered season. */
export function seasonsAll(sp: SearchParams): boolean {
  const raw = one(sp, "years");
  return raw === "all" || ("years" in sp && raw === "");
}

/**
 * A slug-CSV filter key. Absent or empty means unfiltered — like seasons, "no
 * pick" and "all" are the same report. Unknown slugs are kept rather than
 * validated away: they match no rows, which a stale link should surface as an
 * empty filter, not silently become the unfiltered field.
 */
function parseSlugCsv(sp: SearchParams, key: string): string[] {
  const raw = one(sp, key);
  if (!raw) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const slug = part.trim().toLowerCase();
    if (slug && !seen.has(slug)) {
      seen.add(slug);
      out.push(slug);
    }
  }
  return out;
}

/** `?rows=span`: one combined row per player over the picked seasons. */
export function parseSpan(sp: SearchParams): boolean {
  return one(sp, "rows") === "span";
}

/** The player filter: `?players=` as a CSV of player slugs. */
export function parsePlayers(sp: SearchParams): string[] {
  return parseSlugCsv(sp, "players");
}

/** The team filter: `?teams=` as a CSV of team slugs. */
export function parseTeams(sp: SearchParams): string[] {
  return parseSlugCsv(sp, "teams");
}

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
 * What a URL means to this site: `resolveReport` plus the landing fallback the
 * page and the export route have to agree on. Both go through here rather than
 * choosing their own options, because they once chose differently — the page
 * fell back to the default preset on a bare visit while the export route did
 * not, so downloading the very report a first-time visitor sees returned 400.
 */
export function resolveReportForUrl(
  runId: number,
  sp: SearchParams,
  metrics: MetricCatalogEntry[],
): Promise<ResolvedReport> {
  return resolveReport(runId, sp, metrics, {
    fallbackPreset:
      parseEntity(sp) === "players" ? DEFAULT_PRESET : DEFAULT_TEAM_PRESET,
  });
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
  const entity = parseEntity(sp);
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
  const preset =
    explicit.length === 0 && presetId ? presetById(presetId, entity) : undefined;
  const activePreset = preset;
  const selected =
    explicit.length > 0
      ? explicit
      : preset
        ? sanitizePresetMetrics(preset, knownKeys)
        : entity === "teams" && untouched
          ? metrics.map((m) => m.key)
          : [];
  const selectedEntries = selected.map((k) => byKey.get(k)!);

  const playerSlugs = parsePlayers(sp);
  const teamSlugs = parseTeams(sp);
  const view = parseView(sp);
  // The row filters that apply to this entity: a team report has no player
  // filter, so a stray `players=` must not lower its min maps.
  const picked =
    teamSlugs.length > 0 || (entity === "players" && playerSlugs.length > 0);
  // The min maps default is the floor the catalog publishes for its
  // maps-denominated metrics, and those metrics are what carry a row's maps.
  const mapsEntries = metrics.filter((m) => m.denom_kind === "maps");
  const mapsMetrics = mapsEntries.map((m) => m.key);
  const mapsFloor =
    mapsEntries.length > 0 ? Math.min(...mapsEntries.map((m) => m.min_denom)) : 0;
  const minMaps = parseMinMaps(sp, mapsFloor, picked);
  const where = parseWhere(sp, selected);
  const top = parseTop(sp);
  const filters: ResultFilters = { minMaps, where, top };
  const span = parseSpan(sp);
  const content = parseContent(sp);

  if (selected.length === 0) {
    return {
      entity,
      selected,
      selectedEntries,
      activePreset,
      scope: EMPTY_SCOPE,
      rankedScope: EMPTY_SCOPE,
      years: [],
      playerSlugs,
      teamSlugs,
      modeMix: [],
      span,
      content,
      aggregated: span || hasContent(content),
      mapsFloor,
      minMaps,
      where,
      top,
      filters,
      sort: "player",
      dir: "asc",
      view,
      defaultSortKey: "",
      defaultDir: "asc",
      query: { metrics: [], mapsMetrics },
    };
  }

  // Sort defaults to the preset's ranked column when active and still present,
  // else the first column, best-first. A stale `?sort=` falls back here.
  const presetSortKey =
    preset?.defaultSort && selected.includes(preset.defaultSort)
      ? preset.defaultSort
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
  const scopeOf = entity === "teams" ? getTeamReportScope : getReportScope;
  const [scope, rankedScope] = await Promise.all([
    scopeOf(runId, selected),
    scopeOf(runId, [rankedKey]),
  ]);

  // The season filter keeps the picked years the chosen columns cover. With no
  // pick it is the ranked column's latest season, since percentiles are scored
  // within a season and one season is the view they read cleanly in. Picking
  // every offered season is the same report as `all`, so both normalise to the
  // empty set.
  const askedYears = parseYears(sp).filter((y) => scope.years.includes(y));
  const latest = rankedScope.years.at(-1) ?? scope.years.at(-1);
  const pickedYears = seasonsAll(sp)
    ? []
    : askedYears.length > 0
      ? askedYears
      : latest !== undefined
        ? [latest]
        : [];
  const years = pickedYears.length === scope.years.length ? [] : pickedYears;

  // `?mode=` is a CSV. One mode reads that mode's published rows; two or more
  // are a mix, re-aggregated from their maps into one row. With no pick, an
  // active preset seeds its mode; otherwise "all modes combined" is the default
  // only when the ranked column has all-modes rows, else that column's first
  // real mode. An explicitly empty `?mode=` is a deliberate pick of "combined"
  // and outranks the preset's seed.
  const modePicks = parseSlugCsv(sp, "mode").filter((m) => scope.modes.includes(m));
  const modeMix =
    modePicks.length > 1 ? scope.modes.filter((m) => modePicks.includes(m)) : [];
  const modeRaw = modePicks.length === 1 ? modePicks[0] : "";
  const modeExplicitAll =
    "mode" in sp && one(sp, "mode") === "" && rankedScope.allModes;
  const presetMode =
    preset?.defaultMode && scope.modes.includes(preset.defaultMode)
      ? preset.defaultMode
      : undefined;
  const modeDefault =
    presetMode ?? (rankedScope.allModes ? undefined : rankedScope.modes[0]);
  const modeSlug =
    modeMix.length > 0
      ? undefined
      : scope.modes.includes(modeRaw)
        ? modeRaw
        : modeExplicitAll
          ? undefined
          : modeDefault;
  // A content filter narrows the maps under every row, which the published
  // season rows cannot, so it always takes the re-aggregation path.
  const aggregated = span || modeMix.length > 0 || hasContent(content);

  return {
    entity,
    selected,
    selectedEntries,
    activePreset,
    scope,
    rankedScope,
    years,
    playerSlugs,
    teamSlugs,
    modeSlug,
    modeMix,
    span,
    content,
    aggregated,
    mapsFloor,
    minMaps,
    where,
    top,
    filters,
    sort,
    dir,
    view,
    defaultSortKey,
    defaultDir,
    query: {
      metrics: selected,
      mapsMetrics,
      years,
      modeSlug,
      players: playerSlugs,
      teams: teamSlugs,
    },
    aggregate: aggregated
      ? {
          metrics: selected,
          // Every covered season, named: the map rows reach back further than
          // the columns do, and a season no column covers has nothing to add.
          years: years.length > 0 ? years : scope.years,
          modes: modeMix.length > 0 ? modeMix : modeSlug ? [modeSlug] : [],
          span,
          players: playerSlugs,
          teams: teamSlugs,
          ...(hasContent(content) ? { content } : {}),
        }
      : undefined,
  };
}
