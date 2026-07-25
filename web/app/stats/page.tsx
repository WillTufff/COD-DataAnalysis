import type { Metadata } from "next";
import Link from "next/link";
import { CarryParams } from "@/components/table/CarryParams";
import { ColumnPicker, type PickerMetric } from "./ColumnPicker";
import { ExportBar } from "./ExportBar";
import { PresetPicker, type PresetTile } from "./PresetPicker";
import { ReportTable } from "./ReportTable";
import {
  type MetricCatalogEntry,
  getMetricCatalog,
  latestRun,
  queryReport,
} from "@/lib/analytics";
import { REPORT_PRESETS } from "@/lib/reports/presets";
import { resolveReport } from "@/lib/reports/resolve";
import {
  type SearchParams,
  parsePage,
  parsePer,
} from "@/lib/paging";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Report builder" };

const MODE_LABELS: Record<string, string> = {
  hardpoint: "Hardpoint",
  "search-and-destroy": "Search & Destroy",
  control: "Control",
  "capture-the-flag": "Capture the Flag",
  uplink: "Uplink",
};

const TIER_ORDER = ["gold", "gold-fun", "standard", "fun"];

// Landing preset: the all-mode engagement core, so the first thing a visitor
// sees covers every season and mode rather than one mode's specialism.
const DEFAULT_PRESET = "slaying-core";

const CATEGORY_LABELS: Record<string, string> = {
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
  // the absence.
  const metrics = sortMetrics(catalog.metrics.filter((m) => m.titles.length > 0));
  const knownKeys = new Set(metrics.map((m) => m.key));

  // One resolution, shared with the export route so a download always matches
  // the table it came from.
  // A bare visit lands on a real report rather than an empty frame: this page
  // fronts every published metric, so it should show what one looks like.
  const resolved = await resolveReport(run.id, sp, metrics, {
    fallbackPreset: DEFAULT_PRESET,
  });
  const { selected, selectedEntries, activePreset, scope, rankedScope } =
    resolved;

  const pickerCatalog: PickerMetric[] = metrics.map((m) => ({
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

  const picker = (
    <div className="mt-6 border-y border-hairline py-4 print:hidden">
      <ColumnPicker
        catalog={pickerCatalog}
        categoryLabels={CATEGORY_LABELS}
        selected={selected}
      />
    </div>
  );

  // No columns yet — lead with the presets and the picker rather than an empty
  // table.
  if (selected.length === 0) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-12">
        {header}
        <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
          {catalog.metrics.length} published metrics, each scored against its own
          season-and-mode cohort. Start with a preset, or add columns to build
          your own.
        </p>
        <div className="mt-8">{presetSection}</div>
        {picker}
        <p className="mt-8 text-sm text-ink-secondary">
          Pick a preset above or add a metric column to start a report.
        </p>
      </main>
    );
  }

  const { year, modeSlug, qualifiedOnly, sort, dir, defaultSortKey, defaultDir } =
    resolved;
  const { columns, rows } = await queryReport(
    run.id,
    resolved.query,
    selectedEntries,
  );

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      {header}

      <details className="mt-6 group print:hidden" open={!!activePreset}>
        <summary className="cursor-pointer list-none text-xs text-ink-muted hover:text-ink">
          <span className="group-open:hidden">▸ Start from a preset</span>
          <span className="hidden group-open:inline">▾ Presets</span>
        </summary>
        <div className="mt-3">{presetSection}</div>
      </details>

      {picker}

      {/* Print-only cohort stamp: the controls are hidden on paper, so the
          printout names its own cohort. */}
      <p className="mt-4 hidden font-mono text-xs text-ink-secondary print:block">
        {activePreset ? `${activePreset.name} · ` : ""}
        {year ?? "All seasons"} ·{" "}
        {modeSlug ? (MODE_LABELS[modeSlug] ?? modeSlug) : "All modes combined"} ·{" "}
        {qualifiedOnly ? "qualified only" : "including small samples"} ·
        metric_layer v{run.version}
      </p>

      <form
        method="GET"
        className="mt-4 flex flex-wrap items-end gap-x-5 gap-y-3 text-sm print:hidden"
      >
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Season</span>
          <select
            name="year"
            defaultValue={year?.toString() ?? ""}
            className="border border-hairline bg-surface px-2 py-1.5"
          >
            <option value="">All covered</option>
            {scope.years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
        {scope.modes.length > 0 && (
          <label className="flex flex-col gap-1">
            <span className="text-xs text-ink-muted">Mode</span>
            <select
              name="mode"
              defaultValue={modeSlug ?? ""}
              className="border border-hairline bg-surface px-2 py-1.5"
            >
              {rankedScope.allModes && (
                <option value="">All modes combined</option>
              )}
              {scope.modes.map((m) => (
                <option key={m} value={m}>
                  {MODE_LABELS[m] ?? m}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex items-center gap-2 pb-1.5">
          <input
            type="checkbox"
            name="all"
            value="1"
            defaultChecked={!qualifiedOnly}
            className="accent-[var(--series-1)]"
          />
          <span className="text-xs text-ink-muted">
            Include below-minimum samples
          </span>
        </label>
        {/* metrics/sort/dir/per live on the URL, written client-side; carry them
            so a cohort submit does not silently drop the report. */}
        <CarryParams names={["metrics", "sort", "dir", "per"]} />
        <button
          type="submit"
          className="border border-accent-dim bg-surface-raised px-4 py-1.5 font-display text-sm font-semibold uppercase tracking-wide text-ink hover:border-accent"
        >
          Apply cohort
        </button>
      </form>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <div className="font-mono text-xs text-ink-muted">
          {columns.length} column{columns.length === 1 ? "" : "s"} ·{" "}
          {rows.length} row{rows.length === 1 ? "" : "s"} ·{" "}
          {qualifiedOnly ? "qualified only" : "including small samples"}
        </div>
        {rows.length > 0 && <ExportBar />}
      </div>

      {rows.length === 0 ? (
        <p className="mt-8 text-sm text-ink-secondary">
          No players match this cohort. The chosen columns may not cover{" "}
          {year ?? "the selected season"}
          {modeSlug ? ` in ${MODE_LABELS[modeSlug] ?? modeSlug}` : ""}. Try a
          different season or mode, or widen the sample.
        </p>
      ) : (
        <ReportTable
          columns={columns}
          rows={rows}
          showMode={modeSlug === undefined}
          initialPer={parsePer(sp)}
          initialPage={parsePage(sp)}
          initialSort={{ id: sort, dir }}
          defaultSort={{ id: defaultSortKey, dir: defaultDir }}
        />
      )}

      <p className="mt-3 max-w-3xl text-xs text-ink-muted">
        Each cell is scored within the qualified players of its own season and
        mode, so a column can qualify a player the next column does not — those
        cells are greyed, never dropped. Percentile and z-score are within that
        cohort. Full definitions are on the{" "}
        <Link href="/methodology#metrics" className="underline">
          methodology
        </Link>{" "}
        page.
      </p>
    </main>
  );
}
