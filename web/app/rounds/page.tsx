import type { Metadata } from "next";
import Link from "next/link";
import {
  type ClutchByN,
  type DensityMap,
  type RoundsGroup,
  getKillDensity,
  getRoundsOverview,
  latestRun,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Rounds & kill feed" };

const MODE_LABELS: Record<string, string> = {
  hardpoint: "Hardpoint",
  "search-and-destroy": "Search & Destroy",
  uplink: "Uplink",
  "capture-the-flag": "Capture the Flag",
};

function one(sp: Record<string, string | string[] | undefined>, k: string): string {
  const v = sp[k];
  return (Array.isArray(v) ? v[0] : v) ?? "";
}

function pct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}

/** Horizontal win-rate bar for one clutch tier, with the W-L count in the label. */
function ClutchBar({ n, wins, attempts, rate }: ClutchByN) {
  const W = 200;
  const w = rate === null ? 0 : Math.max(rate > 0 ? 2 : 0, rate * W);
  return (
    <div className="flex items-center gap-3">
      <div className="w-10 font-mono text-xs text-ink-secondary tabular-nums">1v{n}</div>
      <svg width={W} height={16} viewBox={`0 0 ${W} 16`} role="img" aria-label={`1v${n}: ${pct(rate)} won`}>
        <rect x={0} y={4} width={W} height={8} rx={2} fill="var(--baseline)" />
        {w > 0 && <rect x={0} y={4} width={w} height={8} rx={2} fill="var(--series-1)" />}
      </svg>
      <div className="w-14 text-right font-mono text-xs tabular-nums">{pct(rate)}</div>
      <div className="w-16 font-mono text-xs tabular-nums text-ink-muted">
        {wins}–{attempts - wins}
      </div>
    </div>
  );
}

