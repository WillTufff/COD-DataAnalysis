import type { Metadata } from "next";
import Link from "next/link";
import { Calibration } from "@/components/charts/Calibration";
import { EstimateForest } from "@/components/charts/EstimateForest";
import { LadderMovement } from "@/components/charts/LadderMovement";
import { MovementVsError } from "@/components/charts/MovementVsError";
import { SpreadVsError } from "@/components/charts/SpreadVsError";
import { WhatWinsMaps } from "@/components/charts/WhatWinsMaps";
import {
  formatYearSpan,
  getBacktestCards,
  getCoverage,
  getFeedKinds,
  getKillFeedReconciliation,
  getMapElo,
  getMetricCatalog,
  getModelGaps,
  getModeWeights,
  getPaceByMode,
  getRatingComparison,
  getRatingPersistence,
  getRatingPosterior,
  getRatingShrinkage,
  getRapm,
  getRapmPreflight,
  RAPM_CONCENTRATION_LIMIT,
  getRosterForecast,
  getRoundWinProb,
  getSeasonEras,
  getSeasonKdSpread,
  getPlayerStyleArtifact,
  getSeriesDynamics,
  getSignBaseline,
  getWinprobArtifact,
  PUBLISHED_RATING_VERSION,
  getEvaluationManifest,
  getEvaluationPlacebo,
  getEvaluationPopulation,
  getEvaluationPrimary,
  getEvaluationReproduction,
  getEvaluationSecondary,
  getOpenskillBaseline,
  getMatchContext,
  getOpponentAdjustment,
  getSeasonRapm,
  getSkillPower,
  getSkillPrior,
  getSkillSeasons,
  latestEvaluationRun,
  latestOpenskillRun,
  latestMatchContextRun,
  latestOpponentAdjustRun,
  latestRapmPreflightRun,
  latestRatingRun,
  latestRun,
  latestSeasonRapmRun,
  latestSkillRun,
  type PaceCell,
  getModeCatalog,
} from "@/lib/analytics";
import { kindLabel } from "@/lib/insightKinds";
import { RATINGS, primacyReason } from "@/lib/primacy";
import { modeLabel } from "@/lib/modes";

// The archive is frozen and the models only change on a rerun, so this page is
// prerendered and revalidated on a timer rather than queried per request.
export const revalidate = 3600;

export const metadata: Metadata = { title: "Methodology" };

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="font-mono text-2xl tabular-nums">{value}</div>
      <div className="mt-0.5 text-xs text-ink-muted">{label}</div>
    </div>
  );
}

// The roster-forecast table, ordered best Brier first — which is the order the
// result is argued in, and puts the two box-score-free predictors at the top
// where the reader meets them before the composite rating.
const FORECAST_PREDICTORS: [string, string][] = [
  ["rapm", "RAPM"],
  ["rapm_prior", "RAPM, rating-centered"],
  ["rating", "Roster composite rating"],
  ["rating_zshrink", "Same rating, z-and-shrink"],
  ["glicko", "Glicko-2 team rating"],
  ["kd", "Roster K/D"],
];

const MODEL_LABEL: Record<string, string> = {
  elo: "Elo",
  glicko2: "Glicko-2",
  winprob: "winprob_v1",
  player_rating: "player rating",
  map_elo: "map_elo (blend, rolled up)",
};

// The gap artifact names models the way the analytics layer does, which is not
// how the cards above them are labelled.
const GAP_LABEL: Record<string, string> = {
  elo: "Elo",
  glicko2: "Glicko-2",
  winprob_v1: "winprob_v1",
  map_global: "map_elo global",
  map_mode: "map_elo mode",
  map_blend: "map_elo blend",
};

// The scoreline indicators the series-dynamics artifact publishes. `team1_won`
// is in the artifact as a symmetry check — it has to sit at 0.500 — and is not
// a finding, so the table drops it.
const SERIES_EVENT_LABEL: Record<string, string> = {
  map1_winner_won: "Map-1 winner takes the series",
  sweep: "Sweep (3-0)",
  decider: "Goes the distance (3-2)",
  reverse_sweep: "Reverse sweep (0-2 down, won)",
};

const ARM_LABEL: Record<string, string> = {
  global: "One rating per team",
  mode: "One rating per team and mode",
  blend: "Blend of the two",
};

