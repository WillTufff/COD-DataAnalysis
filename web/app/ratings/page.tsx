import type { Metadata } from "next";
import Link from "next/link";
import { Calibration } from "@/components/charts/Calibration";
import { Leaderboard } from "@/components/Leaderboard";
import {
  getBacktestCards,
  getPlayerLeaderboard,
  getRatingLeaderboard,
  getTeamStandings,
  latestRun,
  teamSlug,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Models" };

const MODEL_LABEL: Record<string, string> = {
  elo: "Elo (series)",
  glicko2: "Glicko-2 (series)",
  winprob: "winprob_v1",
  player_rating: "player_rating_v1",
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="eyebrow text-[10px] text-ink-muted">{label}</div>
      <div className="mt-0.5 font-mono text-lg tabular-nums">{value}</div>
    </div>
  );
}

export default async function ModelsPage() {
  const [eloRun, glickoRun, eraRun, ratingRun, winprobRun] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
    latestRun("era_adjust"),
    latestRun("player_rating"),
    latestRun("winprob"),
  ]);

  if (!eloRun || !glickoRun || !eraRun) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-10">
        <h1 className="font-display text-4xl font-bold uppercase">Models</h1>
        <p className="mt-4 text-sm text-ink-secondary">
          No model runs found. Run the analytics pipeline first.
        </p>
      </main>
    );
  }

  const [standings, leaderboard, ratingBoard, seriesCards, ratingCards] =
    await Promise.all([
      getTeamStandings(eloRun.id, glickoRun.id),
      getPlayerLeaderboard(eraRun.id),
      ratingRun
        ? getRatingLeaderboard(ratingRun.id, eraRun.id)
        : Promise.resolve([]),
      getBacktestCards(
        [eloRun.id, glickoRun.id, winprobRun?.id].filter(
          (x): x is number => x !== undefined && x !== null,
        ),
      ),
      getBacktestCards(
        [ratingRun?.id].filter(
          (x): x is number => x !== undefined && x !== null,
        ),
      ),
    ]);
  const cards = [...seriesCards, ...ratingCards].sort(
    (a, b) => (a.brier ?? 1) - (b.brier ?? 1),
  );

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <p className="eyebrow text-accent">
        Every model, with its backtest · data through {eloRun.dataThrough}
      </p>
      <h1 className="mt-1 font-display text-5xl font-bold uppercase tracking-tight">
        Models
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        Two team-strength systems (Elo and Glicko-2), an open player rating
        (player_rating_v1), and a series win-probability model (winprob_v1),
        each evaluated by walk-forward backtest. Full specifications are on the{" "}
        <Link href="/methodology" className="underline">
          methodology
        </Link>{" "}
        page.
      </p>

      <section className="mt-12">
        <h2 className="eyebrow text-ink-secondary">
          Team standings, both models
        </h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-hairline text-xs text-ink-muted">
                <th className="py-2 pr-3 font-normal">#</th>
                <th className="py-2 pr-4 font-normal">Team</th>
                <th className="py-2 pr-4 text-right font-normal">Elo (final)</th>
                <th className="py-2 pr-4 text-right font-normal">Elo (peak)</th>
                <th className="py-2 pr-4 text-right font-normal">
                  Glicko-2 ± RD
                </th>
                <th className="py-2 text-right font-normal">Rated series</th>
              </tr>
            </thead>
            <tbody>
              {standings.slice(0, 15).map((t, i) => (
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
        </div>
        <p className="mt-3 text-sm">
          <Link
            href="/teams"
            className="text-accent underline underline-offset-4 hover:text-ink"
          >
            All {standings.length} teams, with trajectories and head-to-head →
          </Link>
        </p>
        <p className="mt-2 max-w-3xl text-xs text-ink-muted">
          Ratings are frozen at each team&rsquo;s last rated series in the
          archive (2017&ndash;2019), so teams that stopped competing earlier
          carry older numbers. Glicko-2&rsquo;s ±RD is that uncertainty: a wide
          interval means the model has not seen the team recently.
        </p>
      </section>

      {ratingBoard.length > 0 && (
        <section className="mt-12">
          <h2 className="eyebrow text-ink-secondary">
            player_rating_v1 · top seasons
          </h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-normal">#</th>
                  <th className="py-2 pr-4 font-normal">Player</th>
                  <th className="py-2 pr-4 font-normal">Season</th>
                  <th className="py-2 pr-4 text-right font-normal">Maps</th>
                  <th className="py-2 pr-4 text-right font-normal">
                    Rating ± sd
                  </th>
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
                        <span className="text-ink-muted">
                          {" "}
                          ±{r.ratingSd.toFixed(2)}
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                      {r.kdRaw !== null ? r.kdRaw.toFixed(2) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 max-w-3xl text-xs text-ink-muted">
            player_rating_v1 weights each box-score stat by its regression
            coefficient for winning maps in that title and mode, shrinks small
            samples toward the league mean, and scales the result so an average
            qualified season is 1.00. The ±sd comes from a map-resampling
            bootstrap. Seasons need 30 or more maps to appear here; the full
            spec is on{" "}
            <a className="underline" href="/methodology#player-rating">
              methodology
            </a>
            .
          </p>
        </section>
      )}

      <section className="mt-12">
        <h2 className="eyebrow text-ink-secondary">
          Player seasons · raw vs era-adjusted
        </h2>
        <div className="mt-3">
          <Leaderboard rows={leaderboard} />
        </div>
      </section>

      <section id="backtests" className="mt-12">
        <h2 className="eyebrow text-ink-secondary">Backtest report cards</h2>
        <p className="mt-2 max-w-3xl text-sm text-ink-secondary">
          Walk-forward over every decided series in the archive. Brier and log
          loss score probability quality (lower is better; 0.25 and 0.693 are
          the coin-flip baselines). Read accuracy alongside the calibration
          plot, since a model can be accurate while its probabilities are poorly
          calibrated.
        </p>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
          {cards.map((c) => (
            <div key={c.runId} className="border border-hairline bg-surface p-4">
              <div className="flex items-baseline justify-between">
                <h3 className="font-display text-xl font-semibold uppercase">
                  {MODEL_LABEL[c.model] ?? c.model}
                </h3>
                <span className="font-mono text-xs text-ink-muted">
                  v{c.version} · {c.windowFrom} → {c.windowTo}
                </span>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-4">
                <Stat label="Brier score" value={c.brier?.toFixed(4) ?? "—"} />
                <Stat label="Log loss" value={c.logLoss?.toFixed(4) ?? "—"} />
                <Stat
                  label="Accuracy"
                  value={
                    c.accuracy !== null
                      ? `${(c.accuracy * 100).toFixed(1)}%`
                      : "—"
                  }
                />
                <Stat label="Series predicted" value={String(c.n)} />
              </div>
              <div className="mt-4">
                <Calibration bins={c.calibration} />
              </div>
            </div>
          ))}
          {cards.length === 0 && (
            <p className="text-sm text-ink-muted">No backtests recorded yet.</p>
          )}
        </div>
      </section>
    </main>
  );
}