/** Time-to-first-blood histogram: share of rounds whose opening kill fell in each band. */
function TtfbChart({ counts, edges }: { counts: number[]; edges: number[] }) {
  const total = counts.reduce((a, b) => a + b, 0) || 1;
  const shares = counts.map((c) => c / total);
  const peak = Math.max(...shares, 0.0001);
  const W = 320;
  const H = 120;
  const bw = W / counts.length;
  return (
    <svg width={W} height={H + 22} viewBox={`0 0 ${W} ${H + 22}`} role="img" aria-label="Time to first blood distribution">
      {shares.map((s, i) => {
        const h = (s / peak) * H;
        const label = i === edges.length - 2 ? `${edges[i]}s+` : `${edges[i]}–${edges[i + 1]}`;
        return (
          <g key={i}>
            <rect x={i * bw + 2} y={H - h} width={bw - 4} height={h} rx={2} fill="var(--series-5)" />
            <text
              x={i * bw + bw / 2}
              y={H + 14}
              textAnchor="middle"
              className="fill-ink-muted"
              fontSize={8}
              fontFamily="var(--font-mono, monospace)"
            >
              {label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

/** Kill-density heatmap: one warm hue, density mapped to brightness (sequential). */
function DensityGrid({ m }: { m: DensityMap }) {
  const cell = 9;
  const size = m.bins * cell;
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={`${m.map}: ${m.n_kills} kills, density`}
      className="border border-hairline"
    >
      <rect x={0} y={0} width={size} height={size} fill="var(--surface-raised)" />
      {m.grid.flatMap((row, gy) =>
        row.map((count, gx) => {
          if (count === 0) return null;
          // sqrt scaling spreads the skewed density; opacity is the sequential ramp.
          const r = Math.sqrt(count / m.peak);
          return (
            <rect
              key={`${gx}-${gy}`}
              x={gx * cell}
              // grid[y][x] with y=0 at the top; flip so higher y draws upward
              y={(m.bins - 1 - gy) * cell}
              width={cell}
              height={cell}
              fill="var(--series-6)"
              fillOpacity={0.12 + 0.88 * r}
            />
          );
        }),
      )}
    </svg>
  );
}

export default async function RoundsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const run = await latestRun("metric_layer");
  const overview = run ? await getRoundsOverview(run.id) : null;
  const density = run ? await getKillDensity(run.id) : null;

  if (!run || !overview || overview.groups.length === 0) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-12">
        <h1 className="font-display text-5xl font-bold uppercase tracking-tight">
          Rounds &amp; kill feed
        </h1>
        <p className="mt-4 text-sm text-ink-secondary">
          No kill-feed run has been published yet. This layer covers the 2017 and 2018
          seasons; Black Ops 4 (2019) shipped box scores but no event feed.
        </p>
      </main>
    );
  }

  // Group picker: title + mode. Default to the first Search &amp; Destroy group
  // (it carries the advantage and clutch panels).
  const groupKey = (g: RoundsGroup) => `${g.title}:${g.mode}`;
  const requested = one(sp, "g");
  const group =
    overview.groups.find((g) => groupKey(g) === requested) ??
    overview.groups.find((g) => g.mode === "search-and-destroy") ??
    overview.groups[0];

  const densityTitles = density ? [...new Set(density.maps.map((m) => m.title))] : [];
  const densityTitle = densityTitles.includes(one(sp, "dt")) ? one(sp, "dt") : densityTitles[0];
  const densityMaps = density
    ? density.maps.filter((m) => m.title === densityTitle)
    : [];

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        The 2017–2018 kill feed, reconciled against the box score · metric_layer v{run.version}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Rounds &amp; kill feed
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        What the event feed adds under the box score: who trades, who clutches, and how
        a man advantage converts. Infinite Warfare (2017) and WWII (2018) only — Black
        Ops 4 has box scores but no feed, so it does not appear here. A death counts as
        traded when a teammate answers the killer within{" "}
        {(overview.trade_window_ms / 1000).toFixed(0)} seconds.
      </p>

      <form method="GET" className="mt-8 flex flex-wrap items-end gap-x-5 gap-y-3 border-y border-hairline py-4 text-sm">
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Season &amp; mode</span>
          <select name="g" defaultValue={groupKey(group)} className="border border-hairline bg-surface px-2 py-1.5">
            {overview.groups.map((g) => (
              <option key={groupKey(g)} value={groupKey(g)}>
                {g.year} {g.title} · {MODE_LABELS[g.mode] ?? g.mode}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="border border-accent-dim bg-surface-raised px-4 py-1.5 font-display text-sm font-semibold uppercase tracking-wide text-ink hover:border-accent"
        >
          View
        </button>
      </form>

      <div className="mt-2 font-mono text-xs text-ink-muted">
        {group.year} {group.title} · {MODE_LABELS[group.mode] ?? group.mode} ·{" "}
        {group.deaths.toLocaleString()} reconciled deaths
      </div>

      <div className="mt-8 grid grid-cols-1 gap-x-12 gap-y-10 md:grid-cols-2">
        {/* Trade window */}
        <section>
          <h2 className="lower-third">Trade window</h2>
          <div className="mt-3 flex items-baseline gap-3">
            <div className="font-display text-4xl font-bold tabular-nums">
              {pct(group.traded_share)}
            </div>
            <div className="text-sm text-ink-secondary">of deaths traded back</div>
          </div>
          <p className="mt-2 max-w-md text-xs text-ink-muted">
            Share of deaths a teammate answered within{" "}
            {(overview.trade_window_ms / 1000).toFixed(0)} s. The rest are untraded — the
            kills that actually cost a numbers advantage.
          </p>
        </section>

        {/* Advantage state */}
        {group.advantage && (
          <section>
            <h2 className="lower-third">Man advantage</h2>
            <div className="mt-3 flex gap-10">
              <div>
                <div className="font-display text-4xl font-bold tabular-nums">
                  {pct(group.advantage.adv_conversion)}
                </div>
                <div className="mt-1 text-xs text-ink-secondary">
                  conversion — rounds won after first blood
                </div>
              </div>
              <div>
                <div className="font-display text-4xl font-bold tabular-nums text-series-3">
                  {pct(group.advantage.disadv_steal)}
                </div>
                <div className="mt-1 text-xs text-ink-secondary">
                  steal — rounds won a man down
                </div>
              </div>
            </div>
            <p className="mt-2 max-w-md text-xs text-ink-muted">
              Over {group.advantage.adv_rounds.toLocaleString()} rounds decided by an
              opening pick. The two rates sum to 100% — every such round is one side&rsquo;s
              conversion or the other&rsquo;s steal.
            </p>
          </section>
        )}

        {/* Clutch by N */}
        {group.clutch && (
          <section>
            <h2 className="lower-third">Clutch success</h2>
            <p className="mt-2 max-w-md text-xs text-ink-muted">
              Win rate as the last player alive, by how many opponents remained. W–L to
              the right.
            </p>
            <div className="mt-3 flex flex-col gap-2">
              {group.clutch.map((c) => (
                <ClutchBar key={c.n} {...c} />
              ))}
            </div>
          </section>
        )}

        {/* TTFB */}
        <section>
          <h2 className="lower-third">Time to first blood</h2>
          <p className="mt-2 max-w-md text-xs text-ink-muted">
            When the round&rsquo;s opening kill lands, as a share of rounds.
          </p>
          <div className="mt-3">
            <TtfbChart counts={group.ttfb} edges={overview.ttfb_edges_s} />
          </div>
        </section>
      </div>

      {/* Kill density */}
      {density && densityMaps.length > 0 && (
        <section className="mt-14">
          <h2 className="lower-third">Kill density by map</h2>
          <p className="mt-2 max-w-2xl text-sm text-ink-secondary">
            Where kills land, as a heatmap of the feed&rsquo;s victim positions, normalized
            per map. Brighter is denser. Axes and density only — no game-map imagery.
            Orientation is the feed&rsquo;s coordinate space, not a fixed compass.
          </p>
          <form method="GET" className="mt-4 flex items-end gap-3 text-sm">
            <input type="hidden" name="g" value={groupKey(group)} />
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">Title</span>
              <select name="dt" defaultValue={densityTitle} className="border border-hairline bg-surface px-2 py-1.5">
                {densityTitles.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              className="border border-accent-dim bg-surface-raised px-4 py-1.5 font-display text-sm font-semibold uppercase tracking-wide text-ink hover:border-accent"
            >
              Show
            </button>
          </form>
          <div className="mt-6 flex flex-wrap gap-8">
            {densityMaps.map((m) => (
              <figure key={m.map}>
                <DensityGrid m={m} />
                <figcaption className="mt-1.5 text-xs">
                  <span className="font-medium">{m.map}</span>
                  <span className="ml-2 font-mono text-ink-muted tabular-nums">
                    {m.n_kills.toLocaleString()} kills
                  </span>
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}

      <p className="mt-12 max-w-3xl text-xs text-ink-muted">
        Only player-maps whose feed death count reconciles with the box score feed these
        panels; the residual is excluded, not patched. Full definitions are on the{" "}
        <Link href="/methodology#rounds" className="underline">
          methodology
        </Link>{" "}
        page.
      </p>
    </main>
  );
}
