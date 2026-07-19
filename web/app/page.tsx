import Link from "next/link";
import { DistributionStrip } from "@/components/charts/DistributionStrip";
import { EloExplorer } from "@/components/charts/EloExplorer";
import { PaceByMode } from "@/components/charts/PaceByMode";
import { Sparkline } from "@/components/charts/Sparkline";
import { Leaderboard } from "@/components/Leaderboard";
import {
  getArchiveStats,
  getEloTimelines,
  getEraSpans,
  getEventMarkers,
  getFeed,
  getBacktestCards,
  getPaceByMode,
  getPlayerLeaderboard,
  getSeasonKdSpread,
  getSeriesRecords,
  getTeamStandings,
  latestRun,
  teamSlug,
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
      <main className="mx-auto max-w-6xl px-6 py-10">
        <h1 className="font-display text-5xl font-bold uppercase tracking-tight">
          Competitive Call of Duty, 2017–2019
        </h1>
        <p className="mt-4 text-sm text-ink-secondary">
          No model runs found. Run the analytics pipeline (
          <code className="font-mono text-xs">
            uv run python -m cdlhub_analytics.run_all
          </code>
          ) to populate this page.
        </p>
      </main>
    );
  }

  const [
    stats,
    eras,
    events,
    pace,
    standings,
    leaderboard,
    cards,
    findings,
    records,
    kdSpread,
  ] = await Promise.all([
      getArchiveStats(),
      getEraSpans(),
      getEventMarkers(),
      getPaceByMode(),
      getTeamStandings(eloRun.id, glickoRun?.id ?? eloRun.id),
      getPlayerLeaderboard(eraRun.id),
      getBacktestCards(
        [eloRun.id, glickoRun?.id].filter((x): x is number => x != null),
      ),
      insightsRun ? getFeed(insightsRun.id, 8) : Promise.resolve([]),
      getSeriesRecords(),
      getSeasonKdSpread(eraRun.id),
    ]);
  const topTeams = standings.slice(0, 10);
  const timelines = await getEloTimelines(
    eloRun.id,
    topTeams.map((t) => t.teamId),
  );
  const sparkByTeam = new Map(timelines.map((tl) => [tl.teamId, tl.points]));
  const allSpark = timelines.flatMap((tl) => tl.points.map((p) => p.rating));
  const sparkDomain: [number, number] =
    allSpark.length > 0 ? [Math.min(...allSpark), Math.max(...allSpark)] : [1400, 1600];
  const kdAll = kdSpread.flatMap((s) => s.values);
  const kdDomain: [number, number] =
    kdAll.length > 0 ? [Math.min(...kdAll), Math.max(...kdAll)] : [0.6, 1.6];

  const fmt = (n: number) => n.toLocaleString("en-US");

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <header>
        <p className="font-mono text-xs text-ink-muted">
          CWL archive 2017–2019 · {fmt(stats.seriesCount)} series ·{" "}
          {fmt(stats.maps)} maps · {fmt(stats.statRows)} stat lines ·{" "}
          {fmt(stats.players)} players
          {eloRun.dataThrough && <> · data through {eloRun.dataThrough}</>}
        </p>
        <h1 className="mt-3 font-display text-4xl font-bold uppercase leading-[0.95] tracking-tight sm:text-6xl">
          Competitive Call of Duty
          <br />
          2017–2019
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-ink-secondary">
          Competitive Call of Duty ran on a different game each season, so raw
          stats from 2017, 2018 and 2019 don&rsquo;t compare directly. This site
          scores every player-season against its own year and mode, and rates
          teams from their series results. The ratings, the methods, and the box
          scores they run on are all published here.
        </p>
      </header>

      <section className="mt-14">
        <SectionHeader
          title="Team Elo, 2017–2019"
          note={`after every rated series · top ${topTeams.length} teams by final rating`}
        />
        <div className="mt-4">
          <EloExplorer
            timelines={timelines}
            eras={eras}
            events={events}
            height={380}
          />
        </div>
      </section>

      <section className="mt-14 grid grid-cols-1 gap-10 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <SectionHeader title="Standings" note="final Elo · full table on /teams" />
          <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-hairline text-xs text-ink-muted">
                <th className="py-2 pr-3 font-normal">#</th>
                <th className="py-2 pr-4 font-normal">Team</th>
                <th className="py-2 pr-4 font-normal">Trajectory</th>
                <th className="py-2 pr-4 text-right font-normal">Elo</th>
                <th className="py-2 text-right font-normal">Series W–L</th>
              </tr>
            </thead>
            <tbody>
              {topTeams.map((t, i) => {
                const rec = records.get(t.teamId);
                const pts = sparkByTeam.get(t.teamId);
                return (
                  <tr key={t.teamId} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                      {i + 1}
                    </td>
                    <td className="py-1.5 pr-4 font-medium">
                      <Link
                        href={`/teams/${teamSlug(t.team)}`}
                        className="hover:text-accent hover:underline"
                      >
                        {t.team}
                      </Link>
                    </td>
                    <td className="py-1 pr-4">
                      {pts && pts.length > 1 && (
                        <Sparkline
                          values={pts.map((p) => p.rating)}
                          domain={sparkDomain}
                          label={`${t.team} Elo trajectory`}
                        />
                      )}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {t.finalElo.toFixed(0)}
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                      {rec ? `${rec.wins}–${rec.losses}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
          <p className="mt-3 text-sm">
            <Link
              href="/teams"
              className="text-accent underline underline-offset-4 hover:text-ink"
            >
              All {standings.length} teams, with records and head-to-head →
            </Link>
          </p>
        </div>
        <div className="lg:col-span-2">
          <SectionHeader title="Backtests" note="walk-forward, every decided series" />
          <p className="mb-4 mt-3 text-sm leading-relaxed text-ink-secondary">
            Each system predicts every series before seeing its result, and the
            probabilities are scored afterward. A coin flip scores Brier 0.2500;
            lower is better.
          </p>
          <div className="space-y-3">
            {cards.map((c) => (
              <div
                key={c.runId}
                className="flex flex-wrap items-baseline justify-between gap-y-2 border-b border-hairline pb-3"
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
            Calibration plots and full model specs are on the{" "}
            <Link href="/methodology" className="underline hover:text-ink-secondary">
              methodology
            </Link>{" "}
            page.
          </p>
        </div>
      </section>

      <section className="mt-14">
        <SectionHeader
          title="Era adjustment"
          note="each season scored against its own cohort"
        />
        <div className="mt-6 grid grid-cols-1 gap-10 lg:grid-cols-2">
          <div>
            <p className="text-sm leading-relaxed text-ink-secondary">
              Each strip below is every qualified player-season in one title,
              plotted by raw K/D on a shared axis. The league average shifts from
              year to year, so a 1.10 K/D in 2017 and a 1.10 in 2019 are not the
              same performance.
            </p>
            <div className="mt-5 space-y-6">
              {kdSpread.map((s) => (
                <div key={s.year}>
                  <div className="eyebrow mb-1 text-[10px] text-ink-secondary">
                    {s.year} {s.title} · {s.values.length} players
                  </div>
                  <DistributionStrip
                    values={s.values}
                    domain={kdDomain}
                    unit="raw K/D"
                  />
                </div>
              ))}
            </div>
          </div>
          <div>
            <p className="mb-4 text-sm leading-relaxed text-ink-secondary">
              Engagement pace is part of the reason: kills per player per 10
              minutes moved by double-digit percentages between titles, and
              between modes within a title. So each player-season is scored
              against its own season and mode, and percentiles line up across
              years.
            </p>
            <PaceByMode cells={pace} />
          </div>
        </div>
      </section>

      <section className="mt-14">
        <SectionHeader
          title="Season leaderboard"
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
            Filter every player-season by year, mode and minimum maps →
          </Link>
        </p>
      </section>

      {findings.length > 0 && (
        <section className="mt-14">
          <SectionHeader
            title="Findings"
            note="current model run · fixed thresholds"
          />
          <ul className="mt-4 divide-y divide-hairline/60">
            {findings.map((f) => (
              <li key={f.id} className="flex items-baseline gap-4 py-2.5">
                <span className="eyebrow w-24 flex-none text-[10px] text-ink-muted">
                  {KIND_LABEL[f.kind] ?? f.kind}
                </span>
                <span className="text-sm leading-snug">{f.headline}</span>
                <Link
                  href={
                    f.subjectSlug
                      ? f.subjectType === "team"
                        ? `/teams/${f.subjectSlug}`
                        : `/players/${f.subjectSlug}`
                      : "/ratings"
                  }
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
