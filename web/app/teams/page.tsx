import type { Metadata } from "next";
import { H2HMatrix } from "@/components/charts/H2HMatrix";
import { StandingsTable } from "./StandingsTable";
import {
  getEloTimelines,
  getH2HMatrix,
  getSeriesRecords,
  getTeamStandings,
  latestRun,
  teamSlug,
} from "@/lib/analytics";
import { type SearchParams, parsePage, parsePer } from "@/lib/paging";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Teams" };

export default async function TeamsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp: SearchParams = await searchParams;
  const [eloRun, glickoRun] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
  ]);
  if (!eloRun) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-10">
        <h1 className="font-display text-5xl font-bold uppercase tracking-tight">
          Teams
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

  const standings = await getTeamStandings(eloRun.id, glickoRun?.id ?? eloRun.id);
  const [records, timelines, h2hCells] = await Promise.all([
    getSeriesRecords(),
    getEloTimelines(
      eloRun.id,
      standings.map((t) => t.teamId),
    ),
    getH2HMatrix(standings.slice(0, 8).map((t) => t.teamId)),
  ]);
  const sparkByTeam = new Map(timelines.map((tl) => [tl.teamId, tl.points]));
  // The domain spans every team, not just the visible page, so the trajectory
  // column stays on one scale as the reader moves between pages.
  const allRatings = timelines.flatMap((tl) => tl.points.map((p) => p.rating));
  const sparkDomain: [number, number] = [
    Math.min(...allRatings),
    Math.max(...allRatings),
  ];

  // Standings are one query for all rated teams and feed the head-to-head
  // matrix as well; the table below pages the full set in the browser.
  const standingRows = standings.map((t) => {
    const rec = records.get(t.teamId);
    const pts = sparkByTeam.get(t.teamId);
    return {
      teamId: t.teamId,
      team: t.team,
      slug: teamSlug(t.team),
      finalElo: t.finalElo,
      peakElo: t.peakElo,
      glicko: t.glicko,
      glickoRd: t.glickoRd,
      rec: rec ? { wins: rec.wins, losses: rec.losses } : null,
      spark: pts && pts.length > 1 ? pts.map((p) => p.rating) : null,
      lastPlayedIso: t.lastPlayed ? t.lastPlayed.toISOString() : null,
    };
  });

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        {standings.length} rated teams · CWL 2017–2019
        {eloRun.dataThrough && <> · data through {eloRun.dataThrough}</>}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Teams
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        Every team with a rated series in the archive, ranked by final Elo. The
        trajectory column plots the team&rsquo;s full rating path on a shared
        scale.
      </p>

      <section className="mt-8">
        <StandingsTable
          rows={standingRows}
          sparkDomain={sparkDomain}
          initialPer={parsePer(sp)}
          initialPage={parsePage(sp)}
        />
        <p className="mt-2 text-xs text-ink-muted">
          Ratings freeze at each team&rsquo;s last rated series, so teams that
          left the league early carry older numbers. The Glicko-2 ±RD widens
          with inactivity, so a wide interval flags exactly those teams.
        </p>
      </section>

      <section className="mt-14">
        <h2 className="lower-third">
          Head to head
          <span className="lt-note">top 8 by final Elo · decided series only</span>
        </h2>
        <div className="mt-4">
          <H2HMatrix
            teams={standings.slice(0, 8).map((t) => ({ teamId: t.teamId, team: t.team }))}
            cells={h2hCells}
          />
        </div>
      </section>
    </main>
  );
}
