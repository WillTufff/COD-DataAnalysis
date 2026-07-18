import type { Metadata } from "next";
import Link from "next/link";
import { PctlBar } from "@/components/PctlBar";
import {
  type ExplorerFilters,
  latestRun,
  queryPlayerSeasons,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Player-season explorer" };

const SORTS: { value: ExplorerFilters["sort"]; label: string }[] = [
  { value: "kd_z", label: "K/D vs cohort (z)" },
  { value: "kd_raw", label: "K/D raw" },
  { value: "kd_pctl", label: "K/D percentile" },
  { value: "maps", label: "Maps played" },
  { value: "engagement_z", label: "Engagement (z)" },
  { value: "obj_z", label: "Objective (z)" },
];
const MODES = [
  { value: "", label: "All modes combined" },
  { value: "hardpoint", label: "Hardpoint" },
  { value: "search-and-destroy", label: "Search & Destroy" },
  { value: "control", label: "Control" },
  { value: "capture-the-flag", label: "Capture the Flag" },
  { value: "uplink", label: "Uplink" },
];
const YEARS = ["", "2017", "2018", "2019"];

function parseFilters(sp: Record<string, string | string[] | undefined>): ExplorerFilters {
  const one = (k: string) => {
    const v = sp[k];
    return (Array.isArray(v) ? v[0] : v) ?? "";
  };
  const sort = SORTS.some((s) => s.value === one("sort"))
    ? (one("sort") as ExplorerFilters["sort"])
    : "kd_z";
  const year = YEARS.includes(one("year")) && one("year") ? Number(one("year")) : undefined;
  const modeSlug = MODES.some((m) => m.value === one("mode")) && one("mode")
    ? one("mode")
    : undefined;
  const minMapsRaw = Number(one("min_maps"));
  return {
    year,
    modeSlug,
    minMaps: Number.isFinite(minMapsRaw) && minMapsRaw > 0
      ? Math.min(minMapsRaw, 10000)
      : modeSlug
        ? 8
        : 30,
    sort,
    dir: one("dir") === "asc" ? "asc" : "desc",
    q: one("q").slice(0, 40) || undefined,
    limit: 100,
  };
}

function z(v: number | null): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;
}

export default async function PlayersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const f = parseFilters(await searchParams);
  const eraRun = await latestRun("era_adjust");
  const rows = eraRun ? await queryPlayerSeasons(eraRun.id, f) : [];

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        Every qualified player-season in the archive, era-adjusted · run v
        {eraRun?.version ?? "—"}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Player-season explorer
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        Each row is one player’s season in one title, scored against its own
        cohort. Filters are the query — the URL is shareable.
      </p>

      <form
        method="GET"
        className="mt-8 flex flex-wrap items-end gap-x-5 gap-y-3 border-y border-hairline py-4 text-sm"
      >
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Season</span>
          <select
            name="year"
            defaultValue={f.year?.toString() ?? ""}
            className="rounded border border-hairline bg-surface px-2 py-1.5"
          >
            <option value="">All</option>
            <option value="2017">2017 · IW</option>
            <option value="2018">2018 · WWII</option>
            <option value="2019">2019 · BO4</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Mode</span>
          <select
            name="mode"
            defaultValue={f.modeSlug ?? ""}
            className="rounded border border-hairline bg-surface px-2 py-1.5"
          >
            {MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Min maps</span>
          <input
            type="number"
            name="min_maps"
            min={1}
            defaultValue={f.minMaps}
            className="w-20 rounded border border-hairline bg-surface px-2 py-1.5 font-mono"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Rank by</span>
          <select
            name="sort"
            defaultValue={f.sort}
            className="rounded border border-hairline bg-surface px-2 py-1.5"
          >
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Order</span>
          <select
            name="dir"
            defaultValue={f.dir}
            className="rounded border border-hairline bg-surface px-2 py-1.5"
          >
            <option value="desc">Best first</option>
            <option value="asc">Worst first</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Player</span>
          <input
            type="search"
            name="q"
            placeholder="handle…"
            defaultValue={f.q ?? ""}
            className="w-32 rounded border border-hairline bg-surface px-2 py-1.5"
          />
        </label>
        <button
          type="submit"
          className="rounded border border-accent-dim bg-surface-raised px-4 py-1.5 font-display text-sm font-semibold uppercase tracking-wide text-ink hover:border-accent"
        >
          Run query
        </button>
      </form>

      <div className="mt-2 font-mono text-xs text-ink-muted">
        {rows.length === 100 ? "first 100 rows" : `${rows.length} rows`}
        {f.modeSlug ? " · per-mode cohorts" : " · all modes combined"}
      </div>

      {rows.length === 0 ? (
        <p className="mt-8 text-sm text-ink-secondary">
          No player-seasons match. Lower the minimum maps or clear a filter —
          per-mode cohorts qualify at 8 maps, all-modes at 30.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-hairline text-xs text-ink-muted">
                <th className="py-2 pr-3 font-normal">#</th>
                <th className="py-2 pr-4 font-normal">Player</th>
                <th className="py-2 pr-4 font-normal">Season</th>
                {f.modeSlug && <th className="py-2 pr-4 font-normal">Mode</th>}
                <th className="py-2 pr-4 text-right font-normal">Maps</th>
                <th className="py-2 pr-4 text-right font-normal">K/D</th>
                <th className="py-2 pr-4 text-right font-normal">vs cohort</th>
                <th className="py-2 pr-4 font-normal">Percentile</th>
                <th className="py-2 pr-4 text-right font-normal">Engage z</th>
                <th className="py-2 text-right font-normal">Obj z</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={`${r.playerId}-${r.year}-${r.mode ?? "all"}`}
                  className="border-b border-hairline/60"
                >
                  <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                    {i + 1}
                  </td>
                  <td className="py-1.5 pr-4">
                    <Link
                      href={`/players/${r.slug}`}
                      className="font-medium hover:text-accent"
                    >
                      {r.handle}
                    </Link>
                  </td>
                  <td className="py-1.5 pr-4 text-ink-secondary">
                    {r.year} {r.title}
                  </td>
                  {f.modeSlug && (
                    <td className="py-1.5 pr-4 text-ink-secondary">{r.mode}</td>
                  )}
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                    {r.mapsPlayed}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                    {r.kdRaw?.toFixed(2) ?? "—"}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                    {r.kdZ !== null ? `${z(r.kdZ)}σ` : "—"}
                  </td>
                  <td className="py-1.5 pr-4">
                    {r.kdPctl !== null ? <PctlBar pctl={r.kdPctl} /> : "—"}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                    {z(r.engagementZ)}
                  </td>
                  <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                    {z(r.objZ)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-3 text-xs text-ink-muted">
        z-scores are standard deviations from the qualified-player mean of the
        same season, title{f.modeSlug ? ", and mode" : ""}; percentile is within
        that cohort. Missing archive stats stay “—”, never imputed — see{" "}
        <Link href="/methodology#era" className="underline">
          methodology
        </Link>
        .
      </p>
    </main>
  );
}
