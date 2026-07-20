import type { Metadata } from "next";
import Link from "next/link";
import {
  getFeed,
  getFeedKinds,
  latestRun,
  type FeedItem,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Findings" };

const KIND_META: Record<string, { label: string; dot: string }> = {
  outlier: { label: "Outlier", dot: "var(--series-6)" },
  trend: { label: "Trend", dot: "var(--series-1)" },
  milestone: { label: "Milestone", dot: "var(--series-4)" },
  era_context: { label: "Era context", dot: "var(--series-7)" },
  h2h_edge: { label: "Head-to-head", dot: "var(--series-5)" },
  what_wins: { label: "What wins maps", dot: "var(--series-2)" },
  rating_top: { label: "Top rated", dot: "var(--series-3)" },
  model_null: { label: "Model null", dot: "var(--series-8)" },
  // Metric-layer kinds.
  intangible_outlier: { label: "Split profile", dot: "var(--series-2)" },
  profile_extreme: { label: "League best", dot: "var(--series-4)" },
  clutch_milestone: { label: "Clutch record", dot: "var(--series-6)" },
  trade_asymmetry: { label: "Trade economy", dot: "var(--series-5)" },
  meta_shift: { label: "Meta shift", dot: "var(--series-7)" },
  team_style: { label: "Team style", dot: "var(--series-1)" },
};

// Insight details carry the mode as its display label; /stats filters by slug.
const MODE_SLUG: Record<string, string> = {
  Hardpoint: "hardpoint",
  "Search & Destroy": "search-and-destroy",
  Control: "control",
  "Capture the Flag": "capture-the-flag",
  Uplink: "uplink",
};

/** Where a finding's evidence actually lives. Metric-backed kinds deep-link
 *  into the exact /stats leaderboard the claim was read from; the rest fall
 *  back to the subject page or the model spec. */
function evidenceHref(item: FeedItem): string {
  const d = item.detail;
  // Team findings name a team metric, which /stats does not carry — it would
  // silently fall back to the first player metric. Their evidence is the team
  // page's style section.
  const metric =
    typeof d.metric === "string" && item.subjectType !== "team" ? d.metric : null;
  if (metric) {
    const params = new URLSearchParams({ metric });
    if (typeof d.year === "number") params.set("year", String(d.year));
    const mode = typeof d.mode === "string" ? MODE_SLUG[d.mode] : undefined;
    if (mode) params.set("mode", mode);
    return `/stats?${params}`;
  }
  if (item.kind === "meta_shift") return "/meta";
  if (item.kind === "trade_asymmetry") return "/rounds";
  if (item.subjectSlug) {
    return item.subjectType === "team"
      ? `/teams/${item.subjectSlug}`
      : `/players/${item.subjectSlug}`;
  }
  if (item.kind === "what_wins") return "/methodology#player-rating";
  if (item.kind === "model_null") return "/methodology#winprob";
  return "/methodology";
}

function Chips({ detail }: { detail: Record<string, unknown> }) {
  const chips: string[] = [];
  if (typeof detail.kd_raw === "number") chips.push(`K/D ${detail.kd_raw.toFixed(2)}`);
  if (typeof detail.kd_z === "number")
    chips.push(`${detail.kd_z > 0 ? "+" : ""}${detail.kd_z.toFixed(1)}σ`);
  if (typeof detail.maps_played === "number") chips.push(`${detail.maps_played} maps`);
  if (typeof detail.career_maps === "number") chips.push(`${detail.career_maps} maps`);
  if (typeof detail.peak_elo === "number")
    chips.push(`peak ${Math.round(detail.peak_elo)}`);
  if (typeof detail.win_rate === "number" && typeof detail.n === "number")
    chips.push(`${Math.round(detail.win_rate * 100)}% over ${detail.n} series`);
  if (typeof detail.pct_change === "number")
    chips.push(
      `${detail.pct_change > 0 ? "+" : ""}${Math.round(detail.pct_change * 100)}% pace`,
    );
  if (typeof detail.rating === "number" && typeof detail.rating_sd === "number")
    chips.push(`${detail.rating.toFixed(2)} ±${detail.rating_sd.toFixed(2)}`);
  if (typeof detail.obj_vs_slay === "number")
    chips.push(`obj ${detail.obj_vs_slay.toFixed(1)}× slay`);
  if (typeof detail.n_maps === "number") chips.push(`${detail.n_maps} maps`);
  // Metric-layer kinds: the sample and the cohort position behind the claim.
  if (typeof detail.pctl === "number")
    chips.push(`${Math.round(detail.pctl * 100)}th pctl`);
  if (typeof detail.z === "number")
    chips.push(`${detail.z > 0 ? "+" : ""}${detail.z.toFixed(1)}σ`);
  if (typeof detail.n === "number") chips.push(`n=${Math.round(detail.n)}`);
  if (typeof detail.attempts === "number")
    chips.push(`${Math.round(detail.attempts)} attempts`);
  if (typeof detail.n_deaths === "number")
    chips.push(`${Math.round(detail.n_deaths)} deaths`);
  if (typeof detail.swing === "number")
    chips.push(`${detail.swing > 0 ? "+" : ""}${Math.round(detail.swing * 100)} pts share`);
  if (chips.length === 0) return null;
  return (
    <span className="ml-3 space-x-2 font-mono text-[11px] text-ink-muted">
      {chips.map((c) => (
        <span key={c}>{c}</span>
      ))}
    </span>
  );
}

export default async function FindingsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const kindRaw = Array.isArray(sp.kind) ? sp.kind[0] : sp.kind;
  const kind = kindRaw && KIND_META[kindRaw] ? kindRaw : undefined;

  const insightsRun = await latestRun("insights");
  const [feed, kinds] = insightsRun
    ? await Promise.all([
        getFeed(insightsRun.id, 200, kind),
        getFeedKinds(insightsRun.id),
      ])
    : [[], []];
  const total = kinds.reduce((s, k) => s + k.n, 0);

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        {total} findings computed at fixed thresholds
        {insightsRun?.dataThrough && <> · data through {insightsRun.dataThrough}</>}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Findings
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        What the current model run flagged, ranked by how far each item sits
        from its cohort. Every line links to its evidence.
      </p>

      <div className="mt-8 flex flex-wrap gap-2 border-y border-hairline py-3 text-xs">
        <Link
          href="/findings"
          className={`border px-2.5 py-1 transition-colors ${
            !kind
              ? "border-accent-dim bg-surface-raised text-ink"
              : "border-hairline text-ink-muted hover:text-ink-secondary"
          }`}
        >
          All ({total})
        </Link>
        {kinds.map((k) => (
          <Link
            key={k.kind}
            href={`/findings?kind=${k.kind}`}
            className={`flex items-center gap-1.5 border px-2.5 py-1 transition-colors ${
              kind === k.kind
                ? "border-accent-dim bg-surface-raised text-ink"
                : "border-hairline text-ink-muted hover:text-ink-secondary"
            }`}
          >
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: KIND_META[k.kind]?.dot ?? "var(--ink-muted)" }}
            />
            {KIND_META[k.kind]?.label ?? k.kind} ({k.n})
          </Link>
        ))}
      </div>

      {feed.length === 0 ? (
        <p className="mt-10 text-sm text-ink-secondary">
          No findings in the current run. Run the analytics pipeline (
          <code className="font-mono text-xs">
            uv run python -m cdlhub_analytics.run_all
          </code>
          ) to generate them.
        </p>
      ) : (
        <ol className="mt-2 divide-y divide-hairline/60">
          {feed.map((item) => (
            <li key={item.id} className="py-3">
              <div className="flex items-baseline gap-4">
                <span className="eyebrow w-24 flex-none text-[10px] text-ink-muted">
                  {KIND_META[item.kind]?.label ?? item.kind}
                </span>
                <p className="text-sm leading-snug">
                  {item.headline}
                  <Chips detail={item.detail} />
                </p>
                <span className="ml-auto flex flex-none items-baseline gap-3">
                  {item.subjectSlug && (
                    <Link
                      href={
                        item.subjectType === "team"
                          ? `/teams/${item.subjectSlug}`
                          : `/players/${item.subjectSlug}`
                      }
                      className="font-mono text-xs text-ink-muted underline underline-offset-2 hover:text-ink"
                    >
                      {item.subjectType === "team" ? "team" : "player"}
                    </Link>
                  )}
                  <Link
                    href={evidenceHref(item)}
                    className="font-mono text-xs text-accent underline underline-offset-2 hover:text-ink"
                  >
                    evidence
                  </Link>
                </span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </main>
  );
}