// Brier gaps are small and signed, and the sign is the whole claim.
const signed = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(4)}`;

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
  // map_elo's backtest row is its *series* rollup, so it belongs with the
  // series cards rather than in a table of its own.
  const mapEloRun = await latestRun("map_elo");
  const seriesCards = await getBacktestCards(
    [eloRun?.id, glickoRun?.id, winprobRun?.id, mapEloRun?.id].filter(
      (x): x is number => x !== undefined && x !== null,
    ),
  );
  const mapElo = await getMapElo();
  // The phases fitted after the career rating. Each opened its own run, so none
  // of this is reachable from ratingRun.
  const [
    preflightRun,
    seasonRapmRun,
    opponentRun,
    matchContextRun,
    openskillRun,
    skillRun,
    evaluationRun,
  ] = await Promise.all([
      latestRapmPreflightRun(),
      latestSeasonRapmRun(),
      latestOpponentAdjustRun(),
      latestMatchContextRun(),
      latestOpenskillRun(),
      latestSkillRun(),
      latestEvaluationRun(),
    ]);
  const [
    preflight,
    seasonRapm,
    opponentAdjustment,
    matchContext,
    openskill,
    skillPrior,
    skillSeasons,
    evaluationManifest,
    evaluationPrimary,
    evaluationSecondary,
    evaluationPlacebo,
    evaluationReproduction,
    skillPower,
    evaluationPopulation,
  ] = await Promise.all([
    preflightRun ? getRapmPreflight(preflightRun.id) : Promise.resolve(null),
    seasonRapmRun ? getSeasonRapm(seasonRapmRun.id) : Promise.resolve(null),
    opponentRun ? getOpponentAdjustment(opponentRun.id) : Promise.resolve(null),
    matchContextRun ? getMatchContext(matchContextRun.id) : Promise.resolve(null),
    openskillRun ? getOpenskillBaseline(openskillRun.id) : Promise.resolve(null),
    skillRun ? getSkillPrior(skillRun.id) : Promise.resolve(null),
    skillRun ? getSkillSeasons(skillRun.id) : Promise.resolve([]),
    evaluationRun
      ? getEvaluationManifest(evaluationRun.id)
      : Promise.resolve(null),
    evaluationRun
      ? getEvaluationPrimary(evaluationRun.id)
      : Promise.resolve(null),
    evaluationRun
      ? getEvaluationSecondary(evaluationRun.id)
      : Promise.resolve(null),
    evaluationRun
      ? getEvaluationPlacebo(evaluationRun.id)
      : Promise.resolve(null),
    evaluationRun
      ? getEvaluationReproduction(evaluationRun.id)
      : Promise.resolve(null),
    evaluationRun ? getSkillPower(evaluationRun.id) : Promise.resolve(null),
    getEvaluationPopulation(),
  ]);
  const skillYears = skillSeasons.map((s) => s.year);
  // The secondary test SKILL is good at, kept separate because the primary is
  // the one it lost and a page that shows only one of them is arguing.
  const priorTarget = evaluationSecondary?.prior_target_persistence ?? null;
  const [
    ratingCards,
    modeWeights,
    winprobArt,
    modelGaps,
    coverage,
    pace,
    seasonEras,
    modeCatalog,
  ] = await Promise.all([
      getBacktestCards(
        [ratingRun?.id].filter((x): x is number => x !== undefined && x !== null),
      ),
      ratingRun ? getModeWeights(ratingRun.id) : Promise.resolve([]),
      winprobRun ? getWinprobArtifact(winprobRun.id) : Promise.resolve(null),
      winprobRun ? getModelGaps(winprobRun.id) : Promise.resolve(null),
      getCoverage(),
      getPaceByMode(),
      getSeasonEras(),
      getModeCatalog(),
    ]);
  const [
    comparison,
    signBaseline,
    persistence,
    forecast,
    shrinkage,
    posterior,
    rapm,
  ] = await Promise.all([
    ratingRun ? getRatingComparison(ratingRun.id) : Promise.resolve(null),
    ratingRun ? getSignBaseline(ratingRun.id) : Promise.resolve(null),
    ratingRun ? getRatingPersistence(ratingRun.id) : Promise.resolve(null),
    ratingRun ? getRosterForecast(ratingRun.id) : Promise.resolve(null),
    ratingRun ? getRatingShrinkage(ratingRun.id) : Promise.resolve(null),
    ratingRun ? getRatingPosterior(ratingRun.id) : Promise.resolve(null),
    ratingRun ? getRapm(ratingRun.id) : Promise.resolve(null),
  ]);
  // The two numbers the posterior section argues from, read off the artifact so
  // the prose cannot outlive the run.
  const moved = posterior?.vs_z_shrink.available ? posterior.vs_z_shrink : null;
  const signalShares = (posterior?.cohorts ?? []).flatMap((c) =>
    c.signal_share === null ? [] : [c.signal_share],
  );
  // Built as strings rather than inline fragments so the punctuation after them
  // stays attached to the word instead of picking up JSX's line whitespace.
  const signalRange = signalShares.length
    ? ` — from ${(Math.min(...signalShares) * 100).toFixed(0)}% to ${(
        Math.max(...signalShares) * 100
      ).toFixed(0)}% across these cohorts`
    : "";
  const intervalFactor =
    moved?.interval.median != null
      ? ` — larger by a median factor of ${moved.interval.median.toFixed(2)}`
      : "";
  const orderingNote =
    moved && moved.spearman !== null
      ? `; the two orderings correlate ${moved.spearman.toFixed(3)} and ${
          moved.top10_unchanged
        } of the top ten qualified seasons are the same players`
      : "";
  // Cohorts whose weight ratio has a published interval, and of those, the ones
  // it does not resolve. Both counts are read off the artifact so the paragraph
  // cannot outlive the run it describes.
  const weightCiCohorts = modeWeights.flatMap((c) =>
    c.restVsSlayCi ? [{ ...c, ci: c.restVsSlayCi }] : [],
  );
  const unresolvedCohorts = weightCiCohorts.filter(
    (c) => c.ci[0] <= 1 && c.ci[1] >= 1,
  );
  // Stated as a share so the sentence cannot drift from the artifact.
  const brierGain = comparison
    ? 1 -
      comparison.overall[comparison.published].brier /
        comparison.overall[comparison.baseline].brier
    : null;
  // Versions fitted after the published one, with how each scores against it on
  // the same maps. Read off the artifact, so a promotion empties this list.
  const deferredVersions = comparison
    ? comparison.versions
        .slice(comparison.versions.indexOf(comparison.published) + 1)
        .map((v) => ({
          version: v,
          brierVsPublished:
            comparison.overall[v].brier -
            comparison.overall[comparison.published].brier,
        }))
    : [];
  // The range the slaying columns alone reach, read off the artifact rather
  // than written into the sentence — the whole point of the table is that these
  // numbers move when the model does.
  const slayAcc = (signBaseline?.by_cohort ?? []).flatMap((c) =>
    c.features.filter((f) => f.slaying).map((f) => f.accuracy),
  );
  const slayRange = slayAcc.length
    ? `${(Math.min(...slayAcc) * 100).toFixed(0)}–${(Math.max(...slayAcc) * 100).toFixed(0)}% of maps`
    : "most maps";
  const reconciliation = await getKillFeedReconciliation();
  // RAPM's own gap against the 0.5 floor, which is the headline of the table
  // it now tops. Absent on runs written before the vs-coin-flip block existed.
  const rapmVsFlip = forecast?.contrasts.available
    ? forecast.contrasts.vs_coin_flip?.rapm
    : undefined;
  // What the two RAPM diagnostics say jointly rather than side by side: among
  // the players the artifact actually names, how many clear 1.96 SE *and* are
  // separable from their lineup. Counted rather than asserted, because a rerun
  // can change it and a sentence claiming "one player" would not notice.
  const rapmListed = rapm?.available
    ? [...rapm.leaders, ...rapm.trailers]
    : [];
  const rapmSeparableResolved = rapmListed.filter(
    (p) =>
      p.se > 0 &&
      Math.abs(p.coef) > 1.96 * p.se &&
      p.teammate_concentration < RAPM_CONCENTRATION_LIMIT,
  ).length;
  const seriesDyn = await getSeriesDynamics();
  const styleArt = await getPlayerStyleArtifact();
  // The sequence fit, and the plain regression it corrects — both pulled out by
  // name so the paragraphs cannot drift onto another spec if the list grows.
  const seriesQuality = seriesDyn?.momentum?.quality ?? null;
  const seriesMap2 = seriesDyn?.momentum?.map2;
  const seriesNaive =
    seriesMap2?.available === true
      ? (seriesMap2.specs
          .find((s) => s.spec === "strength_prev")
          ?.terms.find((t) => t.term === "prev") ?? null)
      : null;
  const roundWp = await getRoundWinProb();
  // Pulled out by name so the paragraphs below cannot drift onto another
  // comparison if the spec list ever grows.
  const roundBacktest = roundWp?.winProb.backtest.available
    ? roundWp.winProb.backtest
    : null;
  const roundBrier = new Map(
    (roundBacktest?.models ?? []).map((m) => [m.model, m.brier]),
  );
  const timeNull =
    roundBacktest?.nested.find((p) => p.a === "survivors_time") ?? null;
  const bombNull =
    roundBacktest?.nested.find((p) => p.a === "survivors_bomb") ?? null;
  const roundRel = roundWp?.wpa?.reliability.available
    ? roundWp.wpa.reliability
    : null;
  const wpaResid = roundRel?.cells.find((c) => c.key === "wpa_resid") ?? null;
  // Rendered from the run rather than restated in prose: the earlier hardcoded
  // list said "eight kinds" and stayed there through six metric-layer additions.
  const feedKinds = insightsRun ? await getFeedKinds(insightsRun.id) : [];
  // Qualified all-mode cohort sizes per title (≥ 8 maps), for the era section.
  const cohorts = eraRun ? await getSeasonKdSpread(eraRun.id, 8) : [];
  const cohortSizes = [...cohorts].sort((a, b) => a.year - b.year);
  const cards = [...seriesCards].sort(
    (a, b) => (a.brier ?? 1) - (b.brier ?? 1),
  );
  // The contrast the momentum null rests on, pulled out by name so the
  // paragraph above the coefficient table cannot drift onto another pair, and
  // re-signed as an improvement over Glicko-2 to match the sentence around it.
  const raw =
    modelGaps?.pairs.find(
      (p) =>
        (p.a === "glicko2" && p.b === "winprob_v1") ||
        (p.a === "winprob_v1" && p.b === "glicko2"),
    ) ?? null;
  const momentum =
    raw === null
      ? null
      : raw.a === "glicko2"
        ? { delta: raw.delta, lo: raw.lo, hi: raw.hi, p: raw.dmP }
        : { delta: -raw.delta, lo: -raw.hi, hi: -raw.lo, p: raw.dmP };

  // The window the winprob backtest actually ran over, and its accuracy gap in
  // points, so the null result below quotes the run rather than a past one.
  const winprobCard = cards.find((c) => c.model === "winprob") ?? null;
  const winprobWindow = winprobCard
    ? formatYearSpan(
        new Date(winprobCard.windowFrom).getUTCFullYear(),
        new Date(winprobCard.windowTo).getUTCFullYear(),
      )
    : null;
  const accuracyGapPp =
    modelGaps?.models.winprob_v1 && modelGaps.models.glicko2
      ? (modelGaps.models.winprob_v1.accuracy - modelGaps.models.glicko2.accuracy) * 100
      : null;

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
            paper): one event is one rating period, τ=0.5, and every rated team
            advances at each period&rsquo;s close, so rating deviation (RD)
            grows when a team is idle and shrinks with evidence.
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
          <p>
            Rating state is keyed on the organisation rather than the brand, so a
            rebrand continues one curve instead of restarting at 1500, while the
            stored rows keep the team that actually played. A lineage is only
            declared where two brands&rsquo; series windows don&rsquo;t overlap —
            a same-brand roster playing concurrently is an academy team, not a
            rebrand. In this archive that leaves one lineage, eRa → eRa Eternity:
            the era&rsquo;s well-known rebrands all happen after the data stops,
            so the machinery matters for the CDL era more than this one.
          </p>
        </div>
      </section>

      {mapElo && (
        <section id="map-elo" className="mt-12">
          <h2 className="font-display text-2xl font-semibold uppercase">
            Map Elo, and one rating per mode
          </h2>
          <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
            <p>
              The ratings above use{" "}
              {mapElo.seriesRollup.n_series.toLocaleString()} series while the{" "}
              {mapElo.mapBacktest.n_maps.toLocaleString()} decided maps
              underneath them go unrated. And a series is a blend of three or
              four different games — a Hardpoint, a Search and Destroy, a
              Control or Capture the Flag — which one number per team cannot
              describe. This model asks both questions at once: does the extra
              sample help, and is “top three in Hardpoint, mid-table in
              Search” a real thing?
            </p>
            <p>
              Three arms, all Elo, all on the same maps, all walk-forward, all
              sharing one K={mapElo.mapBacktest.k} so the comparison is about
              how finely rating state is cut rather than about a constant tuned
              for one of them. The blend mixes a team’s global and
              per-mode ratings by how much mode history it has, m / (m +{" "}
              {mapElo.mapBacktest.blend_k}), so it nests the other two.
            </p>
          </div>

          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="eyebrow text-ink-secondary">
              Predicting map winners
            </h3>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full max-w-3xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Rating state</th>
                    <th className="py-2 pr-4 text-right font-normal">Brier</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Log loss
                    </th>
                    <th className="py-2 text-right font-normal">Accuracy</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(mapElo.mapBacktest.arms)
                    .sort((a, b) => a[1].brier - b[1].brier)
                    .map(([arm, s]) => (
                      <tr key={arm} className="border-b border-hairline/60">
                        <td className="py-2 pr-4">{ARM_LABEL[arm] ?? arm}</td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {s.brier.toFixed(5)}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {s.log_loss.toFixed(5)}
                        </td>
                        <td className="py-2 text-right font-mono tabular-nums">
                          {(s.accuracy * 100).toFixed(1)}%
                        </td>
                      </tr>
                    ))}
                  <tr className="text-ink-muted">
                    <td className="py-2 pr-4">Coin flip</td>
                    <td className="py-2 pr-4 text-right font-mono tabular-nums">
                      {mapElo.mapBacktest.coin_flip_brier.toFixed(5)}
                    </td>
                    <td className="py-2 pr-4 text-right font-mono tabular-nums">
                      0.69315
                    </td>
                    <td className="py-2 text-right font-mono tabular-nums">
                      50.0%
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              <strong className="text-ink">
                A mode-specific rating does not beat a global one at predicting
                map winners — it loses.
              </strong>{" "}
              {(() => {
                const p = mapElo.mapBacktest.gaps.pairs?.find(
                  (x) => x.a === "global" && x.b === "mode",
                );
                if (!p) return null;
                return (
                  <>
                    The gap is {signed(p.delta)} [{signed(p.lo)},{" "}
                    {signed(p.hi)}], DM p ={" "}
                    {p.dm_p === null ? "—" : p.dm_p.toFixed(3)}, against a
                    detectability threshold of {p.mde80.toFixed(5)} — it clears
                    both tests, and global wins at every K in the sweep. Cutting
                    the archive five ways costs more in precision than mode
                    identity returns in signal.
                  </>
                );
              })()}
            </p>
          </div>

          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="eyebrow text-ink-secondary">Mode by mode</h3>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              The overall number hides a pattern, so the same contrast is
              computed inside each mode. A negative gap means the mode-specific
              rating is worse.
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full max-w-3xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Mode</th>
                    <th className="py-2 pr-4 text-right font-normal">Maps</th>
                    <th className="py-2 pr-4 text-right font-normal">Global</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Per mode
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">Blend</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Global − mode
                    </th>
                    <th className="py-2 text-right font-normal">95% CI</th>
                  </tr>
                </thead>
                <tbody>
                  {mapElo.mapBacktest.by_mode.map((row) => {
                    const p = row.gaps.pairs?.find(
                      (x) => x.a === "global" && x.b === "mode",
                    );
                    return (
                      <tr key={row.mode} className="border-b border-hairline/60">
                        <td className="py-2 pr-4">
                          {modeLabel(modeCatalog, row.mode)}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {row.n_maps.toLocaleString()}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {row.arms.global?.brier.toFixed(5) ?? "—"}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {row.arms.mode?.brier.toFixed(5) ?? "—"}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {row.arms.blend?.brier.toFixed(5) ?? "—"}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {p ? signed(p.delta) : "—"}
                        </td>
                        <td className="py-2 text-right font-mono text-xs tabular-nums">
                          {p ? `${signed(p.lo)} to ${signed(p.hi)}` : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              Search &amp; Destroy is the only mode whose gap doesn&rsquo;t run
              against the mode arm — and its interval spans zero. So Search is
              the one mode where mode-specific state is <em>not shown to hurt</em>
              , not one where it helps. The thin modes go the other way hard:
              Control and Uplink lose more than a hundredth of Brier to
              per-mode state, which is what a rating with a few hundred maps
              behind it looks like.
            </p>
          </div>

          {mapElo.specialization.available && (
            <div className="mt-4 border border-hairline bg-surface p-4">
              <h3 className="eyebrow text-ink-secondary">
                Is mode specialization real?
              </h3>
              <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
                A spread of per-mode ratings proves nothing on its own: fit five
                noisy numbers per team instead of one and they will differ. So
                the spread is tested against a null that shuffles mode labels
                within each event — keeping every team, opponent, result, date
                and the event&rsquo;s own mode mix, and destroying only which
                mode a team was playing.
              </p>
              <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat
                  label={`Observed spread, ${mapElo.specialization.n_cells} cells`}
                  value={`${mapElo.specialization.observed_sd?.toFixed(1)}`}
                />
                <Stat
                  label={`Shuffled, ${mapElo.specialization.null_permutations} refits`}
                  value={`${mapElo.specialization.null_mean_sd?.toFixed(1)}`}
                />
                <Stat
                  label="Null 95% range"
                  value={`${mapElo.specialization.null_lo?.toFixed(1)}–${mapElo.specialization.null_hi?.toFixed(1)}`}
                />
                <Stat
                  label="p-value"
                  value={`${mapElo.specialization.p_value?.toFixed(4)}`}
                />
              </div>
              <p className="mt-4 max-w-3xl text-sm leading-relaxed text-ink-secondary">
                <strong className="text-ink">
                  {mapElo.specialization.exceeds_null
                    ? "The spread exceeds what shuffled labels produce."
                    : "The spread sits inside what shuffled labels produce."}
                </strong>{" "}
                {!mapElo.specialization.exceeds_null && (
                  <>
                    About {mapElo.specialization.excess_sd?.toFixed(1)} rating
                    points of the observed{" "}
                    {mapElo.specialization.observed_sd?.toFixed(1)} survive what
                    noise alone supplies. This archive cannot show that teams
                    have real per-mode strengths distinct from being good or bad
                    in general. The table below is published with that number
                    attached — the ordering is the thing readers ask for, and
                    hiding it would not make it less tempting elsewhere. Note
                    what the null does <em>not</em> rule out: an effect too small
                    for {mapElo.mapBacktest.n_maps.toLocaleString()} maps to
                    separate from noise.
                  </>
                )}
              </p>
            </div>
          )}

          {mapElo.modeRatings && mapElo.modeRatings.rows.length > 0 && (
            <div className="mt-4 border border-hairline bg-surface p-4">
              <h3 className="eyebrow text-ink-secondary">
                Largest mode gaps, and why to distrust them
              </h3>
              <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
                Each team&rsquo;s per-mode rating minus its own global rating,
                over cells with at least {mapElo.modeRatings.min_mode_maps}{" "}
                maps. Read against the null above: every gap here is inside the
                range shuffled labels produce.
              </p>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full max-w-3xl text-left text-sm">
                  <thead>
                    <tr className="border-b border-hairline text-xs text-ink-muted">
                      <th className="py-2 pr-4 font-normal">Team</th>
                      <th className="py-2 pr-4 font-normal">Mode</th>
                      <th className="py-2 pr-4 text-right font-normal">Maps</th>
                      <th className="py-2 pr-4 text-right font-normal">
                        Mode rating
                      </th>
                      <th className="py-2 pr-4 text-right font-normal">
                        Global
                      </th>
                      <th className="py-2 text-right font-normal">Gap</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mapElo.modeRatings.rows.slice(0, 10).map((r) => (
                      <tr
                        key={`${r.team_id}-${r.mode}`}
                        className="border-b border-hairline/60"
                      >
                        <td className="py-2 pr-4">{r.team}</td>
                        <td className="py-2 pr-4">
                          {modeLabel(modeCatalog, r.mode)}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {r.n_maps}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {r.rating.toFixed(0)}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {r.global_rating.toFixed(0)}
                        </td>
                        <td className="py-2 text-right font-mono tabular-nums">
                          {r.delta > 0 ? "+" : "−"}
                          {Math.abs(r.delta).toFixed(0)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="eyebrow text-ink-secondary">
              What the extra sample does buy
            </h3>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              A map Brier can&rsquo;t be read against a series Brier, so each arm
              also rolls up. At the moment a series starts the ratings are
              frozen, a win probability is computed for each of the five maps
              the title&rsquo;s rotation would play, and those combine into P(win
              the series) as a best-of-five. The rotation is a league rule known
              before the series starts; the number of maps <em>actually</em>{" "}
              played is never read, because it is the result — a series that went
              five was 3-2 by definition. All{" "}
              {mapElo.seriesRollup.n_series.toLocaleString()} series roll up.
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full max-w-3xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">
                      Model, on the same series
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">Brier</th>
                    <th className="py-2 text-right font-normal">Accuracy</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(mapElo.seriesRollup.gaps.models ?? {})
                    .sort((a, b) => a[1].brier - b[1].brier)
                    .map(([name, m]) => (
                      <tr key={name} className="border-b border-hairline/60">
                        <td className="py-2 pr-4">
                          {GAP_LABEL[name] ?? name}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono tabular-nums">
                          {m.brier.toFixed(5)}
                        </td>
                        <td className="py-2 text-right font-mono tabular-nums">
                          {(m.accuracy * 100).toFixed(1)}%
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              {(() => {
                const p = mapElo.seriesRollup.gaps.pairs?.find(
                  (x) =>
                    (x.a === "map_blend" && x.b === "elo") ||
                    (x.a === "elo" && x.b === "map_blend"),
                );
                if (!p) return null;
                const d = p.a === "map_blend" ? p.delta : -p.delta;
                return (
                  <>
                    <strong className="text-ink">
                      Rating maps beats rating series.
                    </strong>{" "}
                    The blend arm beats Elo by {Math.abs(d).toFixed(5)} of
                    Brier, with an interval clear of zero and clear of its own
                    80%-power threshold ({p.mde80.toFixed(5)}) — the only model
                    gap on this page that passes both tests. Nothing about the
                    model changed; it saw 3.9× as many results. The contrasts{" "}
                    <em>within</em> map_elo do not clear power, so which of the
                    three arms is best is not settled here.
                  </>
                );
              })()}
            </p>
          </div>
        </section>
      )}

      {seriesDyn && (
        <section id="series-dynamics" className="mt-12">
          <h2 className="font-display text-2xl font-semibold uppercase">
            Series dynamics
          </h2>
          <div className="mt-3 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
            <p>
              A series is a race to three maps, and much of what gets said about
              one is a claim about the race: a 1-0 lead is worth more than
              arithmetic, teams ride momentum, a reverse sweep is a collapse
              rather than a coin landing the same way twice. Over the{" "}
              {seriesDyn.dynamics.n_series.toLocaleString()} best-of-fives whose
              maps reconstruct their scoreline exactly, each of those is measured
              against a race with no memory in it: both teams’ map-level
              Elo frozen before map 1, the league rotation for which maps get
              played, and an exact enumeration of every scoreline that race could
              reach.
            </p>
            <p>
              The map-1 winner takes{" "}
              <strong className="text-ink">
                {(seriesDyn.dynamics.map1.observed * 100).toFixed(1)}%
              </strong>{" "}
              of these series — and almost none of that is a dynamic. Between two
              identical teams a 1-0 lead is already worth{" "}
              {(seriesDyn.dynamics.map1.coin_flip * 100).toFixed(1)}%, by
              arithmetic. The ratings are also modest: their map-1 calibration
              slope is{" "}
              {seriesDyn.dynamics.strength_check.available
                ? seriesDyn.dynamics.strength_check.calibration_slope.toFixed(2)
                : "above 1"}
              , so real strength gaps are wider than the ratings say — and a team
              that wins map 1 is, on that evidence, better than its rating said.
              Every rate below is therefore stated twice: against the ratings, and
              against the wider strength gap that best explains the same archive
              with no carryover at all.
            </p>
          </div>

          <div className="mt-5 overflow-x-auto">
            <table className="w-full max-w-3xl text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Scoreline</th>
                  <th className="py-2 pr-4 text-right font-normal">Observed</th>
                  <th className="py-2 pr-4 text-right font-normal">
                    vs the ratings
                  </th>
                  <th className="py-2 text-right font-normal">
                    vs unmeasured quality
                  </th>
                </tr>
              </thead>
              <tbody>
                {seriesDyn.dynamics.rates
                  .filter((r) => r.event !== "team1_won")
                  .map((r) => (
                    <tr key={r.event} className="border-b border-hairline/60">
                      <td className="py-2 pr-4">
                        {SERIES_EVENT_LABEL[r.event] ?? r.event}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono tabular-nums">
                        {(r.observed * 100).toFixed(1)}%
                      </td>
                      {["rating", "quality"].map((key) => {
                        const g = r.vs[key];
                        if (!g) return <td key={key} />;
                        return (
                          <td
                            key={key}
                            className={`py-2 text-right font-mono tabular-nums ${
                              key === "rating" ? "pr-4" : ""
                            } ${g.excludes_zero ? "text-ink" : "text-ink-muted"}`}
                          >
                            {(g.expected * 100).toFixed(1)}%{" "}
                            <span className="text-xs">
                              ({g.delta >= 0 ? "+" : ""}
                              {(g.delta * 100).toFixed(1)} [
                              {(g.lo * 100).toFixed(1)},{" "}
                              {(g.hi * 100).toFixed(1)}])
                            </span>
                          </td>
                        );
                      })}
                    </tr>
                  ))}
              </tbody>
            </table>
            <p className="mt-2 max-w-3xl text-xs text-ink-muted">
              Expected rate, then the gap from it in percentage points with a 95%
              interval, resampled over series — the pairing matters, since both
              columns are computed on the same series. Gaps whose interval
              excludes zero are in full contrast. Against the ratings alone every
              signature of momentum is there: too many sweeps, too few deciders, a
              1-0 lead worth five points more than it should be. Against a
              strength gap wide enough to explain the archive with no memory in
              it, none of them survive.
            </p>
          </div>

          {seriesQuality && (
            <div className="mt-6 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
              <h3 className="font-display text-lg font-semibold uppercase text-ink">
                Momentum, separated from quality by shape
              </h3>
              <p>
                Quality a rating missed is a property of the <i>series</i>: every
                map of it, in any order. Carryover is a property of{" "}
                <i>adjacency</i>. So the whole sequence of map results is modelled
                at once — a per-series offset drawn from a normal, plus a lag term
                for who won the previous map — with the offset integrated out and
                the likelihood written over the sequence <i>including map 1</i>.
                That last part is not cosmetic: conditioning on map 1 instead
                would feed the latent quality straight into the lag term.
              </p>
              <p>
                Over {seriesQuality.n_maps.toLocaleString()} maps in{" "}
                {seriesQuality.n_series.toLocaleString()} series, the fit finds{" "}
                <strong className="text-ink">
                  {seriesQuality.full.sigma.toFixed(2)} logits
                </strong>{" "}
                of team quality the ratings did not have, and a carryover effect
                of{" "}
                <strong className="text-ink">
                  {seriesQuality.swing_pp >= 0 ? "+" : ""}
                  {seriesQuality.swing_pp.toFixed(1)} points
                </strong>{" "}
                of map win probability between a team that just won a map and one
                that just lost, 95% CI {seriesQuality.swing_pp_lo >= 0 ? "+" : ""}
                {seriesQuality.swing_pp_lo.toFixed(1)} to{" "}
                {seriesQuality.swing_pp_hi >= 0 ? "+" : ""}
                {seriesQuality.swing_pp_hi.toFixed(1)}, likelihood-ratio p&nbsp;=&nbsp;
                {seriesQuality.p.toFixed(2)}. Fitted on maps 1-3 only — the one
                panel with no stopping rule, since every best-of-five plays all
                three — it is {seriesQuality.first_three.swing_pp >= 0 ? "+" : ""}
                {seriesQuality.first_three.swing_pp.toFixed(1)} pt [
                {seriesQuality.first_three.swing_pp_lo.toFixed(1)},{" "}
                {seriesQuality.first_three.swing_pp_hi.toFixed(1)}].
              </p>
              {seriesNaive && (
                <p>
                  The same data regressed the ordinary way — map 2 on the frozen
                  strength logit and the map-1 result, with no series offset —
                  puts winning map 1 at{" "}
                  <strong className="text-ink">
                    {seriesNaive.swing_pp !== undefined && seriesNaive.swing_pp >= 0
                      ? "+"
                      : ""}
                    {seriesNaive.swing_pp?.toFixed(1)} pt
                  </strong>
                  {seriesNaive.p !== null && (
                    <>, p&nbsp;=&nbsp;{seriesNaive.p.toFixed(4)}</>
                  )}
                  . The gap between that and the number above is the finding: the
                  effect is the two teams being further apart than the rating
                  knew, not one map carrying into the next.
                </p>
              )}
              <p>
                {seriesDyn.dynamics.n_series.toLocaleString()} series could have
                resolved a carryover worth{" "}
                {seriesQuality.mde80_swing_pp?.toFixed(1)} points at 80% power. So
                the claim is that momentum inside a series is not worth as much as
                a moderate effect would be — not that it is exactly zero.
              </p>
            </div>
          )}
        </section>
      )}

      {(() => {
        if (!styleArt) return null;
        // One fit per era: the metric layer's columns change wholesale at the
        // archive seam, so the numbers below are quoted for the earliest
        // published era and the others are tabulated beneath it.
        const publishedAll = styleArt.style.bases.filter((b) =>
          styleArt.style.published_bases.includes(b.basis),
        );
        const published = publishedAll[0];
        if (!published) return null;
        const extended = styleArt.style.bases.find(
          (b) =>
            b.league === published.league &&
            !styleArt.style.published_bases.includes(b.basis),
        );
        const era = styleArt.style.eras.find((e) => e.league === published.league);
        const scored = published.clustering.filter(
          (c) => c.silhouette !== null && c.silhouette_null !== null,
        );
        if (scored.length === 0 || !era) return null;
        const best = scored.reduce((a, b) =>
          (b.silhouette ?? 0) > (a.silhouette ?? 0) ? b : a,
        );
        return (
          <section id="player-style" className="mt-12">
            <h2 className="font-display text-2xl font-semibold uppercase">
              Player style
            </h2>
            <div className="mt-3 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
              <p>
                Every roster in this sport is described in nouns — anchor, entry,
                flex, objective player. This section asks the narrower question
                the archive can answer: do the box scores fall into groups, or
                into a cloud? k-means returns k clusters for any k, on any data,
                including data with no structure at all, so a partition is never
                by itself evidence of one. What is published is the comparison
                between the partition this archive gives up and the partition the{" "}
                <em>same cloud with no groups in it</em> gives up.
              </p>
              <p>
                Quality goes first. Cluster raw box scores and the leading axis
                is &ldquo;more kills, better ratio, larger share&rdquo; with every
                metric loading the same way — that is a rating, and the
                archetypes would come out as tiers. So every feature is
                residualised against the published composite rating, which
                explains just{" "}
                <strong className="text-ink">
                  {(published.quality_share * 100).toFixed(1)}%
                </strong>{" "}
                of the variance here. Style and quality are very nearly
                orthogonal, and what gets clustered is how a player played at
                their level rather than what level that was.
              </p>
              <p>
                The era goes next, and costs more than it sounds. Coverage is not
                flat: demand a complete row across every metric present in every
                season and no player-season qualifies. A column is admitted only
                if it is attainable in every season of its era, and the published
                fit runs on the basis that keeps the league — for {published.era},{" "}
                {published.n_columns} box-score metrics over{" "}
                {published.n.toLocaleString()} of{" "}
                {era.n_subjects_total.toLocaleString()} qualified player-seasons —
                rather than the one with the most columns.
              </p>
              <p>
                The two archives do not share a column set: the{" "}
                {styleArt.style.eras[0]?.era} box scores carry the kill feed and
                the streak and accuracy extras, and the{" "}
                {styleArt.style.eras[1]?.era}{" "}
                box scores carry damage, contested
                hill time and non-traded kills but no map clock. So the fit is
                run once per era and the axes are never compared across the seam
                — a player who spans it is drawn on their most recent era&rsquo;s
                axes.
              </p>
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[28rem] text-left text-sm">
                  <thead>
                    <tr className="border-b border-hairline text-xs text-ink-muted">
                      <th className="py-2 pr-4 font-normal">Era</th>
                      <th className="py-2 pr-4 text-right font-normal">Columns</th>
                      <th className="py-2 pr-4 text-right font-normal">
                        Player-seasons
                      </th>
                      <th className="py-2 pr-4 text-right font-normal">Axes</th>
                      <th className="py-2 text-right font-normal">Archetypes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {publishedAll.map((b) => (
                      <tr key={b.basis} className="border-b border-hairline/60">
                        <td className="py-2 pr-4 font-mono text-xs">{b.era}</td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {b.n_columns}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {b.n.toLocaleString()}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {b.n_components}
                        </td>
                        <td className="py-2 text-right font-mono text-xs">
                          {b.taxonomy ? b.surviving_k : "none"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>
                <strong className="text-ink">There is no taxonomy</strong>, in
                either era. Taking {published.era} as the worked example: the
                gap statistic prefers k={published.gap_k} to every partition up to{" "}
                {published.clustering.length}. The best silhouette any k reaches
                is {best.silhouette?.toFixed(3)}, at k={best.k}, and a single
                Gaussian with the same covariance and sample size scores{" "}
                {best.silhouette_null?.lo.toFixed(3)}&ndash;
                {best.silhouette_null?.hi.toFixed(3)} on the same test: the
                separation observed is what no separation looks like. Bootstrap
                stability at k={best.k} is {Math.min(...best.stability).toFixed(3)}{" "}
                and on its own means nothing — bisecting an elongated cloud along
                its long axis is enormously reproducible, and the null reproduces
                itself just as well, at{" "}
                {best.stability_null?.lo.toFixed(3)}&ndash;
                {best.stability_null?.hi.toFixed(3)}.
              </p>
              {extended && (
                <p>
                  The extended {published.era} basis — {extended.n_columns}{" "}
                  columns including Hardpoint and Search and Destroy objective
                  metrics, at the cost of falling to{" "}
                  {extended.n.toLocaleString()} player-seasons — agrees, and
                  nothing is published from it either.
                </p>
              )}
              <p>
                What is real is the axes. Horn&rsquo;s parallel analysis — each
                eigenvalue against the 95th percentile of the same matrix with
                every column independently permuted — retains{" "}
                {published.n_components} components for {published.era},
                together{" "}
                {(
                  published.component_share.reduce((a, b) => a + b, 0) * 100
                ).toFixed(1)}
                % of the residual variance. Only the {published.era}{" "}
                axes are
                named: names were read off that fit&rsquo;s loadings, and an
                index carries no reading to a fit nobody has read. A player is
                drawn on{" "}
                <Link className="underline" href="/players">
                  their page
                </Link>{" "}
                as a position on those axes rather than a label.
              </p>
            </div>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[28rem] text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Era</th>
                    <th className="py-2 pr-4 font-normal">Axis</th>
                    <th className="py-2 pr-4 text-right font-normal">Share</th>
                    <th className="py-2 font-normal">Heaviest loadings</th>
                  </tr>
                </thead>
                <tbody>
                  {publishedAll.flatMap((b) =>
                    b.axes.map((axis) => (
                      <tr
                        key={`${b.basis}:${axis.index}`}
                        className="border-b border-hairline/60"
                      >
                        <td className="py-2 pr-4 font-mono text-xs text-ink-muted">
                          {axis.index === 1 ? b.era : ""}
                        </td>
                        <td className="py-2 pr-4 font-mono text-xs">{axis.name}</td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {(axis.share * 100).toFixed(1)}%
                        </td>
                        <td className="py-2 text-xs text-ink-secondary">
                          {axis.loadings
                            .slice(0, 4)
                            .map((l) => l.column.split(":")[1])
                            .join(", ")}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          </section>
        );
      })()}

      <section id="primacy" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Which rating is the rating
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            This site publishes three player ratings. They are not three
            attempts at one number: each names a different object, is judged by
            a different test, and has a different known failure. The rule below
            is which one a page leads with, and it is stated here because
            leaving it implicit would let whichever board renders first become
            the answer by accident.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Rating</th>
                  <th className="py-2 pr-4 font-normal">Answers</th>
                  <th className="py-2 pr-4 font-normal">Judged by</th>
                  <th className="py-2 font-normal">Known failure</th>
                </tr>
              </thead>
              <tbody>
                {(["skill", "value", "decomposition"] as const).map((id) => {
                  const r = RATINGS[id];
                  return (
                    <tr key={id} className="border-b border-hairline/60 align-top">
                      <td className="py-2 pr-4 font-medium">
                        <a href={r.href} className="underline">
                          {r.name}
                        </a>
                      </td>
                      <td className="py-2 pr-4">{r.question}</td>
                      <td className="py-2 pr-4 text-ink-muted">{r.judge}</td>
                      <td className="py-2 text-ink-muted">{r.failure}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p>
            <strong className="text-ink">The demotion, stated plainly.</strong>{" "}
            The composite season rating — feature set v
            {PUBLISHED_RATING_VERSION}, described in the section below — used to
            be the rating this site led with. It is not any more. It answers
            &ldquo;what was that season worth&rdquo;, which is a question about
            a season already played, and readers were using it for &ldquo;who is
            good now&rdquo;, which it was never fitted to answer. It has not
            changed, it has not been withdrawn, and every number it produced is
            still on this site. It has been moved behind the rating that claims
            the forward question.
          </p>
          {skillSeasons.length > 0 && (
            <p>
              <strong className="text-ink">The rule degrades by era.</strong>{" "}
              SKILL exists for {skillSeasons[0].year}–
              {skillSeasons[skillSeasons.length - 1].year} and for no season
              before that.{" "}
              {primacyReason(skillSeasons[0].year - 1, new Set(skillYears))} So
              a CWL-era player page leads with the season rating, and says so
              rather than showing an empty panel where SKILL would be.
            </p>
          )}
          <p>
            And the rating now in front is the one that lost its own test. SKILL
            was declared on next-season persistence before it was fitted, and it
            scored below raw K/D z on that test; the numbers are in the{" "}
            <a href="#skill" className="underline">
              SKILL section
            </a>
            , next to the rating rather than only here. It leads because it is
            the only one of the three that answers the forward question, not
            because it answers it well.
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
              Inside each cohort, a two-level normal-normal model treats those
              scores as noisy reads of a true skill that is itself spread across
              the league. Its posterior is the rating: partial pooling, so a
              12-map sample cannot outrank a 200-map season, with the interval
              arriving in the same closed form rather than from a bootstrap
              added afterwards — see below.
            </li>
            <li>
              Mode posteriors blend by maps played, centred so the qualified
              league averages 1.00 and scaled so one rating point is 0.15 of
              that cohort&rsquo;s estimated true-skill spread. The ±sd is the
              posterior&rsquo;s, on per-mode rows as well as the blend.
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
              <WhatWinsMaps cohorts={modeWeights} seasons={seasonEras} />
            </div>
            {weightCiCohorts.length > 0 && (
              <p className="mt-4 max-w-3xl text-sm leading-relaxed text-ink-secondary">
                Each ratio is fitted on between{" "}
                {Math.min(...weightCiCohorts.map((c) => c.nMaps)).toLocaleString()}{" "}
                and{" "}
                {Math.max(...weightCiCohorts.map((c) => c.nMaps)).toLocaleString()}{" "}
                maps, over features the caveat above already calls collinear, so it
                ships with a 95% percentile bootstrap: resample a cohort&rsquo;s maps
                with replacement, refit the standardization and the ridge end to end,
                recompute the ratio, 200 draws. Without the interval, a ratio
                measured on a full title would read the same as one measured on a
                single event&rsquo;s worth of maps.{" "}
                {weightCiCohorts.length - unresolvedCohorts.length} of{" "}
                {weightCiCohorts.length} intervals exclude 1×, so the side they land
                on survives.{" "}
                {unresolvedCohorts.length > 0 && (
                  <>
                    The {unresolvedCohorts.length === 1 ? "exception" : "exceptions"}{" "}
                    — {unresolvedCohorts.map((c) => `${c.year} ${c.mode}`).join(", ")}{" "}
                    — {unresolvedCohorts.length === 1 ? "is" : "are"} drawn faded and
                    publish no finding at all: which half carried those maps is
                    unresolved.
                  </>
                )}
              </p>
            )}
          </div>
        )}
        {posterior && posterior.cohorts.length > 0 && (
          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="font-display text-xl font-semibold uppercase">
              The rating is a posterior
            </h3>
            <div className="mt-3 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
              <p>
                This page named hierarchical and Bayesian statistics as the right
                tools for a dataset this size, then shipped a z-score multiplied
                by a shrinkage factor. Both pieces had the right instinct and
                neither was a model — and they worked against each other, since
                the z-score divided by the <em>observed</em> spread of season
                scores, which is inflated by exactly the per-map noise the
                shrinkage step exists to discount.
              </p>
              <p>
                One two-level model states all of it at once. Within a cohort, a
                player&rsquo;s per-map score is a noisy read of a true skill,
                σ² wide; true skills are spread across the league, τ² wide. The
                posterior for a season of m maps is then closed form — the
                observed score pulled toward the league mean by
                B = τ²/(τ² + σ²/m), with variance B·σ²/m — so the pooling{" "}
                <em>is</em> the model rather than a correction applied to one,
                and the interval needs no bootstrap.
              </p>
              <p>
                Two things follow that the old estimator could not say. A rating
                point is now 0.15 of the estimated <em>true</em> spread rather
                than of the observed one, which makes τ over the observed spread
                a publishable number in its own right: how much of the
                leaderboard&rsquo;s range is real difference between players
                {signalRange}. And the published ± is now the
                posterior&rsquo;s, which is a different quantity from the
                bootstrap it replaces: resampling a shrunk estimate measures how
                far it would move on other maps, while the posterior measures
                what is still unknown about the player{intervalFactor}. Every
                band drawn from the old column was too tight.
              </p>
              {moved && (
                <p>
                  Switching estimators moved the published ratings by{" "}
                  {moved.mean_abs_delta.toFixed(3)} on average and{" "}
                  {moved.max_abs_delta.toFixed(3)} at most, on a scale whose
                  league SD is 0.15{orderingNote}. It is a re-estimation, not a
                  re-ranking. Whether it forecasts better is a separate
                  question, tested out of sample below.
                </p>
              )}
            </div>
            <h4 className="eyebrow mt-5 text-ink-secondary">
              Where the interval is drawn
            </h4>
            <div className="mt-2 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
              <p>
                Storing an interval and publishing a point estimate is the same
                mistake as not having one, so the posterior travels with the
                rating wherever the rating appears: the{" "}
                <Link href="/players" className="underline">
                  rating board
                </Link>{" "}
                and the player index, and a per-season plot on every player
                page. Every band on this site is ±1.96 SD of the quantity beside
                it, drawn on a domain shared by the whole table or plot — a band
                comparable only to itself hides the one thing it is for.
              </p>
              <p>
                The same rule covers the era model&rsquo;s standard error on the
                career arc, the season tables and the home leaderboard, and
                Glicko-2&rsquo;s RD on the team pages. Two intervals count as
                separated only when they do not touch, which is the conservative
                direction; on the rating board most of the order below the
                leader does not clear that bar. A season the model stored no SD
                for is drawn as a point with no band, never a zero-width one,
                which would read as certainty — and Elo carries no band at all,
                because the estimator does not produce one, which is why the
                team pages publish Glicko-2 next to it.
              </p>
            </div>
            <h4 className="eyebrow mt-5 text-ink-secondary">
              How many maps a season needs
            </h4>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              k = σ²/τ² is the maps at which a season keeps half of what it
              measured, and it is read off the fit rather than asserted — this
              page fixed it at 15 for every cohort until the model that implies
              it existed.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full max-w-3xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Cohort</th>
                    <th className="py-2 pr-4 text-right font-normal">Players</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      k (maps to keep half)
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">
                      vs the old {shrinkage?.fallback ?? 15}
                    </th>
                    <th className="py-2 text-right font-normal">Signal share</th>
                  </tr>
                </thead>
                <tbody>
                  {posterior.cohorts.map((c) => (
                    <tr
                      key={`${c.season_id}-${c.mode_id}`}
                      className="border-b border-hairline/60"
                    >
                      <td className="py-2 pr-4 whitespace-nowrap">
                        {c.year} {c.title} · {c.mode}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                        {c.n_players}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                        {c.implied_k.toFixed(1)}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                        {(() => {
                          const gap = c.implied_k - (shrinkage?.fallback ?? 15);
                          return `${gap > 0 ? "+" : ""}${gap.toFixed(1)}`;
                        })()}
                      </td>
                      <td className="py-2 text-right font-mono text-xs tabular-nums text-ink-secondary">
                        {c.signal_share === null
                          ? "—"
                          : `${(c.signal_share * 100).toFixed(0)}%`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 max-w-3xl text-xs text-ink-muted">
              The old constant was close for the respawn modes and far too weak
              for Search &amp; Destroy, where a round-scale scoreline with four
              players a side is noisy enough that a season needs two to three
              times as many maps to say the same thing about a player. Fitted
              from every player in the cohort, not only the qualified ones: the
              pooling is applied to short seasons, so measuring its strength on
              long seasons alone would measure a different population.
              {posterior.calibration.available && (
                <>
                  {" "}
                  The one assumption the closed form makes — that a season
                  profile&rsquo;s sampling variance is σ²/m — is measured rather
                  than asserted, by resampling each player&rsquo;s own maps:{" "}
                  {posterior.calibration.median.toFixed(3)}× overall (
                  {posterior.calibration.min.toFixed(3)}–
                  {posterior.calibration.max.toFixed(3)} by cohort), and σ² is
                  scaled by it before the fit. It matters: an overstated
                  observation variance does not merely widen intervals, it eats
                  the between-player variance and reports a cohort as flatter
                  than it is.
                </>
              )}
            </p>
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
              earlier events in the same (season × mode). This establishes one
              narrow thing — that the learned weights generalize to events they
              were not trained on, which is what the rating relies on — and it is
              not a forecast. Some features are the win condition itself, so the
              accuracy is largely a decomposition of the final score. Read it with
              the leakage table below.
            </p>
            <div className="mt-4">
              <Calibration bins={c.calibration} unit="maps" />
            </div>
          </div>
        ))}

        {comparison && (
          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="font-display text-xl font-semibold uppercase">
              Does adding intangibles help?
            </h3>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              {comparison.versions.length} feature sets, scored walk-forward on
              the {comparison.common_maps.toLocaleString()} maps every one of
              them predicts — versions have different data requirements, and
              comparing raw totals would let a version look better by quietly
              predicting an easier subset. v{comparison.baseline} is the original
              box-score model; v2.0.0 adds the measured per-mode metric features;
              v2.1.0 adds kill-feed trades where a reconciled feed exists; v2.2.0
              claims the damage, accuracy and non-traded-kill columns both
              archives already populate.
              {brierGain !== null && (
                <>
                  {" "}
                  The published v{comparison.published} cuts Brier score{" "}
                  {(brierGain * 100).toFixed(0)}% against the baseline. That
                  measures how well each set <em>describes</em> a map, not how well
                  it forecasts one — part of the gain is a leaked column being
                  divided by a better denominator, which the table below sizes.
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

            {deferredVersions.length > 0 && (
              <p className="mt-4 max-w-3xl text-sm leading-relaxed text-ink-secondary">
                {deferredVersions.map((d) => (
                  <span key={d.version}>
                    v{d.version} is fitted and not published: it scores{" "}
                    {d.brierVsPublished > 0 ? "+" : ""}
                    {d.brierVsPublished.toFixed(4)} Brier against v
                    {comparison.published} on these same maps.{" "}
                  </span>
                ))}
                A version is not demoted for that alone — this table rewards
                columns that already know the result, and the newer columns are
                the less leaky ones. It stays unpublished because promoting a
                version changes what every leaderboard on this site means, and a
                feature set does not earn that by losing the one test it was
                given.
              </p>
            )}

            <h4 className="mt-6 eyebrow text-ink-secondary">By cohort</h4>
            <p className="mt-2 max-w-3xl text-xs text-ink-muted">
              “v{comparison.published} wins” is a weaker claim than
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

        {signBaseline && signBaseline.by_cohort.length > 0 && (
          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="font-display text-xl font-semibold uppercase">
              What the map backtest does not establish
            </h3>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              A 94% map accuracy invites a reading it does not support. Several
              features <em>are</em> the win condition — Capture the Flag is won by
              scoring the most captures, Hardpoint awards a point per second of
              hill control — so the accuracy above is largely a decomposition of
              the final score. To size that, every feature is put through the
              crudest baseline available: take the sign of the team differential
              on that one column, call it the winner, and score it. No weights,
              no fitting, nothing learned, on exactly the maps the fitted model
              predicted.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full max-w-3xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Cohort</th>
                    <th className="py-2 pr-4 text-right font-normal">Maps</th>
                    <th className="py-2 pr-4 text-right font-normal">Model</th>
                    <th className="py-2 pr-4 font-normal">
                      Best single column, by sign alone
                    </th>
                    <th className="py-2 text-right font-normal">Model gain</th>
                  </tr>
                </thead>
                <tbody>
                  {signBaseline.by_cohort.map((c) => {
                    const best = c.best_feature;
                    if (!best || c.model_gain === null) return null;
                    // A non-positive gain means one unfitted column beat the
                    // whole model. Those rows are the point of the table.
                    const leaks = c.model_gain <= 0;
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
                          {(c.model_accuracy * 100).toFixed(1)}%
                        </td>
                        <td className="py-2 pr-4">
                          <span
                            className={`font-mono text-xs tabular-nums ${
                              leaks ? "text-ink" : "text-ink-secondary"
                            }`}
                          >
                            {(best.accuracy * 100).toFixed(1)}%
                          </span>
                          <span className="ml-2 text-xs text-ink-muted">
                            {best.label}
                          </span>
                        </td>
                        <td
                          className={`py-2 text-right font-mono text-xs tabular-nums ${
                            leaks ? "text-ink" : "text-ink-secondary"
                          }`}
                        >
                          {c.model_gain > 0 ? "+" : ""}
                          {(c.model_gain * 100).toFixed(1)} pt
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="mt-3 max-w-3xl text-xs leading-relaxed text-ink-muted">
              Rows where the model gains nothing are the ones to read. Across the
              WWII Capture the Flag maps the sign of the capture differential was
              never once wrong, because outscoring the opponent in captures is the
              definition of winning that mode — the fitted model, splitting weight
              between collinear features, does worse than the identity buried
              inside it. Elsewhere the whole model adds one to two points over its
              single best column, and a plain kill differential alone picks the
              winner on {slayRange}. The correct summary is that this backtest
              confirms the learned weights transfer to unseen events, and says
              nothing about whether the rating can predict a result before it
              happens. The two tests it can actually fail are below.
            </p>
          </div>
        )}

        {persistence && persistence.n_pairs > 0 && (
          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="font-display text-xl font-semibold uppercase">
              Test 1: does the rating persist?
            </h3>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              For every player with two consecutive seasons and at least{" "}
              {persistence.min_maps_each_side} maps on each side, season{" "}
              <em>N</em> predicts season <em>N+1</em>. Two predictors against two
              targets: predicting next season&rsquo;s rating flatters the rating
              and predicting next season&rsquo;s K/D flatters K/D, so the
              off-diagonal is where the question lives. {persistence.n_pairs}{" "}
              transitions (
              {persistence.transitions
                .filter((t) => t.n > 0)
                .map((t) => `${t.n} across ${t.from_title} → ${t.to_title}`)
                .join(", ")}
              ), Pearson <em>r</em> with a{" "}
              {persistence.bootstrap_b.toLocaleString()}-draw bootstrap over
              players.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full max-w-2xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">
                      Predictor (season <em>N</em>)
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">
                      → next rating
                    </th>
                    <th className="py-2 text-right font-normal">→ next K/D z</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ["Composite rating", "rating"],
                    ["Era-adjusted K/D z", "kd"],
                  ].map(([label, from]) => (
                    <tr key={from} className="border-b border-hairline/60">
                      <td className="py-2 pr-4">{label}</td>
                      {["rating", "kd"].map((to) => {
                        const c = persistence.cells[`${from}->${to}`];
                        // The winner of each column is emphasized; on this test
                        // that is K/D in both, which is the finding.
                        const other =
                          persistence.cells[
                            `${from === "rating" ? "kd" : "rating"}->${to}`
                          ];
                        const best =
                          c?.r !== null &&
                          other?.r !== null &&
                          (c?.r ?? 0) > (other?.r ?? 0);
                        return (
                          <td
                            key={to}
                            className={`py-2 text-right font-mono text-xs tabular-nums ${
                              to === "rating" ? "pr-4" : ""
                            } ${best ? "text-ink" : "text-ink-secondary"}`}
                          >
                            {c?.r === null || c === undefined ? (
                              "—"
                            ) : (
                              <>
                                {c.r.toFixed(2)}
                                <span className="ml-2 text-ink-muted">
                                  [{c.lo?.toFixed(2)}, {c.hi?.toFixed(2)}]
                                </span>
                              </>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 max-w-3xl text-xs leading-relaxed text-ink-muted">
              The contrasts are paired — the same resampled players scoring both
              predictors, because comparing two intervals that happen to overlap
              answers nothing.{" "}
              {["kd", "rating"].map((target) => {
                const k = persistence.contrasts[target];
                if (!k || k.delta_r === null) return null;
                return (
                  <span key={target}>
                    {k.what.replace("−", "minus")} = {k.delta_r.toFixed(2)} [
                    {k.lo?.toFixed(2)}, {k.hi?.toFixed(2)}],{" "}
                    {k.excludes_zero
                      ? "which excludes zero — a real difference."
                      : "which spans zero — no measurable difference."}{" "}
                  </span>
                );
              })}
              Raw K/D z is the better predictor in both columns, including the one
              that is the rating&rsquo;s own output.
            </p>
          </div>
        )}

        {forecast && forecast.common.available && forecast.contrasts.available && (
          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="font-display text-xl font-semibold uppercase">
              Test 2: does a roster predict future map wins?
            </h3>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              Walk-forward by event within each season: at every event the whole
              rating pipeline is refit on maps from earlier events only, each
              team&rsquo;s players are averaged into a roster strength for that
              map&rsquo;s mode, and the differential becomes a win probability
              through a logistic also fit on those earlier maps. Nothing from the
              event being scored enters.{" "}
              {forecast.common.n_maps.toLocaleString()} maps survive on which every
              predictor has an opinion; the first event of each season has no
              history and is skipped.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full max-w-2xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Predictor</th>
                    <th className="py-2 pr-4 text-right font-normal">Brier</th>
                    <th className="py-2 pr-4 text-right font-normal">Log loss</th>
                    <th className="py-2 text-right font-normal">Accuracy</th>
                  </tr>
                </thead>
                <tbody>
                  {FORECAST_PREDICTORS.map(([key, label]) => {
                    const s = forecast.common.available
                      ? forecast.common.predictors[key]
                      : undefined;
                    const a = forecast.contrasts.available
                      ? forecast.contrasts.accuracy[key]
                      : undefined;
                    if (!s) return null;
                    return (
                      <tr key={key} className="border-b border-hairline/60">
                        <td className="py-2 pr-4">{label}</td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                          {s.brier.toFixed(5)}
                        </td>
                        <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                          {s.log_loss.toFixed(4)}
                        </td>
                        <td className="py-2 text-right font-mono text-xs tabular-nums">
                          {(s.accuracy * 100).toFixed(1)}%
                          {a && (
                            <span className="ml-2 text-ink-muted">
                              [{((a.lo ?? 0) * 100).toFixed(1)},{" "}
                              {((a.hi ?? 0) * 100).toFixed(1)}]
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                  <tr className="border-b border-hairline/60 text-ink-muted">
                    <td className="py-2 pr-4">Coin flip at 0.5</td>
                    <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                      {forecast.coin_flip_brier.toFixed(5)}
                    </td>
                    <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                      0.6931
                    </td>
                    <td className="py-2 text-right">—</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p className="mt-3 max-w-3xl text-xs leading-relaxed text-ink-muted">
              A paired bootstrap over maps (
              {forecast.contrasts.bootstrap_b.toLocaleString()} draws) puts the
              rating at{" "}
              {["coin_flip", "glicko", "kd"].map((key) => {
                const c = forecast.contrasts.available
                  ? forecast.contrasts.brier[key]
                  : undefined;
                if (!c) return null;
                const name =
                  key === "coin_flip"
                    ? "a coin flip"
                    : key === "glicko"
                      ? "Glicko-2"
                      : "roster K/D";
                return (
                  <span key={key}>
                    {c.delta > 0 ? "+" : ""}
                    {c.delta.toFixed(5)} [{c.lo?.toFixed(4)}, {c.hi?.toFixed(4)}]
                    against {name}
                    {key === "kd" ? ". " : "; "}
                  </span>
                );
              })}
              Every interval spans zero.{" "}
              {rapmVsFlip && (
                <>
                  RAPM is the best row in the table and still does not clear the bar:{" "}
                  {rapmVsFlip.delta.toFixed(5)} [{rapmVsFlip.lo?.toFixed(4)},{" "}
                  {rapmVsFlip.hi?.toFixed(4)}] against a coin flip is the largest gap
                  any predictor manages and its interval very nearly excludes zero,
                  but{" "}
                  {rapmVsFlip.mde80 !== null && rapmVsFlip.mde80 !== undefined && (
                    <>
                      {forecast.common.available &&
                        forecast.common.n_maps.toLocaleString()}{" "}
                      maps could only have resolved a gap of{" "}
                      {rapmVsFlip.mde80.toFixed(4)}, so the honest reading is
                      &ldquo;closest, not established&rdquo;.{" "}
                    </>
                  )}
                </>
              )}
              Brier and accuracy disagree here, and
              reporting either alone would mislead: every predictor picks the
              winner more often than chance, so roster strength does carry
              directional signal — but the fitted logistic finds so little to work
              with that its output barely leaves 0.5, which is what a Brier at the
              floor with an above-chance pick rate means. A single map in this
              league is close to a coin flip, and knowing which four players are on
              the server does not measurably change that. See{" "}
              <a
                href="https://github.com/WillTufff/COD-DataAnalysis/blob/main/docs/methodology.md"
                className="underline"
              >
                the methodology doc
              </a>{" "}
              for what this does and does not say about the rating.
            </p>
          </div>
        )}

        {rapm?.available && (
          <div id="rapm" className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="font-display text-xl font-semibold uppercase">
              Plus-minus: value in wins, without the box score
            </h3>
            <div className="mt-3 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
              <p>
                Every other player number here starts from the scoreboard. RAPM asks
                the question from the other end: forget what a player did, and look
                only at whether their side won and who else was on the server. One
                row per map, one column per player, +1 for one side and &minus;1 for
                the other, ridge-regressed on the map result. A coefficient is a
                player&rsquo;s estimated contribution to the log-odds of winning a
                map, holding the other seven constant — no box-score column enters at
                any point. {rapm.n_maps.toLocaleString()} decided maps,{" "}
                {rapm.n_players} players with at least {rapm.min_maps} of them.
              </p>
              <p>
                Two things decide how much the leaderboard means.
              </p>
            </div>

            <dl className="mt-4 grid max-w-3xl gap-4 sm:grid-cols-2">
              <div className="border border-hairline p-3">
                <dt className="text-xs uppercase text-ink-muted">Collinearity</dt>
                <dd className="mt-1 text-sm text-ink-secondary">
                  Players who never appear apart are one column wearing several
                  names, and ridge splits the credit evenly. Median teammate
                  concentration is{" "}
                  <strong className="font-mono">
                    {rapm.concentration_median.toFixed(2)}
                  </strong>
                  , and <strong>{rapm.n_concentrated}</strong> of {rapm.n_players}{" "}
                  players sit at 0.90 or above — their coefficient is their
                  lineup&rsquo;s, not theirs.
                </dd>
              </div>
              <div className="border border-hairline p-3">
                <dt className="text-xs uppercase text-ink-muted">Shrinkage</dt>
                <dd className="mt-1 text-sm text-ink-secondary">
                  Standard errors come from the penalized Hessian. The median is{" "}
                  <span className="font-mono">{rapm.se_median.toFixed(2)}</span>{" "}
                  against a coefficient spread of{" "}
                  <span className="font-mono">{rapm.coef_sd.toFixed(2)}</span>, and
                  only <strong>{rapm.n_resolved}</strong> of {rapm.n_players}{" "}
                  coefficients exceed 1.96 standard errors.
                </dd>
              </div>
            </dl>

            <p className="mt-4 text-sm leading-relaxed text-ink-secondary">
              The table below is the top and bottom {rapm.leaders.length}. The
              whole fit &mdash; all {rapm.n_players}{" "}
              players &mdash; is stored
              per player and drawn on each player&rsquo;s page with the interval
              attached, because a coefficient whose interval covers zero is not
              a ranking and {rapm.n_players - rapm.n_resolved} of these do.
              Note what the two diagnostics say together rather than apart:
              among the players named below,{" "}
              <strong>{rapmSeparableResolved}</strong>{" "}
              {rapmSeparableResolved === 1 ? "clears" : "clear"} 1.96 SE while
              also playing under {Math.round(RAPM_CONCENTRATION_LIMIT * 100)}%
              of their maps beside the same teammate. The rest of the
              significant coefficients are their lineup&rsquo;s as much as their
              own.
            </p>

            {rapm.ridge_path.length > 0 && (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full max-w-xl text-left text-sm">
                  <thead>
                    <tr className="border-b border-hairline text-xs text-ink-muted">
                      <th className="py-2 pr-4 font-normal">Ridge strength</th>
                      <th className="py-2 pr-4 text-right font-normal">
                        Coefficient spread
                      </th>
                      <th className="py-2 pr-4 text-right font-normal">Largest</th>
                      <th className="py-2 text-right font-normal">
                        Correlation with lightest
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rapm.ridge_path.map((e) => (
                      <tr key={e.l2} className="border-b border-hairline/60">
                        <td className="py-1.5 pr-4 font-mono text-xs tabular-nums">
                          {e.l2}
                        </td>
                        <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                          {e.sd.toFixed(3)}
                        </td>
                        <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                          {e.max_abs.toFixed(3)}
                        </td>
                        <td className="py-1.5 text-right font-mono text-xs tabular-nums text-ink-secondary">
                          {e.corr_with_l2_min === undefined
                            ? "—"
                            : e.corr_with_l2_min.toFixed(3)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 max-w-3xl text-xs leading-relaxed text-ink-muted">
                  The penalty is doing much of the work, and nothing here tunes it
                  against the held-out maps — that would turn the forecast above into
                  a selection statistic rather than a test.
                </p>
              </div>
            )}

            <p className="mt-4 max-w-3xl text-xs leading-relaxed text-ink-muted">
              {rapm.blend.available && (
                <>
                  The rating-centered variant shrinks each coefficient toward that
                  player&rsquo;s composite rating converted into map-win logits, at an
                  exchange rate estimated on the training maps rather than assumed.
                  Its coefficients correlate{" "}
                  {rapm.blend.corr_with_plain.toFixed(3)} with plain RAPM, and in the
                  forecast above it is worse on Brier and slightly better on accuracy.
                  It is reported because a mixed result is a result; it is not
                  adopted.{" "}
                </>
              )}
              <strong>What RAPM is actually measuring.</strong> At a median teammate
              concentration of {rapm.concentration_median.toFixed(2)}, a
              player&rsquo;s coefficient is substantially their lineup&rsquo;s. That
              explains the table above: RAPM&rsquo;s accuracy
              lands much closer to Glicko-2&rsquo;s team rating than to the box-score
              rating&rsquo;s, while never being told which team is playing. It
              behaves like a team rating expressed one player at a time — the best
              available answer to whether player-level information forecasts map
              wins, and a warning against reading its leaderboard as a ranking of
              individuals.
            </p>
          </div>
        )}
      </section>


      {seasonRapm && seasonRapm.available && (
        <section id="season-rapm" className="mt-12">
          <h2 className="font-display text-2xl font-semibold uppercase">
            Season plus-minus
          </h2>
          <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
            <p>
              The career plus-minus above gives a player one number for the whole
              archive. This one gives them one per time cell: {seasonRapm.what}.
            </p>
            <p>
              The cell is the finding. A season-resolution coefficient needs
              enough lineup variety inside the season to separate a player from
              the four they play beside, and the CWL years do not have it — so
              those seasons are fitted as one era-wide cell:{" "}
              {seasonRapm.graphs
                .filter((g) => g.resolution === "era")
                .map((g) => g.cell)
                .join(", ")}
              . One coefficient stands for every season inside it, so a page
              showing a CWL player a &ldquo;2018 plus-minus&rdquo; would be
              showing them a three-season average with a season&rsquo;s label on
              it.
            </p>
            {preflight && (
              <div className="border border-hairline bg-surface p-4">
                <h3 className="font-display text-lg font-semibold uppercase">
                  The pre-flight that set the cell
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-secondary">
                  {preflight.what}. Measured before the model was fitted, and
                  binding on it: {preflight.reason}.
                </p>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full max-w-2xl text-left text-sm">
                    <thead>
                      <tr className="border-b border-hairline text-xs text-ink-muted">
                        <th className="py-2 pr-4 font-normal">Era</th>
                        <th className="py-2 pr-4 text-right font-normal">
                          Rank share
                        </th>
                        <th className="py-2 pr-4 text-right font-normal">
                          Effective lineups
                        </th>
                        <th className="py-2 pr-4 text-right font-normal">
                          Recovery within team
                        </th>
                        <th className="py-2 font-normal">Branch</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preflight.by_era.map((e) => (
                        <tr
                          key={e.league}
                          className="border-b border-hairline/60"
                        >
                          <td className="py-1.5 pr-4">{e.league}</td>
                          <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                            {e.rank_share.toFixed(4)}
                          </td>
                          <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                            {e.median_effective_lineups.toFixed(2)}
                          </td>
                          <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                            {e.recovery_within_team.toFixed(4)}
                          </td>
                          <td className="py-1.5 font-mono text-xs text-ink-secondary">
                            {e.branch}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 max-w-3xl text-xs leading-relaxed text-ink-muted">
                  Against thresholds of {preflight.thresholds.rank_share} rank
                  share, {preflight.thresholds.effective_lineups} effective
                  lineups and {preflight.thresholds.recovery_within_team}{" "}
                  within-team recovery, every era fails at least one — and the
                  CWL era fails all of them, recovering nothing at all within a
                  roster. The phase as specified was stopped by its own
                  pre-flight; what ships is the resolution each era can carry,
                  not the resolution that was planned.
                </p>
              </div>
            )}
            <div className="border border-hairline bg-surface p-4">
              <SpreadVsError
                rows={seasonRapm.by_cell.map((c) => ({
                  label: c.cell,
                  spread: c.coef_sd,
                  error: c.se_median,
                  note: `${c.player_columns} players`,
                }))}
                spreadLabel="spread of the cell's coefficients"
                errorLabel="the median standard error on one of them"
              />
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                Every circle sits to the right of its square. The uncertainty on
                one coefficient is larger than the distance between the
                coefficients, in every cell — which is the same statement the
                table below makes in two columns.
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Cell</th>
                    <th className="py-2 pr-4 font-normal">Resolution</th>
                    <th className="py-2 pr-4 text-right font-normal">Players</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Coefficient sd
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Median se
                    </th>
                    <th className="py-2 text-right font-normal">
                      Penalty-dominated
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {seasonRapm.by_cell.map((c) => (
                    <tr key={c.cell} className="border-b border-hairline/60">
                      <td className="py-1.5 pr-4">{c.cell}</td>
                      <td className="py-1.5 pr-4 font-mono text-xs text-ink-muted">
                        {c.resolution}
                      </td>
                      <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                        {c.player_columns}
                      </td>
                      <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                        {c.coef_sd.toFixed(4)}
                      </td>
                      <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                        {c.se_median.toFixed(4)}
                      </td>
                      <td className="py-1.5 text-right font-mono text-xs tabular-nums text-ink-secondary">
                        {(c.penalty_dominated_share * 100).toFixed(0)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs leading-relaxed text-ink-muted">
              Every cell&rsquo;s median standard error is larger than the spread
              of its own coefficients. That is the whole reading of this table:
              the estimator does not establish that the players inside a cell
              differ, and the last column says how much of each column is the
              ridge penalty rather than the maps. Split-half reliability on whole
              series is r = {seasonRapm.reliability.r.toFixed(4)} [
              {seasonRapm.reliability.lo.toFixed(4)},{" "}
              {seasonRapm.reliability.hi.toFixed(4)}] over{" "}
              {seasonRapm.reliability.n_player_cells} player-cells, which is a
              measurement that repeats about half the time.
            </p>
            <p>
              <strong className="text-ink">
                Two families are fitted; only one may be read forward.
              </strong>{" "}
              {seasonRapm.scopes.rule}. The smoothed family correlates{" "}
              {seasonRapm.scopes.smoothed_vs_filtered_r.toFixed(4)} with the
              filtered one, so the restriction costs almost nothing and removes
              a way of scoring a rating against a season it has already seen.{" "}
              {seasonRapm.publication.player_cells_published.toLocaleString()}{" "}
              player-cells clear the {seasonRapm.publication.min_maps}-map
              publication floor, written as{" "}
              {seasonRapm.publication.rows.toLocaleString()} rows across both
              families.
            </p>
            <p className="text-xs leading-relaxed text-ink-muted">
              The response is {seasonRapm.response.published}; the fit drops{" "}
              {seasonRapm.response.dropped_maps} maps where the margin is{" "}
              {seasonRapm.response.dropped_reason}. Two other responses were
              fitted and are reported rather than chosen from:{" "}
              {seasonRapm.response.sensitivity
                .map(
                  (s) =>
                    `${s.response} ranks ${s.rank_corr_with_published_response.toFixed(4)} against the published one`,
                )
                .join(", ")}
              . The penalty is chosen by {seasonRapm.penalties.chosen_by}, at{" "}
              {seasonRapm.penalties.effective_df.toFixed(1)} effective degrees of
              freedom over {seasonRapm.columns.total} columns.
            </p>
          </div>
        </section>
      )}

      {opponentAdjustment && (
        <section id="opponent-adjustment" className="mt-12">
          <h2 className="font-display text-2xl font-semibold uppercase">
            Opponent adjustment
          </h2>
          <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
            <p>
              What a stat line owed to who was across from it. This adjusts{" "}
              {opponentAdjustment.adjusts} — the plus-minus already conditions on
              opposition, since the opposing four are the −1 columns of its own
              design, so nothing here touches it.
            </p>
            <p>
              Four rungs were fitted rather than one chosen, and each is judged
              on three criteria declared before anything was fitted: it must move
              the leaderboard against the rung below it, what it moves must stand
              clear of its own placebo, and it must not leave the statistic less
              repeatable.
            </p>
            <div className="border border-hairline bg-surface p-4">
              <LadderMovement
                rungs={opponentAdjustment.stop_rule.per_rung.map((v) => ({
                  rung: v.rung,
                  move: v.mean_abs_dz_median,
                  placeboRatio: v.placebo_ratio_median,
                  reliabilityGain: v.reliability_gain_median,
                  clears: v.clears,
                  adopted: v.rung === opponentAdjustment.stop_rule.adopted,
                }))}
                threshold={opponentAdjustment.stop_rule.thresholds.mean_abs_dz}
              />
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                The dashed line is the{" "}
                {opponentAdjustment.stop_rule.thresholds.mean_abs_dz} cohort-sd
                movement a rung has to reach to be worth having. Movement alone
                settles nothing: the two-way lineup rung moves further than two
                of the three rungs that clear, and fails on repeatability. That
                is why each rung is judged on its own rather than climbed until
                one breaks.
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Rung</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Median move
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Placebo ratio
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Reliability vs raw
                    </th>
                    <th className="py-2 font-normal">Clears</th>
                  </tr>
                </thead>
                <tbody>
                  {opponentAdjustment.stop_rule.per_rung.map((v) => (
                    <tr key={v.rung} className="border-b border-hairline/60">
                      <td className="py-1.5 pr-4 font-mono text-xs">
                        {v.rung}
                        {v.rung === opponentAdjustment.stop_rule.adopted && (
                          <span className="ml-2 text-accent">adopted</span>
                        )}
                      </td>
                      <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                        {v.mean_abs_dz_median.toFixed(4)}
                      </td>
                      <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                        {v.placebo_ratio_median === null
                          ? "—"
                          : v.placebo_ratio_median.toFixed(2)}
                      </td>
                      <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                        {v.reliability_gain_median === null
                          ? "—"
                          : v.reliability_gain_median.toFixed(4)}
                      </td>
                      <td className="py-1.5 font-mono text-xs">
                        {v.clears ? "yes" : "no"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs leading-relaxed text-ink-muted">
              The ladder is not monotone and the rule does not assume it is: the
              two-way lineup rung moves the most and comes out less repeatable
              than the raw number, and the pooled rung above it repairs exactly
              that. A climb-until-failure rule would have stopped at the cheap
              rung and discarded the one that works.
            </p>
            <p>
              <strong className="text-ink">
                The cheap rung is blind where the disparity is largest.
              </strong>{" "}
              Residualizing on a team rating needs the team to have a rating.{" "}
              {(opponentAdjustment.coverage.blind_share * 100).toFixed(1)}% of{" "}
              {opponentAdjustment.coverage.lines.toLocaleString()} lines face an
              opponent still sitting on the Glicko-2 prior, and a further{" "}
              {opponentAdjustment.coverage.opponent_missing} face a team the
              rating never reached. That is concentrated in the CWL years, which
              is precisely where the competitive spread is widest.
            </p>
            <p>
              <strong className="text-ink">
                Published as a null, and not promoted.
              </strong>{" "}
              Per line, opposition is worth about half a cohort standard
              deviation. Averaged over a player&rsquo;s season it very nearly
              cancels — zero to three decimal places on both sides of the
              CWL/CDL seam — and the softest fields in the archive are CDL
              events rather than the CWL open brackets the question was about. So
              the correction stays out of the published per-player statistics:
              the site continues to show unadjusted numbers, and what ships is
              the ladder, its controls and its verdict as a versioned run.
            </p>
          </div>
        </section>
      )}

      {matchContext && (
        <section id="match-context" className="mt-12">
          <h2 className="font-display text-2xl font-semibold uppercase">
            Match context
          </h2>
          <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
            <p>
              <strong className="text-ink">The object</strong> is the same one
              the opponent ladder adjusts — {matchContext.adjusts}. Four columns
              describe the circumstances of a map and nothing read any of them:
              whether it was played on a LAN stage or online, what round it was,
              which map it was, and what the event paid. The design holds the
              player and the opposing lineup fixed, so every coefficient here is
              what survives that — and no further: {matchContext.claim}.
            </p>
            <p>
              <strong className="text-ink">The judge</strong> was declared before
              anything was fitted: one row per feature family, each judged on two
              numbers. How far it moves the leaderboard, in cohort standard
              deviations, and whether it lowers out-of-fold error on the rate it
              claims to explain, over five folds cut on whole series. A family
              has to lower that error on more than{" "}
              {(matchContext.ablation.keep_share * 100).toFixed(0)}% of the
              cohorts it was fitted on. Moving the table without predicting is
              what a family fitting its own noise looks like, and the pair of
              numbers is what separates the two.
            </p>
            <div className="border border-hairline bg-surface p-4">
              <MovementVsError
                families={matchContext.families
                  .map((family) => {
                    const stats = matchContext.ablation.by_family[family];
                    return {
                      family,
                      move: stats?.leaderboard_move?.median ?? 0,
                      errorDelta: stats?.oof_rmse_delta?.median ?? 0,
                      improved: stats?.cohorts_improved ?? 0,
                      measured: stats?.cohorts_measured ?? 0,
                      kept: (
                        matchContext.ablation.verdicts[family] ?? ""
                      ).startsWith("kept"),
                    };
                  })
                  .filter((f) => f.measured > 0)}
              />
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                The bar is how far the family moves the leaderboard; the mark to
                its right is the change in out-of-fold error, on its own scale,
                with a filled square where prediction improved and a hollow
                circle where it did not. Five of the six families have a bar and
                sit on the dashed line: they move the table and predict nothing.
                One sits well to the left of it.
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Family</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Median move
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Out-of-fold RMSE
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Cohorts improved
                    </th>
                    <th className="py-2 font-normal">Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {matchContext.families.map((family) => {
                    const stats = matchContext.ablation.by_family[family];
                    const verdict =
                      matchContext.ablation.verdicts[family] ?? "not fitted";
                    return (
                      <tr key={family} className="border-b border-hairline/60">
                        <td className="py-1.5 pr-4 font-mono text-xs">
                          {family}
                        </td>
                        <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                          {stats?.leaderboard_move?.median === undefined
                            ? "—"
                            : stats.leaderboard_move.median.toFixed(4)}
                        </td>
                        <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                          {stats?.oof_rmse_delta?.median === undefined
                            ? "—"
                            : stats.oof_rmse_delta.median.toFixed(4)}
                        </td>
                        <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                          {stats?.cohorts_measured
                            ? `${stats.cohorts_improved}/${stats.cohorts_measured}`
                            : "—"}
                        </td>
                        <td className="py-1.5 text-xs">{verdict}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p>
              <strong className="text-ink">
                The LAN effect, per player, is a null.
              </strong>{" "}
              Each player&rsquo;s LAN-minus-online deviation is pooled by
              empirical Bayes, and the interval is placed around the cohort&rsquo;s
              common effect rather than around zero — shrinkage pulls every
              player toward that common value, so asking whether a player differs
              from zero asks whether the cohort does, and answers yes for
              everyone at once. Of{" "}
              {matchContext.venue_effect.players.length.toLocaleString()}{" "}
              player-cohort-features over{" "}
              {matchContext.venue_effect.n_players} players,{" "}
              {matchContext.venue_effect.n_clearing_interval} clear their
              interval against that common effect. Chance alone would put about{" "}
              {Math.round(matchContext.venue_effect.players.length / 20)} outside
              it. The online warrior is not in this record — not that the effect
              is small, but that the players cannot be told apart in how they
              carry between the two venues.
            </p>
            <p>
              <strong className="text-ink">The known failure</strong> is that the
              venue term is half a stage term. Across{" "}
              {(matchContext.coverage.by_era.CDL?.lines ?? 0).toLocaleString()}{" "}
              Call of Duty League lines, LAN is where the Major bracket is played
              and online is where the qualifier is: regressing the venue flag on
              the stage classes returns an R² near one half. And the earlier era
              has no venue contrast at all —{" "}
              {(matchContext.coverage.by_era.CWL?.lan ?? 0).toLocaleString()} CWL
              lines, every one of them LAN, none online. So the venue question is
              answerable only inside the newer era, at roughly twice the variance
              the raw split suggests, and the era and the venue cannot be told
              apart across the seam between them at all.
            </p>
            <p>
              <strong className="text-ink">
                The home-market effect lands as noise.
              </strong>{" "}
              No source carries a team&rsquo;s home city — Liquipedia records a
              region for 457 of 458 teams and an event&rsquo;s organizer is
              always the publisher — so the map from venue to franchise is
              hand-curated and published with a reason and a confidence for every
              one of its entries, neutral sites included.{" "}
              {matchContext.host_effect.n_clearing_interval} of{" "}
              {matchContext.host_effect.per_cohort.length} cohort-features clear
              their interval, on a flag that is set for one team at one event, and
              the family does not survive the ablation. What large coefficients
              there are sit on a few dozen lines each.
            </p>
            <p>
              <strong className="text-ink">
                Map identity is the one family that earns its place.
              </strong>{" "}
              It is fitted as a random effect rather than as one dummy per map —
              the rotation changes every title and several maps carry a few
              hundred rows — and it lowers out-of-fold error on{" "}
              {matchContext.ablation.by_family.map_identity?.cohorts_improved ??
                0}{" "}
              of{" "}
              {matchContext.ablation.by_family.map_identity?.cohorts_measured ??
                0}{" "}
              cohorts. Hill time on one map is not hill time on another, and the
              cohort the rating standardizes in — season by mode — averages over
              the whole rotation.
            </p>
          </div>
        </section>
      )}

      {evaluationManifest && evaluationPrimary && (
        <section id="evaluation" className="mt-12">
          <h2 className="font-display text-2xl font-semibold uppercase">
            The evaluation harness
          </h2>
          <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
            <p>
              A rating that picks its own test after seeing the answer has not
              been tested. So the test is declared first, hashed, and the hash
              checked on every run: {evaluationManifest.primary.what}, resampled
              by {evaluationManifest.primary.resampling_unit} at{" "}
              {evaluationManifest.bootstrap_b.toLocaleString()} draws, with the
              detectable-difference floor computed from the panel rather than
              assumed. The pin{" "}
              {evaluationManifest.matches_pin
                ? "matches, so this is the test that was declared"
                : "does not match: the harness changed after it was pinned"}
              .
            </p>
            <div className="border border-hairline bg-surface p-4">
              <EstimateForest
                rows={Object.entries(evaluationPrimary.predictors).map(
                  ([name, p]) => ({
                    label: name,
                    value: p.r,
                    lo: p.lo,
                    hi: p.hi,
                    emphasis: name === "skill",
                    note: evaluationPrimary.gaps[name]
                      ? `${evaluationPrimary.gaps[name].delta_r >= 0 ? "+" : ""}${evaluationPrimary.gaps[name].delta_r.toFixed(3)}`
                      : "baseline",
                  }),
                )}
                reference={evaluationPrimary.baseline_r}
                referenceLabel="K/D z"
                unit="correlation with next season's K/D z"
              />
              <p className="mt-2 text-xs leading-relaxed text-ink-muted">
                The dashed line is the baseline every predictor was declared
                against. Nothing reaches it, and the right-hand column is each
                one&rsquo;s distance from it — the rating this site leads with is
                the row in the accent.
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full max-w-2xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Predictor</th>
                    <th className="py-2 pr-4 text-right font-normal">r</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      95% interval
                    </th>
                    <th className="py-2 text-right font-normal">
                      vs K/D z, against the floor
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(evaluationPrimary.predictors).map(
                    ([name, p]) => {
                      const gap = evaluationPrimary.gaps[name];
                      return (
                        <tr key={name} className="border-b border-hairline/60">
                          <td className="py-1.5 pr-4 font-mono text-xs">
                            {name}
                          </td>
                          <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                            {p.r.toFixed(4)}
                          </td>
                          <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                            {p.lo.toFixed(4)} to {p.hi.toFixed(4)}
                          </td>
                          <td className="py-1.5 text-right font-mono text-xs tabular-nums text-ink-secondary">
                            {gap
                              ? `${gap.delta_r >= 0 ? "+" : ""}${gap.delta_r.toFixed(4)} (floor ${gap.mde80.toFixed(4)})`
                              : "baseline"}
                          </td>
                        </tr>
                      );
                    },
                  )}
                </tbody>
              </table>
            </div>
            <p className="text-xs leading-relaxed text-ink-muted">
              {evaluationPrimary.n} transitions over{" "}
              {evaluationPrimary.resampling.clusters} resampling clusters, drawn
              from a panel of {evaluationPrimary.n_panel} —{" "}
              {evaluationPrimary.panel}. Nothing on this table beats K/D z, and
              the two ratings built to are further below it than the floor the
              test could have missed by. That is a result, not a missing result.
            </p>
            {openskill && (
              <p>
                <strong className="text-ink">The baseline.</strong>{" "}
                {openskill.what}. It is a {openskill.model} rating over{" "}
                {openskill.n_maps.toLocaleString()} four-on-four map results at
                library defaults, tuned against nothing, covering{" "}
                {openskill.n_player_seasons_published} player-seasons. It knows
                who was on the server and nothing else, and it exists so that a
                rating cannot claim a win over an absent opponent.
              </p>
            )}
            {evaluationPlacebo && (
              <p className="text-xs leading-relaxed text-ink-muted">
                <strong className="text-ink">The placebos.</strong>{" "}
                {evaluationPlacebo.n_run} run,{" "}
                {evaluationPlacebo.n_failed} failed —{" "}
                {Object.entries(evaluationPlacebo.placebos)
                  .map(([k, v]) => `${k} ${v.passes ? "passes" : "fails"}`)
                  .join(", ")}
                . A pass here means the machinery finds nothing where there is
                nothing to find, which is the only reason to believe it when it
                finds something.
              </p>
            )}
            {evaluationReproduction && (
              <p className="text-xs leading-relaxed text-ink-muted">
                <strong className="text-ink">Reproduction.</strong> The harness
                recomputes {evaluationReproduction.recomputed.length} published
                numbers from the runs that wrote them and{" "}
                {evaluationReproduction.against_the_page.length}{" "}
                numbers written
                into this page&rsquo;s prose, at a tolerance of{" "}
                {evaluationReproduction.tolerance}.{" "}
                {evaluationReproduction.n_mismatched +
                  evaluationReproduction.n_page_mismatched ===
                0
                  ? "All of them match."
                  : `${evaluationReproduction.n_mismatched} run numbers and ${evaluationReproduction.n_page_mismatched} page numbers do not match.`}
              </p>
            )}
            {evaluationPopulation && (
              <p className="text-xs leading-relaxed text-ink-muted">
                <strong className="text-ink">The population is frozen.</strong>{" "}
                {evaluationPopulation.n_maps.toLocaleString()} maps, cut{" "}
                {evaluationPopulation.cut}, hashed and stored so a later
                ingestion cannot quietly change what the test was scored on.{" "}
                {evaluationPopulation.matches
                  ? "The database still matches it."
                  : `The database has diverged: ${evaluationPopulation.n_added} added, ${evaluationPopulation.n_removed} removed.`}
              </p>
            )}
          </div>
        </section>
      )}

      {skillPrior && (
        <section id="skill" className="mt-12">
          <h2 className="font-display text-2xl font-semibold uppercase">
            SKILL
          </h2>
          <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
            <p>
              The composite rating is fitted against map outcome, so a column
              that <em>names</em> the result earns weight for naming it. SKILL
              asks the inverted question: the target is the season plus-minus at{" "}
              {skillPrior.target.scope} scope, the box score is the predictor,
              and a column earns weight only if the profile it belongs to
              preceded a player who moved the margin. The fit is walk-forward by
              season over {skillPrior.design.n_columns}{" "}
              columns; the prediction
              is blended with the season&rsquo;s own coefficient by inverse
              variance, and that posterior is SKILL.
            </p>
            <p>
              <strong className="text-ink">
                The most important number here is about the target, not the
                model.
              </strong>{" "}
              Empirical Bayes returns a between-player variance of{" "}
              {skillPrior.weights.tau2.toExponential(1)} against a mean
              observation variance of{" "}
              {skillPrior.target_signal.mean_obs_var.toFixed(4)}. Taken at face
              value with its own uncertainty, the season plus-minus does not
              establish that these players differ — so the blend has nothing to
              defend, and{" "}
              {(skillPrior.blend.mean_weight_prior * 100).toFixed(0)}% of the
              posterior&rsquo;s weight falls on the prior. {skillPrior.statement}
              .
            </p>
            <p>
              <strong className="text-ink">
                It lost the gate it was declared on.
              </strong>{" "}
              {evaluationPrimary?.gaps.skill && (
                <>
                  Against next season&rsquo;s K/D z, SKILL scores r ={" "}
                  {evaluationPrimary.predictors.skill.r.toFixed(4)} where raw K/D
                  z scores {evaluationPrimary.baseline_r.toFixed(4)}: a gap of{" "}
                  {evaluationPrimary.gaps.skill.delta_r.toFixed(4)} [
                  {evaluationPrimary.gaps.skill.lo.toFixed(4)},{" "}
                  {evaluationPrimary.gaps.skill.hi.toFixed(4)}] over{" "}
                  {evaluationPrimary.n} transitions against a floor of{" "}
                  {evaluationPrimary.gaps.skill.mde80.toFixed(4)}. The loss is
                  larger than the floor, so this is a measured failure rather
                  than an inconclusive test.{" "}
                </>
              )}
              K/D z remains the recommended forecaster, and that sentence appears
              next to the rating on{" "}
              <Link className="underline" href="/players">
                /players
              </Link>{" "}
              rather than only here.
            </p>
            {priorTarget && (
              <p>
                <strong className="text-ink">What it is good at.</strong>{" "}
                Against the quantity it was actually fitted for —{" "}
                {priorTarget.what} — SKILL reaches r ={" "}
                {priorTarget.predictors.skill?.toFixed(4)}{" "}
                against the composite
                rating&rsquo;s {priorTarget.predictors.composite?.toFixed(4)}{" "}
                and K/D z&rsquo;s {priorTarget.predictors.kd_z?.toFixed(4)}, over{" "}
                {priorTarget.n} transitions. No significance is claimed on that
                comparison. A rating can be the best available answer to its own
                question and still be the wrong thing to forecast a player with,
                and publishing only the half that flatters it is the omission
                this harness exists to prevent.
              </p>
            )}
            {skillPower && skillPower.available && (
              <p className="text-xs leading-relaxed text-ink-muted">
                <strong className="text-ink">
                  How much of this is the panel.
                </strong>{" "}
                {skillPower.statement} The panel is {skillPower.n_panel}{" "}
                transitions over {skillPower.clusters} clusters, at a design
                effect of {skillPower.design_effect.toFixed(2)}; the gap would
                have to close by {skillPower.distance_to_clear.toFixed(4)} before
                the test could call it.
              </p>
            )}
            {skillSeasons.length > 0 && (
              <p className="text-xs leading-relaxed text-ink-muted">
                <strong className="text-ink">Coverage.</strong> SKILL is
                published for{" "}
                {skillSeasons
                  .map((s) => `${s.year} (${s.players})`)
                  .join(", ")}{" "}
                and for no season before that. The absence is structural: the
                first season has nothing before it to train the prior on, and the
                CWL years carry no season-resolution coefficient to blend with.
              </p>
            )}
          </div>
        </section>
      )}

      <section id="winprob" className="mt-12">
        <h2 className="font-display text-2xl font-semibold uppercase">
          Series win probability (winprob_v1)
        </h2>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-ink-secondary">
          <p>
            Rather than a third rating system, this model tests whether anything{" "}
            <em>beyond</em> the ratings carries information about who wins a
            series. Its features, all computed strictly before each series, are
            the walk-forward Glicko-2 and Elo probabilities, combined rating
            deviation, each team’s last-{winprobArt?.formWindow ?? 10} win rate,
            and a shrunken head-to-head record, in an expanding-window logistic
            regression refit every {winprobArt?.refitEvery ?? 50} series. Until{" "}
            {winprobArt?.minTrain ?? 200} series of history exist it passes
            Glicko-2 through unchanged, so its backtest is directly comparable.
          </p>
          <p>
            That comparability is the whole design, and it was broken for a
            while: the rating systems moved to whole-roster rating periods while
            this model kept advancing a private copy of Glicko-2 one series at a
            time, so its pass-through phase passed through a Glicko-2 published
            nowhere on this site. The rating period, lineage map, K and τ now
            come from the same values the Elo and Glicko-2 runs use
            {winprobArt?.period && (
              <> (period: {winprobArt.period})</>
            )}
            , and a test pins the pass-through phase against the published
            Glicko-2 prediction by prediction.
          </p>
          <p>
            Measured against that baseline, the added features move the
            forecast barely at all
            {winprobWindow && <> over {winprobWindow}</>}
            {momentum && <>: Brier improves by {momentum.delta.toFixed(4)}</>}
            {accuracyGapPp !== null && (
              <>
                {" "}
                while accuracy{" "}
                {Math.abs(accuracyGapPp) < 0.05 ? (
                  <>is unchanged</>
                ) : (
                  <>
                    {accuracyGapPp >= 0 ? "gains" : "drops"}{" "}
                    {Math.abs(accuracyGapPp).toFixed(2)} points
                  </>
                )}
              </>
            )}
            . The Brier gain is small but not noise — its interval below
            excludes zero — so the reading is a marginal edge over the ratings
            rather than a second rating system: recent form and head-to-head
            history add little beyond team strength, and nothing at all to how
            often the favourite is called correctly.
            {momentum && (
              <>
                {" "}
                That gap carries an interval: the Brier
                improvement over Glicko-2 is {signed(momentum.delta)}, 95% CI{" "}
                {signed(momentum.lo)} to {signed(momentum.hi)} over{" "}
                {modelGaps?.nSeries.toLocaleString()} paired series
                {momentum.p !== null && (
                  <> (Diebold-Mariano p = {momentum.p.toFixed(2)})</>
                )}
                .
              </>
            )}
            {modelGaps?.formBeta !== null && modelGaps?.formSwingPp != null && (
              <>
                {" "}
                It also has a power statement, which is the more useful half. At
                this sample size only a form coefficient of{" "}
                {modelGaps.formBeta?.toFixed(2)} or larger would have been
                detectable — momentum worth{" "}
                {modelGaps.formSwingPp.toFixed(0)} points of win probability
                between a team on a 10-0 run and one on 0-10. The fit found{" "}
                {winprobArt?.finalWeights.form_diff?.toFixed(2).replace("-", "−") ??
                  "≈0"}
                . So this null is narrower than “form does not
                matter”: an effect that large is ruled out, a realistic one is
                not, and {modelGaps.nSeries.toLocaleString()} series is not
                enough to settle it.
              </>
            )}
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
                The two rating logits carry the prediction, and the ridge splits
                them unevenly because they are near-restatements of each other —
                read them together, not as a ranking of Elo against Glicko-2.
                The three non-rating rows are small and, in the case of form,
                signed the wrong way for a momentum story. Treat them as an
                absence of residual signal rather than as precise effect sizes:
                the interval published on this model is on its Brier gap, not on
                the coefficients themselves.
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
        {modelGaps && (
          <div className="mt-4 border border-hairline bg-surface p-4">
            <h3 className="eyebrow text-ink-secondary">
              Which of these gaps is real
            </h3>
            <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
              The cards below are ordered by Brier, which reads as a ranking, so
              here is what the ordering is worth. Every model predicts the same{" "}
              {modelGaps.nSeries.toLocaleString()} series, making the gaps paired
              data: the per-series difference in squared error is one
              observation, its mean is the gap, and a{" "}
              {modelGaps.bootstrapB.toLocaleString()}-draw bootstrap over series
              gives the interval. The Diebold-Mariano statistic is the same mean
              over its standard error. “Detectable” is the smallest gap this many
              series could resolve at 80% power — the answer to “was this
              underpowered?”, which is the number that matters most for a
              published null.
            </p>
            <div className="mt-4 overflow-x-auto">
              <table className="w-full max-w-3xl text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-4 font-normal">Contrast</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Brier gap
                    </th>
                    <th className="py-2 pr-4 text-right font-normal">95% CI</th>
                    <th className="py-2 pr-4 text-right font-normal">DM p</th>
                    <th className="py-2 pr-4 text-right font-normal">
                      Detectable
                    </th>
                    <th className="py-2 text-right font-normal">Verdict</th>
                  </tr>
                </thead>
                <tbody>
                  {modelGaps.pairs.map((p) => (
                    <tr key={`${p.a}-${p.b}`} className="border-b border-hairline/60">
                      <td className="py-2 pr-4">
                        {GAP_LABEL[p.a] ?? p.a} − {GAP_LABEL[p.b] ?? p.b}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono tabular-nums">
                        {signed(p.delta)}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                        {signed(p.lo)} to {signed(p.hi)}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono tabular-nums">
                        {p.dmP === null ? "—" : p.dmP.toFixed(3)}
                      </td>
                      <td className="py-2 pr-4 text-right font-mono tabular-nums">
                        {p.mde80.toFixed(4)}
                      </td>
                      <td className="py-2 text-right">
                        {p.excludesZero ? (
                          <span className="text-ink">separates</span>
                        ) : (
                          <span className="text-ink-muted">not resolved</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-ink-muted">
              A negative gap favours the first model. Signs are on Brier, where
              lower is better.
            </p>
            {mapElo && (
              <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
                This table covers the three series-level models. The{" "}
                <code className="font-mono text-xs">map_elo</code> card below is
                a map-level rating rolled up to the series, and its gaps against
                these three — including the one comparison on this page that
                clears both its interval and its power threshold — are in{" "}
                <Link className="underline" href="#map-elo">
                  Map Elo
                </Link>
                .
              </p>
            )}
          </div>
        )}
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
                <Calibration bins={c.calibration} unit="series" />
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
          record, in{" "}
          {feedKinds.length > 0 ? `${feedKinds.length} kinds` : "several kinds"},
          each with fixed eligibility thresholds (e.g. outliers require ≥ 30 maps
          and |z| ≥ 2). The numbers in each headline are read from the database,
          not written by hand.
          {insightsRun && (
            <span className="text-ink-muted">
              {" "}
              Current run: v{insightsRun.version}, code {insightsRun.codeRef ?? "n/a"}.
            </span>
          )}
        </p>
        {feedKinds.length > 0 && (
          <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-ink-muted">
            {feedKinds.map((k) => (
              <li key={k.kind} className="flex items-baseline gap-1.5">
                <span className="text-ink-secondary">{kindLabel(k.kind)}</span>
                <span className="font-mono tabular-nums">{k.n}</span>
              </li>
            ))}
            <li className="flex items-baseline gap-1.5">
              <span className="text-ink-secondary">Total</span>
              <span className="font-mono tabular-nums">
                {feedKinds.reduce((s, k) => s + k.n, 0)}
              </span>
            </li>
          </ul>
        )}
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
          Two limits. The per-kill distance field
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

      {roundWp && (
        <section id="round-win-probability" className="mt-12">
          <h2 className="font-display text-2xl font-semibold uppercase">
            Round win probability
          </h2>
          <div className="mt-3 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
            <p>
              The event tier says what happened; this is the first model built on
              top of it. Given the state of a Search and Destroy round right now,
              what is the probability each team wins it? SnD is the only mode whose
              rounds are a unit of play — one life each, a discrete winner,{" "}
              {roundWp.winProb.table.n_rounds.toLocaleString()} of them. A Hardpoint
              “round” is the whole map and a CTF one is a half, so there
              is no round-scale contest there to model, and BO4 has no feed at all.
            </p>
            <p>
              Counting outcomes for every (own alive, opponent alive) pair over both
              sides of every instant gives sixteen non-terminal states from{" "}
              {roundWp.winProb.table.n_states.toLocaleString()} observations. Because
              each instant is recorded from both teams’ points of view, the
              table is antisymmetric by construction and every <i>n</i>-versus-
              <i>n</i> state is exactly .500 — a property of the encoding, not a
              finding.
            </p>
          </div>

          <div className="mt-5 overflow-x-auto">
            <table className="w-full max-w-xl text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Survivors</th>
                  {[4, 3, 2, 1].map((opp) => (
                    <th key={opp} className="py-2 pr-4 text-right font-normal">
                      vs {opp}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[4, 3, 2, 1].map((own) => (
                  <tr key={own} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-4">{own} alive</td>
                    {[4, 3, 2, 1].map((opp) => {
                      const cell = roundWp.winProb.table.cells.find(
                        (c) => c.own === own && c.opp === opp,
                      );
                      return (
                        <td
                          key={opp}
                          className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary"
                        >
                          {cell ? cell.p.toFixed(3) : "—"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 max-w-2xl text-xs text-ink-muted">
              Standard errors run 0.003–0.011, widened by &radic;2 because each round
              enters its cells twice. Round win odds track the <i>ratio</i> of
              survivors, not the difference: logit&nbsp;P(win)&nbsp;&asymp;&nbsp;
              {roundWp.winProb.table.parametric.weights.log_ratio?.toFixed(2)}
              &nbsp;&times;&nbsp;[log(own)&nbsp;&minus;&nbsp;log(opp)]&nbsp;+&nbsp;
              {roundWp.winProb.table.parametric.weights.diff?.toFixed(2)}
              &nbsp;&times;&nbsp;[own&nbsp;&minus;&nbsp;opp]. Being up one is worth
              .716 at 4v3 and .846 at 2v1 — the same man advantage, nearly twice the
              swing, because it is a larger share of what is left.
            </p>
          </div>

          {roundBacktest && (
            <div className="mt-6 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
              <p>
                Walk-forward by event over{" "}
                {roundBacktest.n_rounds.toLocaleString()} rounds in the{" "}
                {roundBacktest.n_events_scored} events that have a predecessor. Losses
                accumulate per round and the bootstrap resamples rounds: the rows a
                round contributes are one round seen at successive instants from both
                sides, and treating them as independent draws would shrink every
                interval and manufacture significance. The counted table (Brier{" "}
                {roundBrier.get("state_table")?.toFixed(5)}) beats every smooth fit
                tried, including the log-ratio model above (
                {roundBrier.get("survivors")?.toFixed(5)}); against always guessing
                0.5 ({roundBrier.get("coin_flip")?.toFixed(5)}) the margin is not
                close. With sixteen states and this many observations there is
                nothing for a smooth function to buy.
              </p>
              <p>
                Two nulls come out of the same backtest, each asked against the model
                the feature was added to.{" "}
                {timeNull && (
                  <>
                    <strong>Time elapsed in the round adds nothing</strong> once
                    survivors are known: {timeNull.delta >= 0 ? "+" : ""}
                    {timeNull.delta.toFixed(5)} [{timeNull.lo >= 0 ? "+" : ""}
                    {timeNull.lo.toFixed(5)}, {timeNull.hi >= 0 ? "+" : ""}
                    {timeNull.hi.toFixed(5)}]
                    {timeNull.dm_p !== null && <>, p&nbsp;=&nbsp;{timeNull.dm_p.toFixed(2)}</>},
                    with the sign the wrong way; this archive could have detected{" "}
                    {timeNull.mde80.toFixed(5)}.{" "}
                  </>
                )}
                {bombNull && (
                  <>
                    <strong>Neither does the bomb.</strong> The feed carries no plant
                    or defuse event, but round durations pile up at 90 seconds and
                    then tail off — the regulation clock expiring — so a round still
                    alive at 90 seconds implies a plant. Adding that indicator moves
                    Brier by {bombNull.delta >= 0 ? "+" : ""}
                    {bombNull.delta.toFixed(6)} [{bombNull.lo >= 0 ? "+" : ""}
                    {bombNull.lo.toFixed(6)}, {bombNull.hi >= 0 ? "+" : ""}
                    {bombNull.hi.toFixed(6)}]
                    {bombNull.dm_p !== null && <>, p&nbsp;=&nbsp;{bombNull.dm_p.toFixed(2)}</>}
                    . The reason is structural: the feed never says which side
                    planted, and an indicator that cannot be attributed to a team is
                    symmetric under swapping the teams while the target is
                    antisymmetric, so it can only enter at zero. It is measured
                    anyway rather than argued away.
                  </>
                )}
              </p>
              <p>
                Recovering which side is attacking would be the single largest
                improvement available here, and the archive does not support it.
              </p>
            </div>
          )}

          {roundWp.timeline && roundWp.timeline.leader_drift.length === 2 && (
            <div className="mt-6 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
              <h3 className="font-display text-lg font-semibold uppercase text-ink">
                The round along its own clock
              </h3>
              <p>
                The table says what a state is worth and nothing about when states
                arrive, so a third artifact describes the round on a{" "}
                {roundWp.timeline.bin_ms / 1000}-second grid — survivors, win
                probability, the model against the outcome, and how much of the
                sample is still playing. It is description rather than a second
                model: the probabilities are the same table read <i>in sample</i>,
                which is why a model-free series sits beside them and why the
                out-of-sample claim stays with the walk-forward above. The figure
                is on the{" "}
                <Link href="/rounds" className="underline">
                  rounds page
                </Link>
                .
              </p>
              <p>
                The one result worth arguing with is a calibration drift. On the
                subset of rounds where the sides have different survivor counts,
                the table&rsquo;s price for being ahead can be set against how
                often the side ahead actually won — at two single instants,
                because a round appears in twenty bins and pooling them would
                treat one round as twenty observations.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full max-w-xl text-left text-sm">
                  <thead>
                    <tr className="border-b border-hairline text-xs text-ink-muted">
                      <th className="py-2 pr-4 font-normal">Instant</th>
                      <th className="py-2 pr-4 text-right font-normal">Rounds</th>
                      <th className="py-2 pr-4 text-right font-normal">Table says</th>
                      <th className="py-2 pr-4 text-right font-normal">Actually won</th>
                      <th className="py-2 text-right font-normal">Gap</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roundWp.timeline.leader_drift.map((d) => (
                      <tr key={d.t_s} className="border-b border-hairline/60">
                        <td className="py-1.5 pr-4 font-mono tabular-nums">{d.t_s} s</td>
                        <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                          {d.n.toLocaleString()}
                        </td>
                        <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                          {d.model.toFixed(3)}
                        </td>
                        <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                          {d.observed.toFixed(3)}
                        </td>
                        <td className="py-1.5 text-right font-mono tabular-nums">
                          {d.gap >= 0 ? "+" : ""}
                          {(d.gap * 100).toFixed(1)} pt ±{(d.se * 100).toFixed(1)}
                          {!d.excludes_zero && (
                            <span className="text-ink-muted"> (spans zero)</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>
                A body is worth more late than early, and the table — which has no
                clock in it — prices both the same. That does not overturn the
                &ldquo;time adds nothing&rdquo; null above: the null is about
                prediction, and a small one-directional calibration drift is
                compatible with no measurable Brier gain. Both are true, which is
                the ordinary situation of an effect that is real and small.
              </p>
              <p>
                The same artifact measures what the trade window costs. A death is
                traded when the killer is answered by the victim&rsquo;s side within{" "}
                {roundWp.timeline.trade_latency.window_ms / 1000} seconds; without
                the cutoff,{" "}
                {(
                  (roundWp.timeline.trade_latency.n_answered /
                    roundWp.timeline.trade_latency.n_deaths) *
                  100
                ).toFixed(0)}
                % of deaths are ever answered, the median answer takes{" "}
                {((roundWp.timeline.trade_latency.median_ms ?? 0) / 1000).toFixed(1)}{" "}
                seconds, and only{" "}
                {((roundWp.timeline.trade_latency.within_of_answered ?? 0) * 100).toFixed(
                  0,
                )}
                % of the answers that come land inside it. The distribution peaks
                in its first two seconds, so the convention is not arbitrary — but
                it is a cut across a smooth decay rather than a seam in the data.
              </p>
              <p>
                Three exclusions:{" "}
                {roundWp.timeline.n_rounds_span_conflict.toLocaleString()} rounds
                record a length that ends before their own last death — by 38
                seconds at the median, so not rounding — and are dropped from the
                three state panels, which need to know when a round stopped, while
                the trade latency keeps them; the time axis stops where a hundredth
                of rounds remain; and the calibration series starts at{" "}
                {roundWp.timeline.bin_ms / 1000} seconds, because exactly two rounds
                in the archive open uneven.
              </p>
            </div>
          )}

          {roundRel && wpaResid && (
            <div className="mt-6 max-w-3xl space-y-3 text-sm leading-relaxed text-ink-secondary">
              <h3 className="font-display text-lg font-semibold uppercase text-ink">
                Win probability added, and why it is not a rating
              </h3>
              <p>
                Each kill moves the round from one state to the next, and the killer
                is credited with the change in their own side&rsquo;s win
                probability. The credits telescope: summed in one team&rsquo;s frame
                across a round they land exactly on that team&rsquo;s final win
                probability minus its opening .500. So WPA is an accounting of the
                round rather than a score attached to it.
              </p>
              <p>
                As a description of what happened it works. As a measurement of a{" "}
                <i>player</i> it does not. Per player per round, WPA correlates{" "}
                <strong>{roundRel.corr_wpa_kills.toFixed(3)}</strong> with kills per
                round — most of it is a kill count in a different unit. Splitting each
                player&rsquo;s games in two (by game, never by rounds within a map,
                which would let shared opponent and map context masquerade as a stable
                trait) and asking each half to predict the other, over the{" "}
                {roundRel.n_players} players with at least {roundRel.min_rounds}{" "}
                rounds on both sides:
              </p>
              <div className="overflow-x-auto">
                <table className="w-full max-w-xl text-left text-sm">
                  <thead>
                    <tr className="border-b border-hairline text-xs text-ink-muted">
                      <th className="py-2 pr-4 font-normal">Rate</th>
                      <th className="py-2 pr-4 text-right font-normal">
                        Split-half r
                      </th>
                      <th className="py-2 text-right font-normal">95% interval</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roundRel.cells.map((c) => (
                      <tr key={c.key} className="border-b border-hairline/60">
                        <td className="py-1.5 pr-4">{c.what}</td>
                        <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                          {c.r >= 0 ? "+" : ""}
                          {c.r.toFixed(2)}
                        </td>
                        <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                          [{c.lo >= 0 ? "+" : ""}
                          {c.lo.toFixed(2)}, {c.hi >= 0 ? "+" : ""}
                          {c.hi.toFixed(2)}]
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>
                Kill rate repeats. WPA repeats, slightly less well. The part of WPA
                that kill rate does not explain does not repeat at all — the point
                estimate is negative and the interval spans zero.{" "}
                {roundRel.n_players} players could have detected a correlation of{" "}
                {roundRel.r_detectable.toFixed(2)}, so a large effect is ruled out and
                a small one is not; nothing here supports the claim that <i>which</i>{" "}
                kills a player gets is a repeatable skill this archive can measure.
              </p>
              <p>
                So round WPA is published as a per-round description and is
                deliberately <strong>not</strong> promoted into the player rating.
                That was the hope this model was built on — player value measured in
                outcomes rather than box-score totals — and the reliability test says
                it is not there. Reporting it is the point of running the test.
              </p>
            </div>
          )}
        </section>
      )}

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
        {metricCatalog?.team_metrics && metricCatalog.team_metrics.length > 0 && (
          <div className="mt-8">
            <h3 className="eyebrow text-[10px] text-ink-secondary">
              Team metrics
            </h3>
            <p className="mt-2 text-sm text-ink-secondary">
              Scored the same way, with teams as the cohort. Series-shaped
              metrics (series and deciding-map win rates) exist only for the
              all-modes cohort — a series is one result spanning several modes,
              so slicing it by mode would count it once per mode it touched.
            </p>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-hairline text-xs text-ink-muted">
                    <th className="py-2 pr-3 font-normal">Metric</th>
                    <th className="py-2 pr-3 font-normal">Definition</th>
                    <th className="py-2 pr-3 font-normal">Modes</th>
                    <th className="py-2 font-normal">Qualifies at</th>
                  </tr>
                </thead>
                <tbody>
                  {metricCatalog.team_metrics.map((m) => (
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
                        {m.modes.map((s) => (s === "__all__" ? "all" : s)).join(", ")}
                      </td>
                      <td className="py-2 font-mono text-xs text-ink-secondary">
                        {m.min_denom} {m.denom_kind}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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
            This site draws on three sources, and which one a number came from
            changes what can be said about it.
          </p>
          <p>
            Box scores for 2017&ndash;2019 are Activision Publishing’s official
            CWL data release (the{" "}
            <code className="font-mono text-xs">cwl-data</code> repository,
            BSD-3-Clause). The 2017–2018 structured event feeds behind the
            kill-feed tier come from the same repository under the same licence.
            Handles are normalized across seasons and sources with an alias map
            maintained in the repository; corrections are welcome.
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
          <p>
            CDL-era box scores, 2020&ndash;2026, come from{" "}
            <a className="underline hover:text-ink" href="https://citoapi.com">
              Cito
            </a>
            , which carries Breaking Point match data, used with attribution
            under its terms. That licence is not CC-BY-SA and the data is not
            redistributable, so what is published from it here is{" "}
            <strong className="text-ink">derived analysis only</strong> —
            ratings, era-adjusted metrics, model outputs. The raw box scores are
            not exposed through this site&rsquo;s API and the stored responses
            are not in the public repository.
          </p>
          <p>
            Tournaments, placements, prize money, rosters, transfers and player
            bios come from{" "}
            <a
              className="underline hover:text-ink"
              href="https://liquipedia.net/callofduty"
            >
              Liquipedia
            </a>{" "}
            (CC-BY-SA 3.0) through the LPDB API — within the published rate
            limits, with an identifying User-Agent, and never by scraping HTML.
            Liquipedia also supplies map-level results where the CDL-era source
            garbled them: four 2020 series whose two sides were filed under one
            franchise slot, and 92 map results across 23 series whose per-map
            record contradicted the series score.
          </p>
          <p>
            Every row carries the source it came from, so these rules are
            enforced by a column rather than by inference from the season year.
            Where two sources cover the same match the scores are taken from the
            box-score source and the structure from Liquipedia, and both
            identifiers are kept on the row.
          </p>
          <p>
            The two box-score archives are not the same measurement, and the
            seam is visible in what each can answer. The CWL archive carries a
            kill feed, per-shot accuracy, streak and multi-kill counts, and map
            duration. The CDL-era source carries damage, contested hill time,
            non-traded kills and source-counted clutches, but{" "}
            <strong className="text-ink">no map clock</strong> — so every
            per-10-minute metric stops at 2019 and its per-map counterpart takes
            over from 2020. A cohort is never given both forms of one quantity,
            and nothing is averaged across the seam.
          </p>
        </div>
      </section>
      </div>
    </main>
  );
}
