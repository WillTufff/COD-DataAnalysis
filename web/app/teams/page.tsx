import type { Metadata } from "next";
import Link from "next/link";
import { H2HMatrix } from "@/components/charts/H2HMatrix";
import { Sparkline } from "@/components/charts/Sparkline";
import {
  getEloTimelines,
  getH2HMatrix,
  getSeriesRecords,
  getTeamStandings,
  latestRun,
  teamSlug,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Teams" };

export default async function TeamsPage() {
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
  const allRatings = timelines.flatMap((tl) => tl.points.map((p) => p.rating));
  const sparkDomain: [number, number] = [
    Math.min(...allRatings),
    Math.max(...allRatings),
  ];

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
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-hairline text-xs text-ink-muted">
                <th className="py-2 pr-3 font-normal">#</th>
                <th className="py-2 pr-4 font-normal">Team</th>
                <th className="py-2 pr-4 font-normal">Trajectory</th>
                <th className="py-2 pr-4 text-right font-normal">Elo</th>
                <th className="py-2 pr-4 text-right font-normal">Peak</th>
                <th className="py-2 pr-4 text-right font-normal">Glicko-2 ± RD</th>
                <th className="py-2 pr-4 text-right font-normal">Series W–L</th>
                <th className="py-2 text-right font-normal">Last rated</th>
              </tr>
            </thead>
            <tbody>
              {standings.map((t, i) => {
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
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {t.peakElo.toFixed(0)}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {t.glicko !== null ? (
                        <>
                          {t.glicko.toFixed(0)}
                          <span className="text-ink-muted"> ±{t.glickoRd?.toFixed(0)}</span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {rec ? `${rec.wins}–${rec.losses}` : "—"}
                    </td>
                    <td className="py-1.5 text-right font-mono text-xs tabular-nums text-ink-muted">
                      {t.lastPlayed ? t.lastPlayed.toISOString().slice(0, 10) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
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
