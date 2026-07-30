// The one place URL params become a report. The page and the export route both
// call this, so a downloaded file always matches the table it was exported from
// — the cohort, the column order, the sort, and the qualified gate are resolved
// exactly once.

import {
  type MetricCatalogEntry,
  type ReportQuery,
  type ScopeSeason,
  getReportScope,
  getTeamReportScope,
} from "@/lib/analytics";
import { type SearchParams, one } from "@/lib/paging";
import {
  type ReportPreset,
  presetById,
  sanitizePresetMetrics,
} from "./presets";

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
  years: number[]; // empty = every covered season, combined
  playerSlugs: string[]; // empty = everyone
  teamSlugs: string[]; // empty = every team
  modeSlug?: string;
  qualifiedOnly: boolean;
  sort: string;
  dir: "asc" | "desc";
  defaultSortKey: string;
  defaultDir: "asc" | "desc";
  query: ReportQuery;
};

/**
 * The season cohort: a CSV of years on `?years=`, tolerant of the legacy
 * single-season `?year=`. An absent or empty key means every covered season,
 * combined — seasons are additive, so "none picked" and "all picked" are the
 * same report and only one of them needs to ride the URL.
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
  // Presets are player reports; the team catalog is six metrics, so its bare
  // visit simply shows all of them.
  const explicit = parseMetrics(sp).filter((k) => byKey.has(k));
  const untouched =
    !("metrics" in sp) && !("metric" in sp) && !("preset" in sp);
  const namedPresetId = entity === "players" ? one(sp, "preset") : "";
  const presetId =
    namedPresetId ||
    (entity === "players" && untouched ? (opts.fallbackPreset ?? "") : "");
  const preset =
    explicit.length === 0 && presetId ? presetById(presetId) : undefined;
  // The fallback seeds a preset's columns, sort and mode, but only a preset
  // named in the URL counts as *chosen* — a bare visit shows a default report,
  // not a pre-selected tile.
  const activePreset = namedPresetId ? preset : undefined;
  const selected =
    explicit.length > 0
      ? explicit
      : preset
        ? sanitizePresetMetrics(preset, knownKeys)
        : entity === "teams" && untouched
          ? metrics.map((m) => m.key)
          : [];
  const selectedEntries = selected.map((k) => byKey.get(k)!);

  const qualifiedOnly = one(sp, "all") !== "1";
  const playerSlugs = parsePlayers(sp);
  const teamSlugs = parseTeams(sp);

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

  // Seasons combine: the cohort is the set of picked years, dropping any the
  // chosen columns don't cover. Picking every offered season is the same report
  // as picking none, so both normalise to the empty "all covered" set.
  const askedYears = parseYears(sp).filter((y) => scope.years.includes(y));
  const years = askedYears.length === scope.years.length ? [] : askedYears;

  // The cohort fixes a single mode — modes are compared, not combined. An
  // active preset seeds its mode; otherwise "all modes combined" is the default
  // only when the ranked column has all-modes rows, else that column's first
  // real mode. An explicitly empty `?mode=` is a deliberate pick of "combined"
  // and outranks the preset's seed.
  const modeRaw = one(sp, "mode");
  const modeExplicitAll = "mode" in sp && modeRaw === "" && rankedScope.allModes;
  const presetMode =
    preset?.defaultMode && scope.modes.includes(preset.defaultMode)
      ? preset.defaultMode
      : undefined;
  const modeDefault =
    presetMode ?? (rankedScope.allModes ? undefined : rankedScope.modes[0]);
  const modeSlug = scope.modes.includes(modeRaw)
    ? modeRaw
    : modeExplicitAll
      ? undefined
      : modeDefault;

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
    qualifiedOnly,
    sort,
    dir,
    defaultSortKey,
    defaultDir,
    query: {
      metrics: selected,
      years,
      modeSlug,
      players: playerSlugs,
      teams: teamSlugs,
      qualifiedOnly,
      sort,
      dir,
    },
  };
}
