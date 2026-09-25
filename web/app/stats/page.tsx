import type { Metadata } from "next";
import Link from "next/link";
import type { MetricOption } from "./AddColumnMenu";
import { AddFirstColumn } from "./AddFirstColumn";
import { EntityTabs } from "./EntityTabs";
import { FilterBands } from "./FilterBands";
import { PresetStrip, type PresetTile } from "./PresetStrip";
import { ReportTable } from "./ReportTable";
import {
  ALL_MODES_LABEL,
  modeLabel,
  pickLabel,
  seasonLabel,
} from "./cohortLabel";
import {
  type MetricCatalogEntry,
  getMetricCatalog,
  getMetricCoverage,
  getReportPlayers,
  getReportTeams,
  getReportViewPlayers,
  getTeamMetricCatalog,
  latestRun,
  queryReport,
  queryTeamReport,
  getModeCatalog,
} from "@/lib/analytics";
import { categoryLabel } from "@/lib/reports/labels";
import { applyResultFilters } from "@/lib/reports/rows";
import { presetsFor } from "@/lib/reports/presets";
import { parseEntity, resolveReportForUrl } from "@/lib/reports/resolve";
import {
  type SearchParams,
  parsePage,
  parsePer,
} from "@/lib/paging";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Stats" };

const TIER_ORDER = ["gold", "gold-fun", "standard", "fun"];

/** Gold tier first, then by category, so the picker leads with the good stuff. */
function sortMetrics(metrics: MetricCatalogEntry[]): MetricCatalogEntry[] {
  return [...metrics].sort((a, b) => {
    const t = TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier);
    if (t !== 0) return t;
    if (a.category !== b.category) return a.category.localeCompare(b.category);
    return a.label.localeCompare(b.label);
  });
}

/** A set of years as a span: "2017–2019", or one year alone. */
function yearSpan(years: number[]): string {
  if (years.length === 0) return "";
  const lo = Math.min(...years);
  const hi = Math.max(...years);
  return lo === hi ? String(lo) : `${lo}–${hi}`;
}

/** The row filters a preset link keeps, so switching presets keeps the view. */
const CARRIED_PARAMS = [
  "years",
  "players",
  "teams",
  "all",
  "minmaps",
  "where",
  "top",
  "view",
];

