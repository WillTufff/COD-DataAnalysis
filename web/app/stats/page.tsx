import type { Metadata } from "next";
import Link from "next/link";
import type { MetricOption } from "./AddColumnMenu";
import { AddFirstColumn } from "./AddFirstColumn";
import { CohortTokens } from "./CohortTokens";
import { PresetPicker, type PresetTile } from "./PresetPicker";
import { ReportTable } from "./ReportTable";
import { modeLabel, pickLabel, seasonLabel } from "./cohortLabel";
import {
  type MetricCatalogEntry,
  getMetricCatalog,
  getReportPlayers,
  getReportTeams,
  getTeamMetricCatalog,
  latestRun,
  queryReport,
  queryTeamReport,
} from "@/lib/analytics";
import { REPORT_PRESETS } from "@/lib/reports/presets";
import { parseEntity, resolveReport } from "@/lib/reports/resolve";
import {
  type SearchParams,
  parsePage,
  parsePer,
} from "@/lib/paging";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Report builder" };

const TIER_ORDER = ["gold", "gold-fun", "standard", "fun"];

// Landing preset: the all-mode engagement core, so the first thing a visitor
// sees covers every season and mode rather than one mode's specialism.
const DEFAULT_PRESET = "slaying-core";

const CATEGORY_LABELS: Record<string, string> = {
  team: "Team",
  slaying: "Slaying & engagement",
  discipline: "Discipline & survival",
  trades: "Trades & entries",
  advantage: "Man-advantage",
  clutch: "Clutch",
  hardpoint: "Hardpoint",
  snd: "Search & Destroy",
  control: "Control",
  ctf: "Capture the Flag",
  uplink: "Uplink",
  streaks: "Multikills & streaks",
  scorestreaks: "Scorestreaks",
};

/** Gold tier first, then by category, so the picker leads with the good stuff. */
function sortMetrics(metrics: MetricCatalogEntry[]): MetricCatalogEntry[] {
  return [...metrics].sort((a, b) => {
    const t = TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier);
    if (t !== 0) return t;
    if (a.category !== b.category) return a.category.localeCompare(b.category);
    return a.label.localeCompare(b.label);
  });
}

