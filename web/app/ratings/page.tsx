import type { Metadata } from "next";
import { EloExplorer } from "@/components/charts/EloExplorer";
import { Leaderboard } from "@/components/Leaderboard";
import {
  getEloTimelines,
  getEraSpans,
  getPlayerLeaderboard,
  getTeamStandings,
  latestRun,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Ratings" };

export default async function RatingsPage() {
  const [eloRun, glickoRun, eraRun] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
    latestRun("era_adjust"),
  ]);

  if (!eloRun || !glickoRun || !eraRun) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="font-display text-4xl font-bold uppercase">Ratings</h1>
        <p className="mt-4 text-sm text-ink-secondary">
          No model runs found — run the analytics pipeline first.
        </p>
      </main>
    );
  }

  const standings = await getTeamStandings(eloRun.id, glickoRun.id);
  const topTeams = standings.slice(0, 12);
  const [timelines, leaderboard, eras] = await Promise.all([
    getEloTimelines(
      eloRun.id,
      topTeams.map((t) => t.teamId),
    ),
    getPlayerLeaderboard(eraRun.id),
    getEraSpans(),
  ]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <p className="eyebrow text-accent">
        Team strength & player form · data through {eloRun.dataThrough}
      </p>
      <h1 className="mt-1 font-display text-5xl font-bold uppercase tracking-tight">
        Ratings
      </h1>

      <section className="mt-10">
        <h2 className="eyebrow text-ink-secondary">Team Elo over time</h2>
        <div className="mt-3 rounded border border-hairline bg-surface p-4">
          <EloExplorer timelines={timelines} eras={eras} />
        </div>
      </section>

      <section className="mt-10">
        <h2 className="eyebrow text-ink-secondary">Final standings, two models</h2>
        <div className="mt-3 overflow-x-auto rounded border border-hairline bg-surface p-4">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-hairline text-xs text-ink-muted">
                <th className="py-2 pr-3 font-normal">#</th>
                <th className="py-2 pr-4 font-normal">Team</th>
                <th className="py-2 pr-4 text-right font-normal">Elo (final)</th>
                <th className="py-2 pr-4 text-right font-normal">Elo (peak)</th>
                <th className="py-2 pr-4 text-right font-normal">Glicko-2 ± RD</th>
                <th className="py-2 text-right font-normal">Rated series</th>
              </tr>
            </thead>
            <tbody>
              {standings.slice(0, 20).map((t, i) => (
                <tr key={t.teamId} className="border-b border-hairline/60">
                  <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                    {i + 1}
                  </td>
                  <td className="py-1.5 pr-4 font-medium">{t.team}</td>
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
                        <span className="text-ink-muted">
                          {" "}
                          ±{t.glickoRd?.toFixed(0)}
                        </span>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                    {t.nSeries}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-ink-muted">
            Ratings are frozen at each team’s last rated series in the archive
            (2017–2019) — teams that stopped competing earlier carry older, less
            certain numbers. Glicko-2’s ±RD makes that uncertainty explicit: a wide
            interval means the model hasn’t seen the team recently.
          </p>
        </div>
      </section>

      <section className="mt-10">
        <h2 className="eyebrow text-ink-secondary">
          Player seasons — raw vs era-adjusted
        </h2>
        <div className="mt-3 rounded border border-hairline bg-surface p-4">
          <Leaderboard rows={leaderboard} />
        </div>
      </section>
    </main>
  );
}
