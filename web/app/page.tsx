import Link from "next/link";
import { EloExplorer } from "@/components/charts/EloExplorer";
import { PaceByMode } from "@/components/charts/PaceByMode";
import { Leaderboard } from "@/components/Leaderboard";
import {
  getArchiveStats,
  getEloTimelines,
  getEraSpans,
  getFeed,
  getBacktestCards,
  getPaceByMode,
  getPlayerLeaderboard,
  getTeamStandings,
  latestRun,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

const KIND_LABEL: Record<string, string> = {
  outlier: "Outlier",
  trend: "Trend",
  milestone: "Milestone",
  era_context: "Era context",
  h2h_edge: "Head-to-head",
};

function SectionHeader({ title, note }: { title: string; note?: string }) {
  return (
    <h2 className="lower-third">
      {title}
      {note && <span className="lt-note">{note}</span>}
    </h2>
  );
}

export default async function Home() {
  const [eloRun, glickoRun, eraRun, insightsRun] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
    latestRun("era_adjust"),
    latestRun("insights"),
  ]);

  if (!eloRun || !eraRun) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="font-display text-5xl font-bold uppercase tracking-tight">
          Three games. One league.
        </h1>
        <p className="mt-4 text-sm text-ink-secondary">
          No model runs found — run the analytics pipeline (
          <code className="font-mono text-xs">
            uv run python -m cdlhub_analytics.run_all
          </code>
          ) to populate this page.
        </p>
      </main>
    );
  }

  const [stats, eras, pace, standings, leaderboard, cards, findings] =
    await Promise.all([
      getArchiveStats(),
      getEraSpans(),
      getPaceByMode(),
      getTeamStandings(eloRun.id, glickoRun?.id ?? eloRun.id),
      getPlayerLeaderboard(eraRun.id),
      getBacktestCards(
        [eloRun.id, glickoRun?.id].filter((x): x is number => x != null),
      ),
      insightsRun ? getFeed(insightsRun.id, 8) : Promise.resolve([]),
    ]);
  const topTeams = standings.slice(0, 10);
  const timelines = await getEloTimelines(
    eloRun.id,
    topTeams.map((t) => t.teamId),
  );

  const fmt = (n: number) => n.toLocaleString("en-US");

  return (
    <main className="mx-auto max-w-5xl px-6 py-12">
      {/* ---- Thesis ---- */}
      <header>
        <p className="font-mono text-xs text-ink-muted">
          CWL archive 2017–2019 · {fmt(stats.seriesCount)} series ·{" "}
          {fmt(stats.maps)} maps · {fmt(stats.statRows)} stat lines ·{" "}
          {fmt(stats.players)} players
          {eloRun.dataThrough && <> · data through {eloRun.dataThrough}</>}
        </p>
        <h1 className="mt-3 font-display text-6xl font-bold uppercase leading-[0.95] tracking-tight">
          Three games.
          <br />
          One league.
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-ink-secondary">
          Competitive Call of Duty changes its game every season, so its history
          can’t be read off raw stats. This site models the CWL archive —
          era-adjusted player scoring, walk-forward team ratings, backtested
          against every decided series — and every number links back to the box
          scores it came from.
        </p>
      </header>

      {/* ---- Signature: the rating race across eras ---- */}
      <section className="mt-14">
        <SectionHeader
          title="The rating race"
          note={`Elo after every rated series · top ${topTeams.length} teams by final rating`}
        />
        <div className="mt-4">
          <EloExplorer timelines={timelines} eras={eras} height={380} />
        </div>
      </section>

      {/* ---- Why adjust + does it work ---- */}
      <section className="mt-14 grid gap-10 md:grid-cols-2">
        <div>
          <SectionHeader title="Why raw stats lie" />
          <p className="mb-4 mt-3 text-sm leading-relaxed text-ink-secondary">
            A 1.10 K/D means different things in different games. League-wide
            engagement pace moved by double digits between titles — and between
            modes within a title — so every player-season here is scored against
            its own season-and-mode cohort instead.
          </p>
          <PaceByMode cells={pace} />
        </div>
        <div>
          <SectionHeader title="Do the models know anything?" />
          <p className="mb-4 mt-3 text-sm leading-relaxed text-ink-secondary">
            Every rating system is graded walk-forward: predict each series
            before seeing its result, then score the probabilities. Coin flip is
            Brier 0.2500.
          </p>
          <div className="space-y-3">
            {cards.map((c) => (
              <div
                key={c.runId}
                className="flex items-baseline justify-between border-b border-hairline pb-3"
              >
                <div>
                  <div className="font-display text-lg font-semibold uppercase">
                    {c.model === "glicko2" ? "Glicko-2" : "Elo"}
                  </div>
                  <div className="font-mono text-[11px] text-ink-muted">
                    {c.n} series · {c.windowFrom} → {c.windowTo}
                  </div>
                </div>
                <div className="flex gap-6 text-right">
                  <div>
                    <div className="font-mono text-xl tabular-nums">
                      {c.brier?.toFixed(4) ?? "—"}
                    </div>
                    <div className="text-[11px] text-ink-muted">Brier</div>
                  </div>
                  <div>
                    <div className="font-mono text-xl tabular-nums">
                      {c.accuracy !== null
                        ? `${(c.accuracy * 100).toFixed(1)}%`
                        : "—"}
                    </div>
                    <div className="text-[11px] text-ink-muted">accuracy</div>
                  </div>
                </div>
              </div>
            ))}
            {cards.length === 0 && (
              <p className="text-sm text-ink-muted">No backtests recorded yet.</p>
            )}
          </div>
          <p className="mt-3 text-xs text-ink-muted">
            Calibration plots, model specs, and coverage caveats live in{" "}
            <Link href="/methodology" className="underline hover:text-ink-secondary">
              methodology
            </Link>
            .
          </p>
        </div>
      </section>

      {/* ---- Era-adjusted seasons ---- */}
      <section className="mt-14">
        <SectionHeader
          title="The best seasons, adjusted"
          note="qualified player-seasons, ≥30 maps"
        />
        <div className="mt-4">
          <Leaderboard rows={leaderboard} limit={10} />
        </div>
        <p className="mt-3 text-sm">
          <Link
            href="/players"
            className="text-accent underline underline-offset-4 hover:text-ink"
          >
            Query every player-season — filter by year, mode, and minimum maps →
          </Link>
        </p>
      </section>

      {/* ---- Findings ledger ---- */}
      {findings.length > 0 && (
        <section className="mt-14">
          <SectionHeader
            title="Findings"
            note="model-generated, fixed thresholds — never written by hand"
          />
          <ul className="mt-4 divide-y divide-hairline/60">
            {findings.map((f) => (
              <li key={f.id} className="flex items-baseline gap-4 py-2.5">
                <span className="eyebrow w-24 flex-none text-[10px] text-ink-muted">
                  {KIND_LABEL[f.kind] ?? f.kind}
                </span>
                <span className="text-sm leading-snug">{f.headline}</span>
                <Link
                  href={f.subjectSlug ? `/players/${f.subjectSlug}` : "/ratings"}
                  className="ml-auto flex-none font-mono text-xs text-accent underline underline-offset-2 hover:text-ink"
                >
                  evidence
                </Link>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-sm">
            <Link
              href="/findings"
              className="text-accent underline underline-offset-4 hover:text-ink"
            >
              All findings →
            </Link>
          </p>
        </section>
      )}
    </main>
  );
}
