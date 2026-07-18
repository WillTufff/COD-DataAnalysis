import type { Metadata } from "next";
import { Calibration } from "@/components/charts/Calibration";
import { WhatWinsMaps } from "@/components/charts/WhatWinsMaps";
import {
  getBacktestCards,
  getCoverage,
  getModeWeights,
  getWinprobArtifact,
  latestRun,
} from "@/lib/analytics";

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

const MODEL_LABEL: Record<string, string> = {
  elo: "Elo",
  glicko2: "Glicko-2",
  winprob: "winprob_v1",
  player_rating: "player_rating_v1",
};

export default async function MethodologyPage() {
  const [eloRun, glickoRun, eraRun, insightsRun, ratingRun, winprobRun] =
    await Promise.all([
      latestRun("elo"),
      latestRun("glicko2"),
      latestRun("era_adjust"),
      latestRun("insights"),
      latestRun("player_rating"),
      latestRun("winprob"),
    ]);
  const seriesCards = await getBacktestCards(
    [eloRun?.id, glickoRun?.id, winprobRun?.id].filter(
      (x): x is number => x !== undefined && x !== null,
    ),
  );
  const [ratingCards, modeWeights, winprobArt, coverage] = await Promise.all([
    getBacktestCards(
      [ratingRun?.id].filter((x): x is number => x !== undefined && x !== null),
    ),
    ratingRun ? getModeWeights(ratingRun.id) : Promise.resolve([]),
    winprobRun ? getWinprobArtifact(winprobRun.id) : Promise.resolve(null),
    getCoverage(),
  ]);
  const cards = [...seriesCards].sort(
    (a, b) => (a.brier ?? 1) - (b.brier ?? 1),
  );

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

      <section id="player-rating" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Open player rating (player_rating_v1)
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            The composite rating shown on{" "}
            <a className="underline" href="/ratings">
              /ratings
            </a>{" "}
            is built in four auditable steps. <strong className="text-ink">First</strong>,
            for every (season × mode) each map becomes one observation — the
            difference between the two teams’ per-10-minute stat profiles (kills,
            deaths, assists, mode objective), standardized and regressed against
            which team won the map (L2 logistic, λ=1, fit by IRLS in ~40 lines of
            published numpy). The coefficients are data-derived answers to “how
            much was a one-SD edge in hill time worth against the same edge in
            kills, in this title?” <strong className="text-ink">Second</strong>,
            each player-season-mode aggregate is z-scored within its qualified
            cohort and dotted with those weights.{" "}
            <strong className="text-ink">Third</strong>, scores shrink toward the
            league mean by m/(m+15) in maps played — partial pooling, so a hot
            12-map cameo cannot outrank a great 200-map season.{" "}
            <strong className="text-ink">Fourth</strong>, mode scores blend by
            maps played and scale so the qualified league averages 1.00; the ±sd
            is a 200-draw map-resampling bootstrap.
          </p>
          <p>
            One reading caveat: in respawn modes a team’s kills mirror its
            opponent’s deaths almost exactly, so those two coefficients are
            near-collinear and the ridge penalty splits their shared weight — read
            them jointly as slaying.
            {ratingRun && (
              <span className="text-ink-muted">
                {" "}
                Current run: v{ratingRun.version}, code {ratingRun.codeRef ?? "n/a"},
                data through {ratingRun.dataThrough}.
              </span>
            )}
          </p>
        </div>
        {modeWeights.length > 0 && (
          <div className="mt-4 rounded border border-hairline bg-surface p-4">
            <h3 className="eyebrow text-ink-secondary">
              What the regression learned: what wins maps
            </h3>
            <div className="mt-3">
              <WhatWinsMaps cohorts={modeWeights} />
            </div>
          </div>
        )}
        {ratingCards.map((c) => (
          <div key={c.runId} className="mt-4 rounded border border-hairline bg-surface p-4">
            <div className="flex items-baseline justify-between">
              <h3 className="font-display text-xl font-semibold uppercase">
                Weight validation — walk-forward by event
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
              <Stat label="Maps predicted" value={String(c.n)} />
            </div>
            <p className="mt-3 text-xs text-ink-muted">
              Each event’s maps are classified using weights trained only on
              earlier events in the same (season × mode). Read this for what it
              is: a <em>value</em> model scored on same-map box scores, not a
              forecast — it establishes that the learned weights generalize
              across events instead of memorizing them, which is the property the
              rating stands on.
            </p>
          </div>
        ))}
      </section>

      <section id="winprob" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          The momentum test (winprob_v1)
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Glicko-2 is the strongest baseline below, so instead of another
            rating system this model asks a sharper question: <em>given</em> the
            ratings, does anything else carry information about who wins a
            series? Its features — all computed strictly before each series — are
            the walk-forward Glicko-2 and Elo probabilities, combined rating
            deviation, each team’s last-{winprobArt?.formWindow ?? 10} win rate,
            and a shrunken head-to-head record, in an expanding-window logistic
            regression refit every {winprobArt?.refitEvery ?? 50} series. Until{" "}
            {winprobArt?.minTrain ?? 200} series of history exist it passes
            Glicko-2 through unchanged, so its backtest is directly comparable.
          </p>
          <p>
            The answer over 2017–2019 is no: the Brier difference in the report
            cards below is noise, and the learned form coefficient is
            approximately zero. Recent form and head-to-head history add nothing
            beyond team strength — the first published test of the momentum
            narrative on this record. A null, backtested and reported, is a
            result.
            {winprobRun && (
              <span className="text-ink-muted">
                {" "}
                Current run: v{winprobRun.version}, code {winprobRun.codeRef ?? "n/a"}.
              </span>
            )}
          </p>
          {winprobArt && (
            <div className="overflow-x-auto rounded border border-hairline bg-surface p-4">
              <h3 className="eyebrow text-ink-secondary">
                Final learned coefficients (log-odds per unit)
              </h3>
              <table className="mt-2 w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-1.5 pr-4 font-normal">Feature</th>
                    <th className="py-1.5 text-right font-normal">Weight</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(winprobArt.finalWeights).map(([f, w]) => (
                    <tr key={f} className="border-b border-hairline/60">
                      <td className="py-1.5 pr-4 font-mono text-xs">{f}</td>
                      <td className="py-1.5 text-right font-mono tabular-nums">
                        {w.toFixed(3)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-ink-muted">
                The rating logits carry the prediction; the rest hover near zero.
                Coefficients are correlated (both rating systems measure the same
                thing), so read the non-rating rows as “nothing left to add,” not
                as precise effects.
              </p>
            </div>
          )}
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
          Feed items are generated, not written: eight kinds (outlier, trend,
          milestone, era context, head-to-head edge, what-wins-maps weights, top
          rated seasons, and published model nulls), each computed from model
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
