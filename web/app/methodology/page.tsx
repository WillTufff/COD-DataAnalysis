import type { Metadata } from "next";
import { Calibration } from "@/components/charts/Calibration";
import { getBacktestCards, getCoverage, latestRun } from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Methodology" };

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-mono text-2xl tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs text-ink-muted">{label}</div>
    </div>
  );
}

export default async function MethodologyPage() {
  const [eloRun, glickoRun, eraRun, insightsRun] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
    latestRun("era_adjust"),
    latestRun("insights"),
  ]);
  const cards = await getBacktestCards(
    [eloRun?.id, glickoRun?.id].filter((x): x is number => x !== undefined && x !== null),
  );
  const coverage = await getCoverage();

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <p className="eyebrow text-accent">How every number on this site is made</p>
      <h1 className="mt-1 font-display text-5xl font-bold uppercase tracking-tight">
        Methodology
      </h1>
      <p className="mt-3 text-sm text-ink-secondary">
        Every model output is written through a versioned, immutable run — the
        pages you’re reading always show one coherent snapshot, and every rerun
        is reproducible from the commit recorded with it. Nothing here is a
        wager aid; backtests below are educational model evaluation.
      </p>

      <section id="era" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Era adjustment
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Raw stats are not comparable across titles: league-wide engagement
            pace differs by double-digit percentages between 2017 Infinite
            Warfare, 2018 WWII, and 2019 Black Ops 4. So every player-season
            aggregate is scored <em>within its cohort</em> — all qualified
            players (≥ 8 maps) in the same season, title, and mode — as a
            z-score and percentile. A 90th-percentile season means the same
            thing in any year.
          </p>
          <p>
            The uncertainty band on career arcs is ±1.96/√maps in z-units — the
            sampling noise of a season-length average, an intentional
            approximation (it assumes unit per-map variance). Missing stats are
            never imputed: if the archive lacks a stat for a season, the cell
            shows “—” and the row’s coverage percentage says how complete the
            underlying data is.
            {eraRun && (
              <span className="text-ink-muted">
                {" "}
                Current run: v{eraRun.version}, code {eraRun.codeRef ?? "n/a"},
                data through {eraRun.dataThrough}.
              </span>
            )}
          </p>
        </div>
      </section>

      <section id="elo" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Team ratings: Elo & Glicko-2
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Two independent systems rate every decided series in chronological
            order. <strong className="text-ink">Elo</strong>: initial 1500,
            logistic expectation with scale 400, constant K=32, series-level
            (map scores ignored). <strong className="text-ink">Glicko-2</strong>{" "}
            (Glickman 2013, implemented step-for-step against the paper’s worked
            example): each series is its own rating period, τ=0.5, so rating
            deviation (RD) grows when a team is idle and shrinks with evidence —
            that’s the ±RD you see on <a className="underline" href="/ratings">/ratings</a>.
          </p>
          <p id="glicko2">
            Undecided series — where the archive is missing the deciding map —
            are never rated. Predictions are strictly walk-forward: each series
            is predicted <em>before</em> the model sees its result.
          </p>
        </div>
      </section>

      <section id="backtests" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Backtest report cards
        </h2>
        <p className="mt-2 text-sm text-ink-secondary">
          Walk-forward over every decided series in the archive. Brier and log
          loss score probability quality (lower is better; 0.25 / 0.693 is the
          coin-flip baseline). Accuracy alone can flatter a model, so read it
          with the calibration plot.
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {cards.map((c) => (
            <div key={c.runId} className="rounded border border-hairline bg-surface p-4">
              <div className="flex items-baseline justify-between">
                <h3 className="font-display text-xl font-semibold uppercase">
                  {c.model === "glicko2" ? "Glicko-2" : "Elo"}
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
                  value={c.accuracy !== null ? `${(c.accuracy * 100).toFixed(1)}%` : "—"}
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

      <section id="insights" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">Insights</h2>
        <p className="mt-3 text-sm leading-relaxed text-ink-secondary">
          Feed items are generated, not written: five kinds (outlier, trend,
          milestone, era context, head-to-head edge), each computed from model
          output or the raw record with fixed eligibility thresholds (e.g.
          outliers require ≥ 30 maps and |z| ≥ 2). Every number in a headline is
          read back from the database — nothing is estimated for narrative
          effect.
          {insightsRun && (
            <span className="text-ink-muted">
              {" "}
              Current run: v{insightsRun.version}, code {insightsRun.codeRef ?? "n/a"}.
            </span>
          )}
        </p>
      </section>

      <section id="coverage" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Coverage — what the archive actually contains
        </h2>
        <div className="mt-3 overflow-x-auto rounded border border-hairline bg-surface p-4">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-hairline text-xs text-ink-muted">
                <th className="py-2 pr-4 font-normal">Season</th>
                <th className="py-2 pr-4 text-right font-normal">Events</th>
                <th className="py-2 pr-4 text-right font-normal">Series</th>
                <th className="py-2 pr-4 text-right font-normal">Maps</th>
                <th className="py-2 pr-4 text-right font-normal">Player-map rows</th>
                <th className="py-2 pr-4 text-right font-normal">Hill time</th>
                <th className="py-2 text-right font-normal">Extended stats</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((c) => (
                <tr key={c.year} className="border-b border-hairline/60">
                  <td className="py-1.5 pr-4">
                    {c.year} <span className="text-ink-muted">{c.title}</span>
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                    {c.events}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                    {c.seriesCount}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                    {c.games}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                    {c.playerMapRows.toLocaleString()}
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                    {Math.round(c.hillTimePct * 100)}%
                  </td>
                  <td className="py-1.5 text-right font-mono tabular-nums">
                    {Math.round(c.extrasPct * 100)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-ink-muted">
            2017 covers CWL Championship only (the recovered archive begins
            there); 2018–2019 are full seasons. “Extended stats” is the share of
            player-map rows carrying the 2019-format extras (EKIA, accuracy,
            time alive, streaks…) — the earlier spreadsheets never recorded
            them, so those cells stay empty rather than estimated.
          </p>
        </div>
      </section>

      <section id="attribution" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Data & attribution
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Box scores are Activision Publishing’s official CWL data release
            (the <code className="font-mono text-xs">cwl-data</code> repository,
            BSD-3-Clause), recovered via Software Heritage after the original
            repository was taken down. Event metadata and roster context come
            from Liquipedia contributors (CC-BY-SA 3.0). Handles are normalized
            across seasons with an alias map maintained in the open — corrections
            welcome.
          </p>
        </div>
      </section>
    </main>
  );
}