export default async function StatsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp: SearchParams = await searchParams;
  const run = await latestRun("metric_layer");
  const [catalog, modeCatalog] = await Promise.all([
    run ? getMetricCatalog(run.id) : Promise.resolve(null),
    getModeCatalog(),
  ]);

  if (!run || !catalog || catalog.metrics.length === 0) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-12">
        <h1 className="font-display text-5xl font-bold uppercase tracking-tight">
          Stats
        </h1>
        <p className="mt-4 text-sm text-ink-secondary">
          No metric run has been published yet.
        </p>
      </main>
    );
  }

  // A metric no title cleared coverage for has no rows in any season, so it is
  // not a column anyone can chart. It keeps its glossary entry, which explains
  // the absence. The team catalog is small and hand-ordered (map win rate
  // first), so it is used as published rather than re-sorted.
  const entity = parseEntity(sp);
  const [metrics, coverage] = await Promise.all([
    entity === "teams"
      ? getTeamMetricCatalog(run.id)
      : Promise.resolve(
          sortMetrics(catalog.metrics.filter((m) => m.titles.length > 0)),
        ),
    getMetricCoverage(run.id, entity),
  ]);
  const knownKeys = new Set(metrics.map((m) => m.key));

  // One resolution, shared with the export route so a download always matches
  // the table it came from.
  const resolved = await resolveReportForUrl(run.id, sp, metrics);
  const { selected, selectedEntries, activePreset, scope, rankedScope } =
    resolved;
  const { years, playerSlugs, teamSlugs, modeSlug } = resolved;

  // A metric is in view when it has rows for at least one season on screen in
  // the mode on screen. An empty `years` is every covered season.
  const viewYears = years.length > 0 ? years : scope.years;
  const coverageYears = (key: string) =>
    [...new Set((coverage[key] ?? []).map((c) => Number(c.split(":")[0])))];
  const metricOptions: MetricOption[] = metrics.map((m) => {
    const cells = new Set(coverage[m.key] ?? []);
    return {
      key: m.key,
      label: m.label,
      category: categoryLabel(m.category),
      gold: m.tier.startsWith("gold"),
      inView: viewYears.some((y) => cells.has(`${y}:${modeSlug ?? ""}`)),
      span: yearSpan(coverageYears(m.key)),
    };
  });

  // Preset links keep the row filters already set, so moving between presets
  // changes the columns and mode and nothing else.
  const carried = new URLSearchParams();
  for (const k of CARRIED_PARAMS) {
    const v = sp[k];
    if (typeof v === "string") carried.set(k, v);
  }
  const presetTiles: PresetTile[] = presetsFor(entity).map((p) => {
    const kept = p.metrics.filter((k) => knownKeys.has(k));
    const params = new URLSearchParams(carried);
    if (entity === "teams") params.set("entity", "teams");
    params.set("preset", p.id);
    params.sort();
    const span = yearSpan([...new Set(kept.flatMap(coverageYears))]);
    return {
      id: p.id,
      name: p.name,
      blurb: p.blurb,
      href: `/stats?${params.toString()}`,
      span,
    };
  });

  const header = (
    <>
      <h1 className="font-display text-5xl font-bold uppercase tracking-tight">
        Stats
      </h1>
      <p className="mt-2 max-w-2xl text-sm text-ink-secondary">
        {metrics.length} {entity === "teams" ? "team" : "player"} metrics,
        season by season. Each number is scored against the qualified field of
        its own season and mode.
      </p>
      <div className="mt-6">
        <EntityTabs entity={entity} />
      </div>
      <div className="mt-4">
        <PresetStrip presets={presetTiles} activeId={activePreset?.id} />
      </div>
    </>
  );

  const footnote = (
    <p className="mt-3 max-w-3xl text-xs text-ink-muted">
      Each cell is scored within the qualified {entity} of its own season and
      mode, so a column can qualify a {entity === "teams" ? "team" : "player"}{" "}
      the next column does not. Those cells are greyed, never dropped. Min
      maps hides whole rows, and defaults to the published floor. Each
      column&apos;s ▾ gives its formula and floor; full definitions are on the{" "}
      <Link href="/methodology/metrics" className="underline">
        methodology
      </Link>{" "}
      page. Metric layer v{run.version}.
    </p>
  );

  // No columns yet: the add control has no header row to live in.
  if (selected.length === 0) {
    return (
      <main className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
        {header}
        <div className="mt-8 flex items-center gap-3 border-t border-hairline pt-4 text-sm text-ink-secondary print:hidden">
          <AddFirstColumn catalog={metricOptions} />
          <span>Add a metric column, or pick a preset above.</span>
        </div>
      </main>
    );
  }

  const { view, sort, dir, defaultSortKey, defaultDir, filters } = resolved;
  // Rows come back without result filters: the table applies them after its
  // own sort, since top N follows whichever column a header click sorts on.
  const [{ columns, rows }, viewPlayers, allPlayers, scopeTeams] =
    await Promise.all([
      entity === "teams"
        ? queryTeamReport(run.id, resolved.query, selectedEntries)
        : queryReport(run.id, resolved.query, selectedEntries),
      // The player filter has no place on a team report: the rows are teams.
      entity === "teams"
        ? Promise.resolve([])
        : getReportViewPlayers(run.id, { years, modeSlug }),
      // Names for picks outside the view, which the chip still has to show.
      entity === "teams" || playerSlugs.length === 0
        ? Promise.resolve([])
        : getReportPlayers(run.id),
      getReportTeams(),
    ]);
  const handleBySlug = new Map(
    [...allPlayers, ...viewPlayers].map((p) => [p.slug, p.handle]),
  );
  const playerNames = Object.fromEntries(
    playerSlugs.map((s) => [s, handleBySlug.get(s) ?? s]),
  );
  const shownRows = applyResultFilters(
    rows,
    filters,
    sort,
    dir,
    view,
    resolved.selected,
  );
  const teamNameBySlug = new Map(scopeTeams.map((t) => [t.slug, t.name]));
  // What the filter chips claim, reused by the print stamp and empty state.
  const playersText = pickLabel(playerSlugs, handleBySlug, "players");
  const teamsText = pickLabel(teamSlugs, teamNameBySlug, "teams");

  return (
    <main className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
      {header}

      <div className="mt-4">
        <FilterBands
          entity={entity}
          seasons={scope.seasons}
          years={years}
          modes={scope.modes}
          modeCatalog={modeCatalog}
          allModes={rankedScope.allModes}
          modeSlug={modeSlug}
          players={viewPlayers}
          pickedPlayers={playerSlugs}
          playerNames={playerNames}
          teams={scopeTeams}
          pickedTeams={teamSlugs}
          mapsFloor={resolved.mapsFloor}
          minMaps={resolved.minMaps}
          minMapsSet={"minmaps" in sp || sp.all === "1"}
          where={resolved.where}
          top={resolved.top}
          columns={columns.map((c) => ({
            key: c.key,
            label: c.label,
            unit: c.unit,
          }))}
        />
      </div>

      {/* Print-only stamp: the controls are hidden on paper, so the printout
          names its own filters. */}
      <p className="mt-4 hidden font-mono text-xs text-ink-secondary print:block">
        {activePreset ? `${activePreset.name} · ` : ""}
        {entity === "teams" ? "teams · " : ""}
        {seasonLabel(scope.seasons, years)} ·{" "}
        {modeLabel(modeCatalog, modeSlug, ALL_MODES_LABEL)} ·{" "}
        {entity === "players" ? `${playersText.toLowerCase()} · ` : ""}
        {teamsText.toLowerCase()} ·{" "}
        {resolved.minMaps > 0 ? `min ${resolved.minMaps} maps` : "any maps"}
        {resolved.where.length > 0
          ? ` · ${resolved.where.length} threshold${resolved.where.length > 1 ? "s" : ""}`
          : ""}
        {resolved.top !== null ? ` · top ${resolved.top}` : ""} · metric layer
        v{run.version}
      </p>

      {shownRows.length === 0 ? (
        <p className="mt-8 text-sm text-ink-secondary">
          {playerSlugs.length > 0 || teamSlugs.length > 0
            ? `No rows for ${[
                playerSlugs.length > 0 && entity === "players"
                  ? playersText
                  : "",
                teamSlugs.length > 0 ? teamsText : "",
              ]
                .filter(Boolean)
                .join(
                  " on ",
                )} here. Try a different season or mode, or clear the row filters.`
            : `No ${entity} match these filters. The chosen columns may not cover ${seasonLabel(scope.seasons, years).toLowerCase()}${modeSlug ? ` in ${modeLabel(modeCatalog, modeSlug)}` : ""}. Try a different season or mode, or loosen the row filters.`}
        </p>
      ) : (
        <ReportTable
          entity={entity}
          columns={columns}
          rows={rows}
          catalog={metricOptions}
          filters={filters}
          initialView={view}
          initialPer={parsePer(sp)}
          initialPage={parsePage(sp)}
          initialSort={{ id: sort, dir }}
          defaultSort={{ id: defaultSortKey, dir: defaultDir }}
        />
      )}

      {footnote}
    </main>
  );
}