export default async function StatsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp: SearchParams = await searchParams;
  const run = await latestRun("metric_layer");
  const catalog = run ? await getMetricCatalog(run.id) : null;

  if (!run || !catalog || catalog.metrics.length === 0) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-12">
        <h1 className="font-display text-5xl font-bold uppercase tracking-tight">
          Report builder
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
  const metrics =
    entity === "teams"
      ? await getTeamMetricCatalog(run.id)
      : sortMetrics(catalog.metrics.filter((m) => m.titles.length > 0));
  const knownKeys = new Set(metrics.map((m) => m.key));

  // One resolution, shared with the export route so a download always matches
  // the table it came from.
  // A bare visit lands on a real report rather than an empty frame: this page
  // fronts every published metric, so it should show what one looks like.
  // (A bare team visit shows all six team metrics; presets are player reports.)
  const resolved = await resolveReport(run.id, sp, metrics, {
    fallbackPreset: entity === "players" ? DEFAULT_PRESET : undefined,
  });
  const { selected, selectedEntries, activePreset, scope, rankedScope } =
    resolved;

  const metricOptions: MetricOption[] = metrics.map((m) => ({
    key: m.key,
    label: m.label,
    category: m.category,
    gold: m.tier.startsWith("gold"),
  }));

  // Preset tiles, each stamped with how many of its columns still resolve
  // against the live catalog (a stale preset shows a smaller count, not a crash).
  const presetTiles: PresetTile[] = REPORT_PRESETS.map((p) => ({
    id: p.id,
    name: p.name,
    blurb: p.blurb,
    category: p.category,
    columns: p.metrics.filter((k) => knownKeys.has(k)).length,
  }));

  const header = (
    <>
      <p className="font-mono text-xs text-ink-muted">
        Build a report · pick a cohort, add metric columns · metric_layer v
        {run.version}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Report builder
      </h1>
    </>
  );

  const presetSection = (
    <PresetPicker presets={presetTiles} activeId={activePreset?.id} />
  );

  // No columns yet — lead with the presets rather than an empty table. The
  // header row that normally hosts the add control does not exist here, so this
  // is the one place it needs its own home. Presets are player reports, so the
  // team builder's blank slate offers only the add control.
  if (selected.length === 0) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-12">
        {header}
        <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
          {metrics.length} published {entity === "teams" ? "team " : ""}metrics,
          each scored against its own season-and-mode cohort.{" "}
          {entity === "teams"
            ? "Add columns to build a report."
            : "Start with a preset, or add columns to build your own."}
        </p>
        {entity === "players" && <div className="mt-8">{presetSection}</div>}
        <div className="mt-8 flex items-center gap-3 border-t border-hairline pt-4 text-sm text-ink-secondary print:hidden">
          <AddFirstColumn
            catalog={metricOptions}
            categoryLabels={CATEGORY_LABELS}
          />
          <span>or add a metric column to start from scratch.</span>
        </div>
      </main>
    );
  }

  const {
    years,
    playerSlugs,
    teamSlugs,
    modeSlug,
    qualifiedOnly,
    sort,
    dir,
    defaultSortKey,
    defaultDir,
  } = resolved;
  const [{ columns, rows }, scopePlayers, scopeTeams] = await Promise.all([
    entity === "teams"
      ? queryTeamReport(run.id, resolved.query, selectedEntries)
      : queryReport(run.id, resolved.query, selectedEntries),
    // The Players token has no place on a team report — the rows are teams.
    entity === "teams" ? Promise.resolve([]) : getReportPlayers(run.id),
    getReportTeams(),
  ]);
  const handleBySlug = new Map(scopePlayers.map((p) => [p.slug, p.handle]));
  const teamNameBySlug = new Map(scopeTeams.map((t) => [t.slug, t.name]));
  // What the filter tokens claim, reused by the print stamp and empty state.
  const playersText = pickLabel(playerSlugs, handleBySlug, "players");
  const teamsText = pickLabel(teamSlugs, teamNameBySlug, "teams");

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      {header}

      {entity === "players" && (
        <details className="mt-6 group print:hidden" open={!!activePreset}>
          <summary className="cursor-pointer list-none text-xs text-ink-muted hover:text-ink">
            <span className="group-open:hidden">▸ Start from a preset</span>
            <span className="hidden group-open:inline">▾ Presets</span>
          </summary>
          <div className="mt-3">{presetSection}</div>
        </details>
      )}

      <div className="mt-6">
        <CohortTokens
          entity={entity}
          seasons={scope.seasons}
          years={years}
          modes={scope.modes}
          allModes={rankedScope.allModes}
          modeSlug={modeSlug}
          players={scopePlayers}
          pickedPlayers={playerSlugs}
          teams={scopeTeams}
          pickedTeams={teamSlugs}
        />
      </div>

      {/* Print-only cohort stamp: the controls are hidden on paper, so the
          printout names its own cohort. */}
      <p className="mt-4 hidden font-mono text-xs text-ink-secondary print:block">
        {activePreset ? `${activePreset.name} · ` : ""}
        {entity === "teams" ? "team report · " : ""}
        {seasonLabel(scope.seasons, years)} · {modeLabel(modeSlug)} ·{" "}
        {entity === "players" ? `${playersText.toLowerCase()} · ` : ""}
        {teamsText.toLowerCase()} ·{" "}
        {qualifiedOnly ? "qualified only" : "including small samples"} ·
        metric_layer v{run.version}
      </p>

      {rows.length === 0 ? (
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
                )} in this cohort. Try a different season or mode, or clear the player and team filters.`
            : `No ${entity} match this cohort. The chosen columns may not cover ${seasonLabel(scope.seasons, years).toLowerCase()}${modeSlug ? ` in ${modeLabel(modeSlug)}` : ""}. Try a different season or mode, or widen the sample.`}
        </p>
      ) : (
        <ReportTable
          entity={entity}
          columns={columns}
          rows={rows}
          catalog={metricOptions}
          categoryLabels={CATEGORY_LABELS}
          showMode={modeSlug === undefined}
          qualifiedOnly={qualifiedOnly}
          initialPer={parsePer(sp)}
          initialPage={parsePage(sp)}
          initialSort={{ id: sort, dir }}
          defaultSort={{ id: defaultSortKey, dir: defaultDir }}
        />
      )}

      <p className="mt-3 max-w-3xl text-xs text-ink-muted">
        Each cell is scored within the qualified {entity} of its own season and
        mode, so a column can qualify a {entity === "teams" ? "team" : "player"}{" "}
        the next column does not — those cells are greyed, never dropped.
        Percentile and z-score are within that cohort. Full definitions are on
        the{" "}
        <Link href="/methodology#metrics" className="underline">
          methodology
        </Link>{" "}
        page.
      </p>
    </main>
  );
}
