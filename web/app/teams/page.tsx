import type { Metadata } from "next";
import { H2HMatrix } from "@/components/charts/H2HMatrix";
import { SeasonChip } from "./SeasonChip";
import { type StandingRow, StandingsTable } from "./StandingsTable";
import {
  formatLeagueSpans,
  getEloTimelines,
  getH2HMatrix,
  getLeagueSpans,
  getSeasonEras,
  getSeasonStandings,
  getSeriesRecords,
  getTeamStandings,
  latestRun,
  teamSlug,
} from "@/lib/analytics";
import { type SearchParams, one, parsePage, parsePer } from "@/lib/paging";

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

  const seasons = await getSeasonEras();
  const current = seasons[seasons.length - 1].year;
  // No param is the current season; `all` is the whole archive.
  const raw = one(sp, "season");
  const year =
    raw === "all"
      ? null
      : (seasons.find((s) => String(s.year) === raw)?.year ?? current);
  const season = seasons.find((s) => s.year === year) ?? null;

  let rows: StandingRow[];
  let minSeries = 0;
  let dataThrough = eloRun.dataThrough;
  let spans = "";
  if (year !== null) {
    const table = await getSeasonStandings(eloRun.id, glickoRun?.id ?? eloRun.id, year);
    minSeries = table.minSeries;
    rows = table.teams.map((t) => ({
      teamId: t.teamId,
      team: t.team,
      slug: teamSlug(t.team),
      finalElo: t.endElo,
      peakElo: t.peakElo,
      delta: t.endElo - t.startElo,
      glicko: t.glicko,
      glickoRd: t.glickoRd,
      rec: { wins: t.wins, losses: t.losses },
      spark: t.spark.length > 1 ? t.spark : null,
      lastPlayedIso: t.lastPlayed ? t.lastPlayed.toISOString() : null,
    }));
    const last = rows.map((r) => r.lastPlayedIso).filter(Boolean).sort().pop();
    if (year !== current && last) dataThrough = last.slice(0, 10);
  } else {
    const standings = await getTeamStandings(eloRun.id, glickoRun?.id ?? eloRun.id);
    const [records, timelines, leagueSpans] = await Promise.all([
      getSeriesRecords(),
      getEloTimelines(
        eloRun.id,
        standings.map((t) => t.teamId),
      ),
      getLeagueSpans(),
    ]);
    spans = formatLeagueSpans(leagueSpans);
    const sparkByTeam = new Map(timelines.map((tl) => [tl.teamId, tl.points]));
    rows = standings.map((t) => {
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
  }

  // The domain spans every row, not just the visible page, so the trajectory
  // column stays on one scale as the reader moves between pages.
  const allRatings = rows.flatMap((r) => r.spark ?? []);
  const sparkDomain: [number, number] = [
    Math.min(...allRatings),
    Math.max(...allRatings),
  ];
  const h2hTeams = rows.slice(0, year !== null ? 12 : 8);
  const h2hCells = await getH2HMatrix(
    h2hTeams.map((t) => t.teamId),
    year ?? undefined,
  );

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        {season ? (
          <>
            {rows.length} teams · {season.league} {season.year} · {season.title}
          </>
        ) : (
          <>
            {rows.length} rated teams · {spans}
          </>
        )}
        {dataThrough && <> · data through {dataThrough}</>}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Teams
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        {season ? (
          <>
            Every team with at least {minSeries} rated series in the{" "}
            {season.year} season, ranked by Elo after its last series of the
            season. Season ± is the change from its first series.
          </>
        ) : (
          <>
            Every team with a rated series in the archive, ranked by final Elo.
            The trajectory column plots the team&rsquo;s full rating path on a
            shared scale.
          </>
        )}
      </p>

      <div className="mt-6">
        <SeasonChip seasons={seasons} current={current} picked={year} />
      </div>

      <section className="mt-4">
        <StandingsTable
          key={year ?? "all"}
          rows={rows}
          sparkDomain={sparkDomain}
          season={season !== null}
          initialPer={parsePer(sp, season ? 20 : 10)}
          initialPage={parsePage(sp)}
        />
        <p className="mt-2 text-xs text-ink-muted">
          {season ? (
            <>
              Ratings carry over between seasons, so a team&rsquo;s Elo here
              includes everything before the season started. The Glicko-2 ±RD
              is the team&rsquo;s value after its last series of the season.
            </>
          ) : (
            <>
              Ratings freeze at each team&rsquo;s last rated series, so teams
              that left the league early carry older numbers. The Glicko-2 ±RD
              widens with inactivity, so a wide interval flags exactly those
              teams.
            </>
          )}
        </p>
      </section>

      <section className="mt-14">
        <h2 className="lower-third">
          Head to head
          <span className="lt-note">
            {season
              ? `top ${h2hTeams.length} · ${season.year} decided series only`
              : "top 8 by final Elo · decided series only"}
          </span>
        </h2>
        <div className="mt-4">
          <H2HMatrix
            teams={h2hTeams.map((t) => ({ teamId: t.teamId, team: t.team }))}
            cells={h2hCells}
          />
        </div>
      </section>
    </main>
  );
}
