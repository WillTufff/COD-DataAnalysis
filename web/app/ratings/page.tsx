import type { Metadata } from "next";
import Link from "next/link";
import { EloExplorer } from "@/components/charts/EloExplorer";
import { Leaderboard } from "@/components/Leaderboard";
import {
  getEloTimelines,
  getEraSpans,
  getPlayerLeaderboard,
  getRatingLeaderboard,
  getTeamStandings,
  latestRun,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Ratings" };

export default async function RatingsPage() {
  const [eloRun, glickoRun, eraRun, ratingRun] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
    latestRun("era_adjust"),
    latestRun("player_rating"),
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
  const [timelines, leaderboard, eras, ratingBoard] = await Promise.all([
    getEloTimelines(
      eloRun.id,
      topTeams.map((t) => t.teamId),
    ),
    getPlayerLeaderboard(eraRun.id),
    getEraSpans(),
    ratingRun ? getRatingLeaderboard(ratingRun.id, eraRun.id) : Promise.resolve([]),
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

      {ratingBoard.length > 0 && (
        <section className="mt-10">
          <h2 className="eyebrow text-ink-secondary">
            Open player rating — top seasons
          </h2>
          <div className="mt-3 overflow-x-auto rounded border border-hairline bg-surface p-4">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-normal">#</th>
                  <th className="py-2 pr-4 font-normal">Player</th>
                  <th className="py-2 pr-4 font-normal">Season</th>
                  <th className="py-2 pr-4 text-right font-normal">Maps</th>
                  <th className="py-2 pr-4 text-right font-normal">Rating ± sd</th>
                  <th className="py-2 text-right font-normal">Raw K/D</th>
                </tr>
              </thead>
              <tbody>
                {ratingBoard.map((r, i) => (
                  <tr
                    key={`${r.playerId}-${r.year}`}
                    className="border-b border-hairline/60"
                  >
                    <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                      {i + 1}
                    </td>
                    <td className="py-1.5 pr-4 font-medium">
                      <Link
                        href={`/players/${r.slug}`}
                        className="hover:text-accent hover:underline"
                      >
                        {r.handle}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-4 text-ink-secondary">
                      {r.year} {r.title}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {r.mapsPlayed}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {r.rating.toFixed(2)}
                      {r.ratingSd !== null && (
                        <span className="text-ink-muted"> ±{r.ratingSd.toFixed(2)}</span>
                      )}
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                      {r.kdRaw !== null ? r.kdRaw.toFixed(2) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-ink-muted">
              player_rating_v1: box-score profiles weighted by what actually won
              maps in each (title × mode) — weights learned by regression, not
              chosen — shrunk toward the league mean for small samples and scaled
              so an average qualified season is 1.00. The ±sd is a map-resampling
              bootstrap. Full spec and backtest on{" "}
              <a className="underline" href="/methodology#player-rating">
                /methodology
              </a>
              ; ≥ 30 maps to appear here.
            </p>
          </div>
        </section>
      )}

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
