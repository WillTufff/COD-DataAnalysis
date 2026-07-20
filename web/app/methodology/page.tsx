import type { Metadata } from "next";
import Link from "next/link";
import { Calibration } from "@/components/charts/Calibration";
import { WhatWinsMaps } from "@/components/charts/WhatWinsMaps";
import {
  getBacktestCards,
  getCoverage,
  getKillFeedReconciliation,
  getMetricCatalog,
  getModeWeights,
  getPaceByMode,
  getRatingComparison,
  getSeasonKdSpread,
  getWinprobArtifact,
  latestRatingRun,
  latestRun,
  type PaceCell,
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
  player_rating: "player rating",
};

const MODE_ORDER = [
  "Hardpoint",
  "Search & Destroy",
  "Control",
  "Capture the Flag",
  "Uplink",
];

// Pivots getPaceByMode() into a mode × title grid of kills per player per
// 10 minutes, with the spread across titles that share a mode. This is the
// direct evidence that raw stats are not comparable between titles.
function PaceTable({ cells }: { cells: PaceCell[] }) {
  const titles = [...new Map(cells.map((c) => [c.year, c.title])).entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([year, title]) => ({ year, title }));
  const modes = [...new Set(cells.map((c) => c.mode))].sort(
    (a, b) =>
      (MODE_ORDER.indexOf(a) + 1 || 99) - (MODE_ORDER.indexOf(b) + 1 || 99),
  );
  const at = (mode: string, year: number) =>
    cells.find((c) => c.mode === mode && c.year === year)?.killsPer10 ?? null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-hairline text-xs text-ink-muted">
            <th className="py-2 pr-4 font-normal">Mode</th>
            {titles.map((t) => (
              <th key={t.year} className="py-2 pr-4 text-right font-normal">
                {t.year} {t.title}
              </th>
            ))}
            <th className="py-2 text-right font-normal">Spread</th>
          </tr>
        </thead>
        <tbody>
          {modes.map((mode) => {
            const vals = titles
              .map((t) => at(mode, t.year))
              .filter((v): v is number => v !== null);
            const spread =
              vals.length > 1
                ? Math.round(((Math.max(...vals) - Math.min(...vals)) /
                    Math.min(...vals)) * 100)
                : null;
            return (
              <tr key={mode} className="border-b border-hairline/60">
                <td className="py-1.5 pr-4">{mode}</td>
                {titles.map((t) => {
                  const v = at(mode, t.year);
                  return (
                    <td
                      key={t.year}
                      className="py-1.5 pr-4 text-right font-mono tabular-nums"
                    >
                      {v !== null ? v.toFixed(1) : <span className="text-ink-muted">—</span>}
                    </td>
                  );
                })}
                <td className="py-1.5 text-right font-mono tabular-nums">
                  {spread !== null ? (
                    `${spread}%`
                  ) : (
                    <span className="text-ink-muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Per-event capture gaps, quoted from the source archive's own notes. Static
// because the archive is: these are losses at capture time, invisible to any
// row count taken afterwards.
const EVENT_LOSSES: {
  event: string;
  captured: string;
  note: string;
  severe?: boolean;
}[] = [
  {
    event: "2017 CWL Champs",
    captured: "297 of 298",
    note: "Hardware failure in one Search & Destroy map; basic stats recovered from video, the complex ones for four rounds were not.",
  },
  {
    event: "2018 Pro League S1",
    captured: "503 of 504",
    note: "One Capture the Flag map lost in week 6.",
  },
  {
    event: "2018 Atlanta",
    captured: "6 maps lost",
    note: "One CTF map on day 1, five more to a data-server crash on day 2.",
  },
  {
    event: "2018 Birmingham",
    captured: "164 maps, ~80 lost",
    note: "Roughly a third of the event never captured. Read anything scoped to this event with that in mind.",
    severe: true,
  },
  {
    event: "2018 Seattle",
    captured: "3 maps lost",
    note: "One Hardpoint on day 1, a Hardpoint and a Search & Destroy on day 2.",
  },
  {
    event: "2018 Champs",
    captured: "295 of 296",
    note: "One Hardpoint map from the last pool-play series of day 2.",
  },
  {
    event: "2019 Pro League Qualifier",
    captured: "317 of ~400",
    note: "The LAN data system was in beta and lost a large share of the event.",
    severe: true,
  },
  {
    event: "2019 London",
    captured: "first series block lost",
    note: "Site power issues on the Friday.",
  },
  {
    event: "2019 Champs",
    captured: "296 of 300",
    note: "Four maps missing.",
  },
];

export default async function MethodologyPage() {
  const [
    eloRun,
    glickoRun,
    eraRun,
    insightsRun,
    ratingRun,
    winprobRun,
    metricRun,
  ] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
    latestRun("era_adjust"),
    latestRun("insights"),
    latestRatingRun(),
    latestRun("winprob"),
    latestRun("metric_layer"),
  ]);
  const metricCatalog = metricRun ? await getMetricCatalog(metricRun.id) : null;
  const seriesCards = await getBacktestCards(
    [eloRun?.id, glickoRun?.id, winprobRun?.id].filter(
      (x): x is number => x !== undefined && x !== null,
    ),
  );
  const [ratingCards, modeWeights, winprobArt, coverage, pace] =
    await Promise.all([
      getBacktestCards(
        [ratingRun?.id].filter((x): x is number => x !== undefined && x !== null),
      ),
      ratingRun ? getModeWeights(ratingRun.id) : Promise.resolve([]),
      winprobRun ? getWinprobArtifact(winprobRun.id) : Promise.resolve(null),
      getCoverage(),
      getPaceByMode(),
    ]);
  const comparison = ratingRun ? await getRatingComparison(ratingRun.id) : null;
  // Stated as a share so the sentence cannot drift from the artifact.
  const brierGain = comparison
    ? 1 -
      comparison.overall[comparison.published].brier /
        comparison.overall[comparison.baseline].brier
    : null;
  const reconciliation = await getKillFeedReconciliation();
  // Qualified all-mode cohort sizes per title (≥ 8 maps), for the era section.
  const cohorts = eraRun ? await getSeasonKdSpread(eraRun.id, 8) : [];
  const cohortSizes = [...cohorts].sort((a, b) => a.year - b.year);
  const cards = [...seriesCards].sort(
    (a, b) => (a.brier ?? 1) - (b.brier ?? 1),
  );

  return (
    // Site grid (6xl) for left-edge alignment with the header; prose keeps
    // its own narrower reading measure inside.
    <main className="mx-auto max-w-6xl px-6 py-10">
      <div className="max-w-3xl">
      <p className="eyebrow text-accent">
        Model specifications and backtests
      </p>
      <h1 className="mt-1 font-display text-5xl font-bold uppercase tracking-tight">
        Methodology
      </h1>
      <p className="mt-3 text-sm text-ink-secondary">
        Each model writes its output as a versioned, immutable run tagged with
        the code commit that produced it, so any figure on the site traces back
        to one rerun. The backtests below evaluate the models on historical play
        only.
      </p>

      <section id="era" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Era adjustment
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Engagement pace changes from title to title, so the same raw stat
            does not mean the same thing across games. The table below is kills
            per player per 10 minutes in each season and mode. In the modes
            played across all three titles it moves by double-digit percentages:
            Hardpoint by roughly a fifth, Search &amp; Destroy by more than a
            third.
          </p>
          {pace.length > 0 && (
            <div className="border border-hairline bg-surface p-4">
              <PaceTable cells={pace} />
              <p className="mt-2 text-xs text-ink-muted">
                Kills per player per 10 minutes, from maps with complete
                duration data. Spread is the gap between the highest and lowest
                title for that mode; modes played in a single title have none.
              </p>
            </div>
          )}
          <p>
            Because of this, each player-season aggregate is scored against its
            own cohort: every qualified player (≥ 8 maps) in the same season,
            title, and mode, expressed as a z-score and percentile.
            {cohortSizes.length > 0 && (
              <>
                {" "}
                The qualified all-mode cohort holds{" "}
                {cohortSizes.map((c, i) => (
                  <span key={c.year}>
                    {i > 0 && (i === cohortSizes.length - 1 ? " and " : ", ")}
                    {c.values.length} in {c.year} {c.title}
                  </span>
                ))}
                .
              </>
            )}{" "}
            A 90th percentile in one cohort is the same rank as a 90th
            percentile in any other.
          </p>
          <p>
            The uncertainty band on career arcs is ±1.96/√maps in z-units, the
            sampling noise of a season-length average (an intentional
            approximation: it assumes unit per-map variance). If the archive
            lacks a stat for a season, the cell shows “—” and the row’s
            coverage percentage says how complete the underlying data is.
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
            (Glickman 2013, implemented against the worked example in the
            paper): each series is its own rating period, τ=0.5, so rating
            deviation (RD) grows when a team is idle and shrinks with evidence.
            That is the ±RD shown on{" "}
            <Link className="underline" href="/teams">
              /teams
            </Link>
            .
          </p>
          <p id="glicko2">
            Series whose deciding map is missing from the archive are left
            unrated. Predictions are strictly walk-forward: each series is
            predicted <em>before</em> the model sees its result.
          </p>
        </div>
      </section>

      <section id="player-rating" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Open player rating
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            The composite rating shown on{" "}
            <Link className="underline" href="/players">
              /players
            </Link>{" "}
            is built in four steps, each reproducible from the published code:
          </p>
          <ol className="list-decimal space-y-2 pl-5 marker:font-mono marker:text-ink-muted">
            <li>
              For each (season × mode), every map is one observation: the
              difference between the two teams’ stat profiles, standardized and
              regressed against which team won the map (L2 logistic, λ=1, fit by
              IRLS in ~40 lines of numpy). Each coefficient is how much a one-SD
              edge in that stat was worth toward winning a map in that title and
              mode.
            </li>
            <li>
              Each player-season-mode aggregate is z-scored within its qualified
              cohort and dotted with those weights.
            </li>
            <li>
              Scores shrink toward the league mean by m/(m+15) in maps played
              (partial pooling), so a 12-map sample cannot outrank a 200-map
              season.
            </li>
            <li>
              Mode scores blend by maps played, scaled so the qualified league
              averages 1.00. The ±sd is a 200-draw map-resampling bootstrap.
            </li>
          </ol>
          <p>
            Which stats step 1 reads is itself measured, not declared. Every
            feature names the source columns it needs, and a cohort keeps it only
            if that title actually populated them — so WWII Hardpoint uses time
            per life where Black Ops 4 uses hill captures, and Search &amp;
            Destroy is measured per round rather than per minute. Where a
            reconciled kill feed exists, trades enter too. Whether that helps is
            a question with an answer, published below.
          </p>
          <p>
            One reading caveat: in respawn modes a team’s kills mirror its
            opponent’s deaths almost exactly, so those two coefficients are
            near-collinear and the ridge penalty splits their shared weight.
            Read the two together as slaying.
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
          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="eyebrow text-ink-secondary">
              Learned map-win weights by mode
            </h3>
            <div className="mt-3">
              <WhatWinsMaps cohorts={modeWeights} />
            </div>
          </div>
        )}
        {ratingCards.map((c) => (
          <div key={c.runId} className="mt-4 border border-hairline bg-surface p-4">
            <div className="flex items-baseline justify-between">
              <h3 className="font-display text-xl font-semibold uppercase">
                Weight validation, walk-forward by event
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
              earlier events in the same (season × mode). The test scores a
              value model on same-map box scores, and shows the learned weights
              generalize to events they were not trained on, which is what the
              rating relies on.
            </p>
          </div>
        ))}

        {comparison && (
          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="font-display text-xl font-semibold uppercase">
              Does adding intangibles help?
            </h3>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              Three feature sets, scored walk-forward on the{" "}
              {comparison.common_maps.toLocaleString()} maps every one of them
              predicts — versions have different data requirements, and comparing
              raw totals would let a version look better by quietly predicting an
              easier subset. v{comparison.baseline} is the original box-score
              model; v2.0.0 adds the measured per-mode metric features; v2.1.0
              adds kill-feed trades where a reconciled feed exists.
              {brierGain !== null && (
                <>
                  {" "}
                  The published v{comparison.published} cuts Brier score{" "}
                  {(brierGain * 100).toFixed(0)}% against the baseline.
                </>
              )}
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full max-w-2xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Version</th>
                    <th className="py-2 pr-4 text-right font-normal">Brier</th>
                    <th className="py-2 pr-4 text-right font-normal">Log loss</th>
                    <th className="py-2 pr-4 text-right font-normal">Accuracy</th>
                    <th className="py-2 text-right font-normal">Δ Brier</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.versions.map((v) => {
                    const s = comparison.overall[v];
                    const d = comparison.delta_vs_baseline[v]?.brier ?? 0;
                    return (
                      <tr key={v} className="border-b border-hairline/60">
                        <td className="py-2 pr-4 font-mono text-xs">
                          v{v}
                          {v === comparison.published && (
                            <span className="ml-2 text-accent">published</span>
                          )}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {s.brier.toFixed(4)}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {s.log_loss.toFixed(4)}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {(s.accuracy * 100).toFixed(1)}%
                        </td>
                        <td className="py-2 text-right font-mono text-xs tabular-nums text-ink-secondary">
                          {v === comparison.baseline
                            ? "—"
                            : `${d > 0 ? "+" : ""}${d.toFixed(4)}`}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <h4 className="mt-6 eyebrow text-ink-secondary">By cohort</h4>
            <p className="mt-2 max-w-3xl text-xs text-ink-muted">
              &ldquo;v{comparison.published} wins&rdquo; is a weaker claim than
              where it wins. Lower Brier is better; the cohorts where the
              published version does not beat the baseline are marked, and they
              stay on the page.
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full max-w-2xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Cohort</th>
                    <th className="py-2 pr-4 text-right font-normal">Maps</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      v{comparison.baseline}
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">
                      v{comparison.published}
                    </th>
                    <th className="py-2 text-right font-normal">Δ</th>
                  </tr>
                </thead>
                <tbody>
                  {comparison.by_cohort.map((c) => {
                    const base = c.versions[comparison.baseline];
                    const pub = c.versions[comparison.published];
                    if (!base || !pub) return null;
                    const d = pub.brier - base.brier;
                    return (
                      <tr
                        key={`${c.season_id}-${c.mode}`}
                        className="border-b border-hairline/60"
                      >
                        <td className="py-2 pr-4 whitespace-nowrap">
                          {c.year} {c.title} · {c.mode}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                          {c.n_maps}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                          {base.brier.toFixed(4)}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {pub.brier.toFixed(4)}
                        </td>
                        <td
                          className={`py-2 text-right font-mono text-xs tabular-nums ${
                            d > 0 ? "text-ink" : "text-ink-secondary"
                          }`}
                        >
                          {d > 0 ? "+" : ""}
                          {d.toFixed(4)}
                          {d > 0 && " worse"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>

      <section id="winprob" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Series win probability (winprob_v1)
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Glicko-2 is the strongest baseline below, so this model tests
            whether anything <em>beyond</em> the ratings carries information
            about who wins a series. Its features, all computed strictly before
            each series, are the walk-forward Glicko-2 and Elo probabilities,
            combined rating
            deviation, each team’s last-{winprobArt?.formWindow ?? 10} win rate,
            and a shrunken head-to-head record, in an expanding-window logistic
            regression refit every {winprobArt?.refitEvery ?? 50} series. Until{" "}
            {winprobArt?.minTrain ?? 200} series of history exist it passes
            Glicko-2 through unchanged, so its backtest is directly comparable.
          </p>
          <p>
            Over 2017–2019 the added features contribute nothing: the Brier
            difference in the report cards below is within noise, and the
            learned form coefficient is approximately zero. On this record,
            recent form and head-to-head history carry no measurable
            information beyond team strength.
            {winprobRun && (
              <span className="text-ink-muted">
                {" "}
                Current run: v{winprobRun.version}, code {winprobRun.codeRef ?? "n/a"}.
              </span>
            )}
          </p>
          {winprobArt && (
            <div className="overflow-x-auto border border-hairline bg-surface p-4">
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
                The rating logits carry the prediction; the rest hover near
                zero. Coefficients are correlated (both rating systems measure
                the same thing), so the near-zero non-rating rows indicate an
                absence of residual signal rather than precise effect sizes.
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
          The findings feed is computed directly from model output and the raw
          record. There are eight kinds (outlier, trend, milestone, era
          context, head-to-head edge, what-wins-maps weights, top rated
          seasons, and published model nulls), each with fixed eligibility
          thresholds (e.g. outliers require ≥ 30 maps and |z| ≥ 2). The numbers
          in each headline are read from the database, not written by hand.
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
          Archive coverage
        </h2>
        <div className="mt-3 overflow-x-auto border border-hairline bg-surface p-4">
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
            there); 2018–2019 are full seasons. “Extended stats” is the share
            of player-map rows carrying the 2019-format extras (EKIA, accuracy,
            time alive, streaks). The earlier spreadsheets did not record
            them, so those cells stay empty.
          </p>
        </div>

        <h3 className="mt-8 eyebrow text-ink-secondary">
          Per-event data loss
        </h3>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-secondary">
          The archive records what it failed to capture, and those gaps are not
          uniform — two events lost enough that any per-event comparison
          involving them is unsafe. Everything below is quoted from the source
          archive&rsquo;s own notes rather than inferred from row counts.
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full max-w-3xl text-left text-sm">
            <thead>
              <tr className="border-b border-hairline text-xs text-ink-muted">
                <th className="py-2 pr-4 font-normal">Event</th>
                <th className="py-2 pr-4 font-normal">Captured</th>
                <th className="py-2 font-normal">Note</th>
              </tr>
            </thead>
            <tbody>
              {EVENT_LOSSES.map((e) => (
                <tr
                  key={e.event}
                  className={`border-b border-hairline/60 align-top ${
                    e.severe ? "text-ink" : "text-ink-secondary"
                  }`}
                >
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {e.event}
                    {e.severe && (
                      <span className="ml-2 font-mono text-[10px] text-accent">
                        major
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-4 font-mono text-xs whitespace-nowrap tabular-nums">
                    {e.captured}
                  </td>
                  <td className="py-2 text-xs">{e.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 max-w-3xl text-xs text-ink-muted">
          Forfeited and tied maps are present in the source data and are kept, so
          a handful of maps have unrepresentative scorelines by design. Of the
          typed columns, Black Ops 4 damage is recorded on about 83% of its
          player-maps, and every damage metric uses only those rows as its
          denominator.
        </p>
      </section>

      <section id="rounds" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Structured event tier
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Underneath the 2017 and 2018 box scores sits a full event feed — every
            kill with its attacker, victim, weapon, position and game clock, plus
            round-boundary scores. Black Ops 4 (2019) shipped box scores with empty
            event lists, so this tier is a 2017–2018 story: the{" "}
            <Link href="/rounds" className="underline">
              rounds page
            </Link>{" "}
            and the trade, clutch and man-advantage cards do not exist for BO4.
          </p>
          <p>
            Nothing from the feed is trusted until it reconciles with the box score.
            For every (game, player) the feed&rsquo;s normal-death count — suicides and
            team kills excluded — must equal the box-score death total. Player-maps
            that fail are excluded from every kill-feed metric through a single
            queryable set, never patched; the WWII figure below is a hard check in CI.
          </p>
        </div>

        {reconciliation && reconciliation.byTitle.length > 0 && (
          <div className="mt-5 overflow-x-auto">
            <table className="w-full max-w-xl text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Title</th>
                  <th className="py-2 pr-4 text-right font-normal">Player-maps</th>
                  <th className="py-2 pr-4 text-right font-normal">Reconciled</th>
                  <th className="py-2 text-right font-normal">Rate</th>
                </tr>
              </thead>
              <tbody>
                {reconciliation.byTitle.map((t) => (
                  <tr key={t.title} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-4">{t.title}</td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {t.player_maps.toLocaleString()}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {t.reconciled.toLocaleString()}
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums">
                      {(t.rate * 100).toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-ink-muted">
              Infinite Warfare&rsquo;s residual is feed deaths the box never recorded,
              not an error. Those player-maps are dropped from kill-feed metrics.
            </p>
          </div>
        )}

        {metricCatalog?.kill_feed_constants && (
          <dl className="mt-6 max-w-3xl space-y-3 text-sm">
            {(
              [
                ["Trade window", metricCatalog.kill_feed_constants.trade],
                ["Man advantage", metricCatalog.kill_feed_constants.advantage_state],
                ["Clutch", metricCatalog.kill_feed_constants.clutch],
                ["Reconciliation", metricCatalog.kill_feed_constants.reconciliation],
              ] as const
            ).map(([term, def]) => (
              <div key={term}>
                <dt className="font-semibold text-ink">{term}</dt>
                <dd className="text-ink-secondary">{def}</dd>
              </div>
            ))}
          </dl>
        )}

        <p className="mt-6 max-w-3xl text-sm leading-relaxed text-ink-secondary">
          Two limits are stated rather than papered over. The per-kill distance field
          is Infinite Warfare only and, despite the box column&rsquo;s metres label, is
          in engine units — it correlates with that column at r&nbsp;=&nbsp;0.97 per
          player-season but on a fixed ~5.75&times; scale, so it is reported as engine
          space, not metric. Every Hardpoint game lists its hill names and rotation
          count, but the events carry no per-hill timing, so kills cannot be attributed
          to a specific hill and hill-by-hill analysis is not claimed. Headshots stay
          with the box score, whose headshot column the feed&rsquo;s cause-of-death
          matches only about 69% of the time.
        </p>
      </section>

      <section id="metrics" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Metric glossary
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Every published metric, generated from the same catalog the{" "}
            <Link href="/stats" className="underline">
              stat explorer
            </Link>{" "}
            reads, so a definition here and a number there can never disagree.
            Numerators and denominators are summed over a player&rsquo;s maps and
            divided once; a season rate is never the average of per-map rates.
          </p>
          <p>
            Which seasons a metric covers is measured, not assumed. A column that
            a title records but never populated is treated as absent, so the
            metric simply does not exist for that season rather than reading zero.
            Those columns are listed at the end of this section.
          </p>
        </div>
        {metricCatalog && (
          <div className="mt-5 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-normal">Metric</th>
                  <th className="py-2 pr-3 font-normal">Definition</th>
                  <th className="py-2 pr-3 font-normal">Seasons</th>
                  <th className="py-2 font-normal">Qualifies at</th>
                </tr>
              </thead>
              <tbody>
                {metricCatalog.metrics.map((m) => (
                  <tr key={m.key} className="border-b border-hairline/60 align-top">
                    <td className="py-2 pr-3">
                      <div className="font-medium text-ink">{m.label}</div>
                      <div className="font-mono text-[10px] text-ink-muted">{m.key}</div>
                    </td>
                    <td className="py-2 pr-3">
                      <div className="font-mono text-xs text-ink-secondary">{m.formula}</div>
                      {m.note && (
                        <div className="mt-1 text-xs text-ink-muted">{m.note}</div>
                      )}
                    </td>
                    <td className="py-2 pr-3 font-mono text-xs text-ink-secondary">
                      {m.titles.join(", ") || (
                        <span className="text-ink-muted">none reached threshold</span>
                      )}
                    </td>
                    <td className="py-2 font-mono text-xs text-ink-secondary">
                      {m.min_denom} {m.denom_kind}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {metricCatalog && metricCatalog.untracked_columns.length > 0 && (
          <div className="mt-8">
            <h3 className="eyebrow text-[10px] text-ink-secondary">
              Columns recorded but never populated
            </h3>
            <p className="mt-2 text-sm text-ink-secondary">
              These appear in the source data for the season shown but hold no
              values, so nothing is published from them. A column counts as
              tracked once at least {metricCatalog.min_nonzero_rows} of its rows
              are non-zero, which keeps genuinely rare events in and empty
              columns out.
            </p>
            <table className="mt-3 w-full max-w-xl text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Season</th>
                  <th className="py-2 pr-4 font-normal">Column</th>
                  <th className="py-2 text-right font-normal">Non-zero rows</th>
                </tr>
              </thead>
              <tbody>
                {metricCatalog.untracked_columns.map((c) => (
                  <tr key={`${c.title}-${c.column}`} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-4 text-ink-secondary">{c.title}</td>
                    <td className="py-1.5 pr-4 font-mono text-xs">{c.column}</td>
                    <td className="py-1.5 text-right font-mono text-xs tabular-nums text-ink-secondary">
                      {c.nonzero} of {c.rows}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section id="attribution" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Data & attribution
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Box scores are Activision Publishing’s official CWL data release
            (the <code className="font-mono text-xs">cwl-data</code> repository,
            BSD-3-Clause). The 2017–2018 structured event feeds behind this tier
            come from the same repository under the same licence. Event metadata
            and roster context come from Liquipedia contributors (CC-BY-SA 3.0).
            Handles are normalized across seasons with an alias map maintained in
            the repository; corrections are welcome.
          </p>
          <p>
            The upstream repository was taken down, so both tiers are recovered
            from Software Heritage and pinned to snapshot{" "}
            <code className="font-mono text-xs">c5ee2cd0</code> and revision{" "}
            <code className="font-mono text-xs">5b7eb907</code> of{" "}
            <code className="font-mono text-xs">github.com/Activision/cwl-data</code>.
            The fetch script re-hashes the box-score CSVs against that revision, so
            the box scores and the event feeds are provably one version of the source.
          </p>
        </div>
      </section>
      </div>
    </main>
  );
}
