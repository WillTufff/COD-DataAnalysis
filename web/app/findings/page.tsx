import type { Metadata } from "next";
import Link from "next/link";
import { FindingsFeed } from "./FindingsFeed";
import { getFeed, getFeedKinds, latestRun } from "@/lib/analytics";
import {
  type SearchParams,
  buildQuery,
  parsePage,
  parsePer,
} from "@/lib/paging";

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
  mode_null: { label: "Mode null", dot: "var(--series-8)" },
  // Metric-layer kinds.
  intangible_outlier: { label: "Split profile", dot: "var(--series-2)" },
  profile_extreme: { label: "League best", dot: "var(--series-4)" },
  clutch_milestone: { label: "Clutch record", dot: "var(--series-6)" },
  trade_asymmetry: { label: "Trade economy", dot: "var(--series-5)" },
  meta_shift: { label: "Meta shift", dot: "var(--series-7)" },
  team_style: { label: "Team style", dot: "var(--series-1)" },
};

// The feed holds every item for the current kind and pages itself client-side.
const FETCH_ALL = 100_000;

export default async function FindingsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp: SearchParams = await searchParams;
  const kindRaw = Array.isArray(sp.kind) ? sp.kind[0] : sp.kind;
  const kind = kindRaw && KIND_META[kindRaw] ? kindRaw : undefined;

  const insightsRun = await latestRun("insights");
  const kinds = insightsRun ? await getFeedKinds(insightsRun.id) : [];
  const total = kinds.reduce((s, k) => s + k.n, 0);

  const feed = insightsRun
    ? await getFeed(insightsRun.id, FETCH_ALL, kind, 0)
    : [];

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
          href={`/findings${buildQuery(sp, { kind: null, page: null })}`}
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
            href={`/findings${buildQuery(sp, { kind: k.kind, page: null })}`}
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
        <FindingsFeed
          rows={feed}
          initialPer={parsePer(sp)}
          initialPage={parsePage(sp)}
        />
      )}
    </main>
  );
}
