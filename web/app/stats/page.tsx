import type { Metadata } from "next";
import Link from "next/link";
import { Pager } from "@/components/Pager";
import { PctlBar } from "@/components/PctlBar";
import {
  type MetricCatalogEntry,
  type MetricQuery,
  countMetric,
  getMetricCatalog,
  getMetricScope,
  latestRun,
  queryMetric,
} from "@/lib/analytics";
import {
  DEFAULT_PER,
  type SearchParams,
  clampPage,
  one,
  parsePaging,
} from "@/lib/paging";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Stat explorer" };

const MODE_LABELS: Record<string, string> = {
  hardpoint: "Hardpoint",
  "search-and-destroy": "Search & Destroy",
  control: "Control",
  "capture-the-flag": "Capture the Flag",
  uplink: "Uplink",
};

const TIER_ORDER = ["gold", "gold-fun", "standard", "fun"];

const CATEGORY_LABELS: Record<string, string> = {
  slaying: "Slaying & engagement",
  discipline: "Discipline & survival",
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

function formatValue(v: number, entry: MetricCatalogEntry): string {
  if (entry.unit.startsWith("share")) return `${(v * 100).toFixed(1)}%`;
  if (Math.abs(v) >= 100) return v.toFixed(0);
  if (Math.abs(v) >= 10) return v.toFixed(2);
  return v.toFixed(3);
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
          Stat explorer
        </h1>
        <p className="mt-4 text-sm text-ink-secondary">
          No metric run has been published yet.
        </p>
      </main>
    );
  }

  // A metric no title cleared coverage for has no rows in any season, so it is
  // not a leaderboard. It keeps its glossary entry, which explains the absence.
  const metrics = sortMetrics(catalog.metrics.filter((m) => m.titles.length > 0));
  const requested = one(sp, "metric");
  const entry =
    metrics.find((m) => m.key === requested) ?? metrics[0];

  const scope = await getMetricScope(run.id, entry.key);
  const yearRaw = Number(one(sp, "year"));
  const year = scope.years.includes(yearRaw) ? yearRaw : undefined;

  // A metric scoped to one mode has no all-modes rows, so default to its mode.
  const modeRaw = one(sp, "mode");
  const allModesAvailable = entry.modes.includes("__all__");
  const modeSlug = scope.modes.includes(modeRaw)
    ? modeRaw
    : allModesAvailable
      ? undefined
      : scope.modes[0];

  const qualifiedOnly = one(sp, "all") !== "1";
  const dir = one(sp, "dir") === "asc" ? "asc" : entry.higher_is_better ? "desc" : "asc";

  const query: MetricQuery = {
    metric: entry.key,
    year,
    modeSlug,
    qualifiedOnly,
    dir,
  };
  const total = await countMetric(run.id, query);
  const paging = clampPage(parsePaging(sp), total);
  const rows = await queryMetric(run.id, query, paging);

  const grouped = new Map<string, MetricCatalogEntry[]>();
  for (const m of metrics) {
    const label = CATEGORY_LABELS[m.category] ?? m.category;
    const list = grouped.get(label) ?? [];
    list.push(m);
    grouped.set(label, list);
  }

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        Every measured stat in the archive, era-scored · metric_layer v{run.version}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Stat explorer
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        {catalog.metrics.length} published metrics, each scored against its own
        season-and-mode cohort. Which seasons a metric covers is measured from
        the data.
      </p>

      <form
        method="GET"
        className="mt-8 flex flex-wrap items-end gap-x-5 gap-y-3 border-y border-hairline py-4 text-sm"
      >
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Metric</span>
          <select
            name="metric"
            defaultValue={entry.key}
            className="max-w-xs border border-hairline bg-surface px-2 py-1.5"
          >
            {[...grouped.entries()].map(([label, list]) => (
              <optgroup key={label} label={label}>
                {list.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                    {m.tier.startsWith("gold") ? " ★" : ""}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
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
              {allModesAvailable && <option value="">All modes combined</option>}
              {scope.modes.map((m) => (
                <option key={m} value={m}>
                  {MODE_LABELS[m] ?? m}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Order</span>
          <select
            name="dir"
            defaultValue={dir}
            className="border border-hairline bg-surface px-2 py-1.5"
          >
            <option value={entry.higher_is_better ? "desc" : "asc"}>Best first</option>
            <option value={entry.higher_is_better ? "asc" : "desc"}>Worst first</option>
          </select>
        </label>
        <label className="flex items-center gap-2 pb-1.5">
          <input
            type="checkbox"
            name="all"
            value="1"
            defaultChecked={!qualifiedOnly}
            className="accent-[var(--series-1)]"
          />
          <span className="text-xs text-ink-muted">
            Include below minimum {entry.denom_kind}
          </span>
        </label>
        {/* The row count is a link in the pager; carry it through so changing a
            filter does not silently reset it. */}
        {paging.per !== DEFAULT_PER && (
          <input type="hidden" name="per" value={paging.per} />
        )}
        <button
          type="submit"
          className="border border-accent-dim bg-surface-raised px-4 py-1.5 font-display text-sm font-semibold uppercase tracking-wide text-ink hover:border-accent"
        >
          Run query
        </button>
      </form>

      <div className="mt-4 border-l-2 border-hairline pl-3">
        <div className="font-display text-lg font-semibold">{entry.label}</div>
        <div className="mt-0.5 font-mono text-xs text-ink-secondary">
          {entry.formula}
        </div>
        <div className="mt-1 text-xs text-ink-muted">
          Qualifies at {entry.min_denom} {entry.denom_kind} · covers{" "}
          {entry.titles.join(", ") || "no season"} · {entry.unit}
          {entry.higher_is_better ? "" : " · lower is better"}
        </div>
        {entry.note && (
          <p className="mt-1.5 max-w-2xl text-xs text-ink-secondary">{entry.note}</p>
        )}
      </div>

      <div className="mt-4 font-mono text-xs text-ink-muted">
        {qualifiedOnly ? "qualified only" : "including small samples"}
      </div>

      {rows.length === 0 ? (
        <p className="mt-8 text-sm text-ink-secondary">
          No rows for this combination. This metric covers{" "}
          {entry.titles.join(", ") || "no season"}
          {modeSlug ? ` in ${MODE_LABELS[modeSlug] ?? modeSlug}` : ""}.
        </p>
      ) : (
        <>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-normal">#</th>
                  <th className="py-2 pr-4 font-normal">Player</th>
                  <th className="py-2 pr-4 font-normal">Season</th>
                  {modeSlug === undefined && (
                    <th className="py-2 pr-4 font-normal">Mode</th>
                  )}
                  <th className="py-2 pr-4 text-right font-normal">{entry.label}</th>
                  <th className="py-2 pr-4 text-right font-normal">vs cohort</th>
                  <th className="py-2 pr-4 font-normal">Percentile</th>
                  <th className="py-2 text-right font-normal">
                    {entry.denom_kind}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr
                    key={`${r.playerId}-${r.year}-${r.mode ?? "all"}`}
                    className={`border-b border-hairline/60 ${r.qualified ? "" : "text-ink-muted"}`}
                  >
                    <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                      {paging.offset + i + 1}
                    </td>
                    <td className="py-1.5 pr-4">
                      <Link
                        href={`/players/${r.slug}`}
                        className="font-medium hover:text-accent"
                      >
                        {r.handle}
                      </Link>
                      {!r.qualified && (
                        <span
                          className="ml-1.5 font-mono text-[10px] text-ink-muted"
                          title={`Below the ${entry.min_denom} ${entry.denom_kind} minimum`}
                        >
                          n low
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 pr-4 text-ink-secondary">
                      {r.year} {r.title}
                    </td>
                    {modeSlug === undefined && (
                      <td className="py-1.5 pr-4 text-ink-secondary">
                        {r.mode ? (MODE_LABELS[r.mode] ?? r.mode) : "All"}
                      </td>
                    )}
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {formatValue(r.value, entry)}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {r.z !== null
                        ? `${r.z >= 0 ? "+" : ""}${r.z.toFixed(2)}σ`
                        : "—"}
                    </td>
                    <td className="py-1.5 pr-4">
                      {r.pctl !== null ? <PctlBar pctl={r.pctl} /> : "—"}
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                      {Math.round(r.denom)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            basePath="/stats"
            searchParams={sp}
            paging={paging}
            total={total}
          />
        </>
      )}

      <p className="mt-3 max-w-3xl text-xs text-ink-muted">
        Percentile and z-score are measured within the qualified players of the same
        season and mode. Rows below the minimum sample are shown greyed when
        included, and are scored against the qualified cohort rather than
        against each other. Full definitions are on the{" "}
        <Link href="/methodology#metrics" className="underline">
          methodology
        </Link>{" "}
        page.
      </p>
    </main>
  );
}
