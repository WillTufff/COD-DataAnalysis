import type { Metadata } from "next";
import Link from "next/link";
import { CarryParams } from "@/components/table/CarryParams";
import type { SortState } from "@/components/table/tableState";
import { PlayersIndexTable } from "./PlayersIndexTable";
import { IntervalBar, halfWidth, overlaps } from "@/components/charts/RatingInterval";
import {
  type PlayerIndexSort,
  boardDisagreements,
  formatLeagueSpans,
  getCareerRankLeaderboard,
  getEvaluationPrimary,
  getLeagueSpans,
  getCareerRankArtifact,
  getPlusMinusCareerBoard,
  getRatingComparison,
  getRatingLeaderboard,
  getSkillLeaderboard,
  getSkillPrior,
  getSkillSeasons,
  latestCareerRankRun,
  latestCareerRun,
  latestEvaluationRun,
  latestRatingRun,
  latestRun,
  latestSkillRun,
  queryPlayerIndex,
} from "@/lib/analytics";
import { RATINGS } from "@/lib/primacy";
import { playerSlug } from "@/lib/slug";
import {
  type SearchParams,
  one,
  parsePage,
  parsePer,
} from "@/lib/paging";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Players" };

// Default direction per column: names read A–Z, everything else best-first.
const SORTS: Record<PlayerIndexSort, "asc" | "desc"> = {
  handle: "asc",
  maps: "desc",
  seasons: "desc",
  teams: "desc",
  rating: "desc",
  last_year: "desc",
};

const DEFAULT_SORT: PlayerIndexSort = "rating";

function isSort(v: string): v is PlayerIndexSort {
  return Object.prototype.hasOwnProperty.call(SORTS, v);
}

// The table holds every matching player and pages itself in the browser; this
// is just a ceiling well above the few thousand rows the archive can produce.
const FETCH_ALL = 100_000;

// The order the five blend components are read in, which is the order the
// pre-registration fixes them in and not alphabetical.
const CAREER_COMPONENTS = [
  "PEAK",
  "PRIME",
  "LONGEVITY",
  "RESUME",
  "ACCOLADE",
] as const;

function componentBreakdown(components: Record<string, number>): string {
  const parts = CAREER_COMPONENTS.filter((name) => name in components).map(
    (name) => `${name.toLowerCase()} ${components[name].toFixed(0)}`,
  );
  const missing = CAREER_COMPONENTS.filter((name) => !(name in components));
  const absent =
    missing.length === 0
      ? ""
      : ` · no ${missing.map((name) => name.toLowerCase()).join(" or ")} the archive can see, so the rest carry the weight`;
  return `${parts.join(" · ")}${absent}`;
}

export default async function PlayersPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp: SearchParams = await searchParams;
  const sortRaw = one(sp, "sort");
  const sort = isSort(sortRaw) ? sortRaw : DEFAULT_SORT;
  const dirRaw = one(sp, "dir");
  const dir = dirRaw === "asc" || dirRaw === "desc" ? dirRaw : SORTS[sort];
  const q = one(sp, "q").slice(0, 40) || undefined;

  const [eraRun, ratingRun] = await Promise.all([
    latestRun("era_adjust"),
    latestRatingRun(),
  ]);

  if (!eraRun || !ratingRun) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-12">
        <h1 className="font-display text-5xl font-bold uppercase tracking-tight">
          Players
        </h1>
        <p className="mt-4 text-sm text-ink-secondary">
          No player runs have been published yet.
        </p>
      </main>
    );
  }

  const rows = await queryPlayerIndex(
    eraRun.id,
    ratingRun.id,
    { q, sort, dir },
    { offset: 0, limit: FETCH_ALL },
  );
  const total = rows.length;
  const initialSort: SortState = { id: sort, dir };

  // The composite rating board, ranking whole seasons rather than careers.
  const [ratingBoard, comparison, leagueSpans, careerRankRun] =
    await Promise.all([
      getRatingLeaderboard(ratingRun.id, eraRun.id),
      getRatingComparison(ratingRun.id),
      getLeagueSpans(),
      latestCareerRankRun(),
    ]);
  const careerRankBoard = careerRankRun
    ? await getCareerRankLeaderboard(careerRankRun.id, 25)
    : [];

  // The second career board, on the map-outcome axis. Both runs are needed:
  // the totals come from career_value and the composite rank each row is shown
  // against comes from the career_rank run above.
  const careerRun = await latestCareerRun();
  const plusMinusBoard =
    careerRun && careerRankRun
      ? await getPlusMinusCareerBoard(careerRun.id, careerRankRun.id)
      : [];
  // The comparison that justifies publishing a second board, read from the run
  // that computes it. A run older than the block returns nothing and the page
  // drops the sentence instead of asserting a number.
  const careerRankArtifact = careerRankRun
    ? await getCareerRankArtifact(careerRankRun.id)
    : null;
  const association =
    careerRankArtifact?.teammate_association?.by_board?.[
      "plus_minus.deviation.cdl"
    ] ?? null;
  const disagreements = boardDisagreements(plusMinusBoard);
  const resolvedDisagreements = disagreements.filter((d) => d.resolved);
  const plusMinusSeparated = plusMinusBoard.filter((r) => r.separated).length;
  // One domain across the shown rows, and it includes zero even though no row
  // on the top of the board is near it: on this axis zero is replacement level,
  // so a bar that does not show where zero is hides the only comparison the
  // interval is for.
  const PLUS_MINUS_PAD = 0.05;
  const plusMinusShown = plusMinusBoard.slice(0, 25);
  const plusMinusDomain = {
    lo:
      Math.min(
        0,
        ...plusMinusShown.map((r) => r.total - 1.96 * (r.totalSd ?? 0)),
      ) - PLUS_MINUS_PAD,
    hi:
      Math.max(
        0,
        ...plusMinusShown.map((r) => r.total + 1.96 * (r.totalSd ?? 0)),
      ) + PLUS_MINUS_PAD,
  };
  const brierGain = comparison
    ? 1 -
      comparison.overall[comparison.published].brier /
        comparison.overall[comparison.baseline].brier
    : null;

  // The board's intervals share one domain, so overlap is legible down the
  // column. How much of the top the leader is not actually separated from is
  // the honest headline of a table sorted to the third decimal.
  // The domain starts at the 1.00 league average even though no row is near it,
  // so the tick on each track means something and the whole board is visibly
  // above the line rather than filling the width by construction.
  // Padded at both ends so the 1.00 tick and the widest band sit inside the
  // track rather than on its edge, where either would be unreadable.
  const RATING_PAD = 0.02;
  const ratingDomain = {
    lo:
      Math.min(
        1,
        ...ratingBoard.map((r) => r.rating - (halfWidth(r.ratingSd) ?? 0)),
      ) - RATING_PAD,
    hi:
      Math.max(
        ...ratingBoard.map((r) => r.rating + (halfWidth(r.ratingSd) ?? 0)),
      ) + RATING_PAD,
  };
  const tiedWithLeader = ratingBoard
    .slice(1)
    .filter((r) => overlaps(r, ratingBoard[0])).length;

  // SKILL leads this page where it exists, which is 2021 onward. The seasons it
  // covers are read from its own run, so the board is empty rather than wrong
  // if a run ever stops writing them.
  const [skillRun, evaluationRun] = await Promise.all([
    latestSkillRun(),
    latestEvaluationRun(),
  ]);
  const skillSeasons = skillRun ? await getSkillSeasons(skillRun.id) : [];
  const latestSkillSeason = skillSeasons[skillSeasons.length - 1] ?? null;
  const [skillBoard, skillPrior, evaluation] = await Promise.all([
    skillRun && latestSkillSeason
      ? getSkillLeaderboard(skillRun.id, latestSkillSeason.seasonId)
      : Promise.resolve([]),
    skillRun ? getSkillPrior(skillRun.id) : Promise.resolve(null),
    evaluationRun ? getEvaluationPrimary(evaluationRun.id) : Promise.resolve(null),
  ]);
  const skillGap = evaluation?.gaps.skill ?? null;
  // One domain for every row, so the overlap down the column is the reading:
  // a board whose top rows all reach each other is an ordering of estimates.
  const SKILL_PAD = 0.01;
  const skillDomain = {
    lo:
      Math.min(0, ...skillBoard.map((r) => r.skill - 1.96 * r.skillSd)) -
      SKILL_PAD,
    hi:
      Math.max(...skillBoard.map((r) => r.skill + 1.96 * r.skillSd), 0) +
      SKILL_PAD,
  };
  const skillTiedWithLeader = skillBoard
    .slice(1)
    .filter(
      (r) =>
        Math.abs(r.skill - skillBoard[0].skill) <=
        1.96 * (r.skillSd + skillBoard[0].skillSd),
    ).length;

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        {total.toLocaleString()} players · {formatLeagueSpans(leagueSpans)}
        {eraRun.dataThrough && <> · data through {eraRun.dataThrough}</>}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Players
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        Every player with a rated map in the archive. Career totals combine all
        modes; the rating column is the player&rsquo;s best qualified season.
      </p>

      <form
        method="GET"
        className="mt-8 flex flex-wrap items-end gap-x-5 gap-y-3 border-y border-hairline py-4 text-sm"
      >
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink-muted">Player</span>
          <input
            type="search"
            name="q"
            placeholder="handle…"
            defaultValue={q ?? ""}
            className="w-44 border border-hairline bg-surface px-2 py-1.5"
          />
        </label>
        {/* The sort and row count live on the URL, written by the table as the
            reader changes them; carry the live values so filtering does not
            silently reset them. */}
        <CarryParams names={["sort", "dir", "per"]} />
        <button
          type="submit"
          className="border border-accent-dim bg-surface-raised px-4 py-1.5 font-display text-sm font-semibold uppercase tracking-wide text-ink hover:border-accent"
        >
          Filter
        </button>
        {q && (
          <Link
            href="/players"
            className="pb-2 font-mono text-xs text-ink-muted hover:text-ink"
          >
            clear
          </Link>
        )}
      </form>

      {rows.length === 0 ? (
        <p className="mt-8 text-sm text-ink-secondary">
          No player matches {q ? `“${q}”` : "this filter"}.
        </p>
      ) : (
        <PlayersIndexTable
          rows={rows}
          initialPer={parsePer(sp)}
          initialPage={parsePage(sp)}
          initialSort={initialSort}
        />
      )}

      <p className="mt-3 max-w-3xl text-xs text-ink-muted">
        The team column shows the most recent roster stint. Best rating is the
        player&rsquo;s highest all-modes season rating at 30 maps or more, with
        the season it came from; players who never reached 30 maps in a season
        show &ldquo;—&rdquo;. Per-season and per-metric leaderboards are on the{" "}
        <Link href="/stats" className="underline">
          stat explorer
        </Link>
        .
      </p>

      {skillBoard.length > 0 && latestSkillSeason && (
        <section
          data-surface="skill-board"
          className="mt-16 border-t border-hairline pt-8"
        >
          <h2 className="lower-third">
            How good now
            <span className="lt-note">
              SKILL, {latestSkillSeason.year} · {skillBoard.length} of{" "}
              {latestSkillSeason.players} rated players
            </span>
          </h2>
          <p className="mt-3 max-w-3xl text-sm text-ink-secondary">
            {RATINGS.skill.question} SKILL is the answer this site leads with:{" "}
            {RATINGS.skill.judge}. It is the only one of the three ratings that
            is meant to point forward, and it is the one with the worst result
            against its own test — read the paragraph under the table before
            reading the order.
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full max-w-3xl text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-normal">#</th>
                  <th className="py-2 pr-4 font-normal">Player</th>
                  <th className="py-2 pr-4 text-right font-normal">
                    SKILL ± sd
                  </th>
                  <th className="py-2 pr-4 font-normal">95% interval</th>
                  <th className="py-2 text-right font-normal">From the prior</th>
                </tr>
              </thead>
              <tbody>
                {skillBoard.map((r, i) => (
                  <tr key={r.playerId} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                      {i + 1}
                    </td>
                    <td className="py-1.5 pr-4 font-medium">
                      <Link
                        href={`/players/${playerSlug(r.handle)}`}
                        className="hover:text-accent hover:underline"
                      >
                        {r.handle}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {r.skill >= 0 ? "+" : ""}
                      {r.skill.toFixed(3)}
                      <span className="text-ink-muted">
                        {" "}
                        ±{r.skillSd.toFixed(3)}
                      </span>
                    </td>
                    <td className="py-1.5 pr-4">
                      <IntervalBar
                        value={r.skill}
                        sd={r.skillSd}
                        lo={skillDomain.lo}
                        hi={skillDomain.hi}
                        mark={0}
                        label={`${r.handle}: SKILL ${r.skill.toFixed(3)}, 95% ${(
                          r.skill -
                          1.96 * r.skillSd
                        ).toFixed(3)} to ${(r.skill + 1.96 * r.skillSd).toFixed(3)}`}
                      />
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                      {(r.weightPrior * 100).toFixed(0)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 max-w-3xl text-xs leading-relaxed text-ink-muted">
            {skillPrior && (
              <>
                The last column is the share of the rating the box-score prior
                supplied, and it averages{" "}
                {(skillPrior.blend.mean_weight_prior * 100).toFixed(0)}% across
                the {skillPrior.blend.seasons.length}{" "}
                rated seasons. That is the
                finding, not a footnote: the plus-minus this rating was supposed
                to be anchored by establishes no measurable spread between
                players, so the posterior is substantially the box score&rsquo;s
                opinion with a small correction.{" "}
              </>
            )}
            {skillGap && evaluation && (
              <>
                Against next season&rsquo;s K/D z — the test SKILL was declared
                on before it was fitted — SKILL scores r ={" "}
                {evaluation.predictors.skill.r.toFixed(4)} where raw K/D z
                scores {evaluation.baseline_r.toFixed(4)}, a gap of{" "}
                {skillGap.delta_r.toFixed(4)} [{skillGap.lo.toFixed(4)},{" "}
                {skillGap.hi.toFixed(4)}] over {evaluation.n} transitions
                against a floor of {skillGap.mde80.toFixed(4)}. It lost, and by
                more than the test could have missed. K/D z remains the
                recommended forecaster.{" "}
              </>
            )}
            {skillBoard.length > 1 && (
              <>
                The interval is ±1.96 sd on a scale every row shares:{" "}
                {skillTiedWithLeader} of the other {skillBoard.length - 1}{" "}
                players on this board have one that reaches the top row&rsquo;s,
                so the order is an ordering of estimates rather than a claim
                that those players differ.{" "}
              </>
            )}
            SKILL covers {skillSeasons[0]?.year}–
            {skillSeasons[skillSeasons.length - 1]?.year} only; earlier seasons
            have no season before them to train the prior on, so those years
            lead with the season rating below.{" "}
            <Link href="/methodology/skill" className="underline">
              methodology
            </Link>
            .
          </p>
        </section>
      )}

      {ratingBoard.length > 0 && (
        <section
          data-surface="value-board"
          className="mt-16 border-t border-hairline pt-8"
        >
          <h2 className="lower-third">
            What a season was worth
            <span className="lt-note">VALUE, top seasons, 30 maps minimum</span>
          </h2>
          <p className="mt-3 max-w-3xl text-sm text-ink-secondary">
            {RATINGS.value.question} A different question from the board above,
            and the one this rating is good at: it describes a season that was
            played rather than forecasting the next one.
          </p>
          <div className="mt-4 overflow-x-auto">
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
                  <th className="py-2 pr-4 font-normal">95% interval</th>
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
                    <td className="py-1.5 pr-4">
                      <IntervalBar
                        value={r.rating}
                        sd={r.ratingSd}
                        lo={ratingDomain.lo}
                        hi={ratingDomain.hi}
                        mark={1}
                        label={
                          r.ratingSd === null
                            ? `${r.handle} ${r.year}: rating ${r.rating.toFixed(2)}, no stored interval`
                            : `${r.handle} ${r.year}: rating ${r.rating.toFixed(2)}, 95% ${(
                                r.rating - 1.96 * r.ratingSd
                              ).toFixed(2)} to ${(r.rating + 1.96 * r.ratingSd).toFixed(2)}`
                        }
                      />
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                      {r.kdRaw !== null ? r.kdRaw.toFixed(2) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 max-w-3xl text-xs text-ink-muted">
            The rating weights each stat by its regression coefficient for
            winning maps in that title and mode, then reads that score through a
            two-level model of the cohort, which pulls short seasons toward the
            league mean and puts an average qualified season at 1.00. Which
            stats it reads is measured per cohort, and covers first bloods,
            survival, time per life and — where a kill feed exists — trades. The
            ±sd is the posterior&rsquo;s: what is still unknown about the player
            after pooling, not how far the number would move on other maps. The
            interval is ±1.96 sd on a scale shared by every row, which is the
            point of drawing it: {tiedWithLeader} of the other{" "}
            {ratingBoard.length - 1} seasons on this board have an interval that
            reaches the top one’s, so those ranks are an ordering of the
            estimates rather than a claim that the seasons differ.
            {brierGain !== null && comparison && (
              <>
                {" "}
                Against the box-score-only version, over the{" "}
                {comparison.common_maps.toLocaleString()} maps both predict, it
                improves Brier score by {(brierGain * 100).toFixed(0)}%; the
                per-cohort comparison, including where it loses, is on{" "}
              </>
            )}
            {brierGain === null && <> The full spec is on </>}
            <Link href="/methodology/player-rating" className="underline">
              methodology
            </Link>
            .
          </p>
        </section>
      )}

      {careerRankBoard.length > 0 && (
        <section
          data-surface="career-rank-board"
          className="mt-16 border-t border-hairline pt-8"
        >
          <h2 className="lower-third">
            All-time
            <span className="lt-note">
              career rank, five components blended across a career
            </span>
          </h2>
          <p className="mt-3 max-w-3xl text-sm text-ink-secondary">
            A different question again. This one reads every gold-tier stat
            a player&rsquo;s page shows across a career of at least three
            qualified seasons, and blends it with what that career finished
            and won.
          </p>
          <p className="mt-2 max-w-3xl text-sm text-ink-muted">
            This board covers 2013 onward. A season is scored against the
            players who played it, then pulled toward that season&rsquo;s
            average by how many maps it is, so a 40-map season cannot post a
            number a 124-map season could not. The early seasons carry a
            narrower set of stats, which is the record and not the player.
            Scored counts the seasons the box-score archive reaches; where a
            career has more, the column says so, and the seasons it does not
            reach score nothing rather than zero. The rating blends three
            things at fixed weights: how the box score reads at a career&rsquo;s
            best season, its best three-season stretch and summed across every
            season above replacement (65 of the total, split 20/25/20 below);
            what the teams finished (25); and what the awards said (10). A
            career the archive cannot see one of those for is scored on the
            rest. Season sum is the older number,
            the scored seasons added up, kept beside the rating because a long
            career and a short better one separate on it.
            See{" "}
            <Link className="underline hover:text-ink-secondary" href="/methodology">
              methodology
            </Link>
            .
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-normal">#</th>
                  <th className="py-2 pr-4 font-normal">Player</th>
                  <th className="py-2 pr-4 text-right font-normal">Scored</th>
                  <th className="py-2 pr-4 text-right font-normal">
                    Career score
                  </th>
                  <th className="py-2 pr-4 text-right font-normal">
                    Season sum ± sd
                  </th>
                  <th className="py-2 pr-4 font-normal">Peak</th>
                  <th className="py-2 pr-4 font-normal">Best three</th>
                  <th
                    className="py-2 font-normal"
                    title="How much a player out-performed the teammates they played alongside, averaged over their career. Not part of the ranking: it carries no weight and does not enter career score."
                  >
                    vs. teammates
                  </th>
                </tr>
              </thead>
              <tbody>
                {careerRankBoard.map((r, i) => (
                  <tr key={r.playerId} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                      {i + 1}
                    </td>
                    <td className="py-1.5 pr-4 font-medium">
                      <Link
                        href={`/players/${playerSlug(r.handle)}`}
                        className="hover:text-accent hover:underline"
                      >
                        {r.handle}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {r.seasonsCovered}
                      {r.seasonsCovered < r.nSeasons && (
                        <span
                          className="text-ink-muted"
                          title={`${r.nSeasons - r.seasonsCovered} further season${
                            r.nSeasons - r.seasonsCovered === 1 ? "" : "s"
                          } carry a finish and no box score`}
                        >
                          {" of "}
                          {r.nSeasons}
                        </span>
                      )}
                    </td>
                    <td
                      className="py-1.5 pr-4 text-right font-mono tabular-nums"
                      title={componentBreakdown(r.careerComponents)}
                    >
                      {r.total.toFixed(1)}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {r.seasonTotal === null ? "—" : r.seasonTotal.toFixed(1)}
                      {r.seasonTotal !== null && r.totalSd !== null && (
                        <span className="text-ink-muted"> ±{r.totalSd.toFixed(1)}</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-4 text-ink-secondary">
                      {r.peak.toFixed(1)}
                      {r.peakSeasonYear !== null && ` (${r.peakSeasonYear})`}
                    </td>
                    <td className="py-1.5 pr-4 text-ink-secondary">
                      {r.bestThree === null ? (
                        "—"
                      ) : (
                        <>
                          {r.bestThree.toFixed(1)}
                          {r.bestThreeStartYear !== null &&
                            ` from ${r.bestThreeStartYear}`}
                        </>
                      )}
                    </td>
                    <td className="py-1.5 text-ink-secondary">
                      {r.netOfTeammatesMean === null
                        ? "—"
                        : r.netOfTeammatesMean.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 max-w-3xl text-xs text-ink-muted">
            &ldquo;vs. teammates&rdquo; is how much a player out-performed the
            teammates they played alongside, averaged over their career. It is
            a separate display figure and is never part of the ranking: it
            carries no weight and does not enter the career score above.
          </p>
          <p className="mt-3 max-w-3xl text-xs text-ink-muted">
            The season score blends every gold-tier stat on a player&rsquo;s
            page, weighted by each mode&rsquo;s share of that season&rsquo;s
            maps, and carries no award credit. Awards reach the rating through
            their own component and nowhere else. Its ±sd
            reflects how much that basket
            disagreed with itself that season, not a measurement error on any
            one stat. A CWL year counts at full weight, same as a CDL season.
            The full spec is on{" "}
            <Link href="/methodology/career-rank" className="underline">
              methodology
            </Link>
            .
          </p>
        </section>
      )}

      {plusMinusBoard.length > 0 && (
        <section
          data-surface="plus-minus-career-board"
          className="mt-16 border-t border-hairline pt-8"
        >
          <h2 className="lower-third">
            All-time, in map results
            <span className="lt-note">
              career plus-minus, CDL era · {plusMinusBoard.length} careers
            </span>
          </h2>
          <p className="mt-3 max-w-3xl text-sm text-ink-secondary">
            The same question as the board above, asked of a different record.
            That one reads what a player did on the scoreboard. This one ignores
            the scoreboard entirely and reads the map result: one row per map,
            the players on one side against the players on the other, which
            estimates what a player&rsquo;s presence was worth in score margin
            while holding the other seven constant. The two orders are not the
            same, and where they part is the table below this one.
          </p>
          {association &&
            association.spearman_plus_minus !== null &&
            association.spearman_composite !== null && (
              <p className="mt-3 max-w-3xl text-sm text-ink-secondary">
                That distinction is the point of publishing it. A box-score
                career total tracks who a player&rsquo;s teammates were about as
                hard as it tracks the player. Over the {association.n} careers
                both boards carry, the board above correlates with career
                teammate strength at{" "}
                {association.spearman_composite.toFixed(3)} and this one at{" "}
                {association.spearman_plus_minus.toFixed(3)}
                {association.lo != null && association.hi != null && (
                  <>
                    {", a difference of "}
                    {association.difference?.toFixed(3) ?? "\u2014"}
                    {" with a 95% interval of "}
                    {association.lo.toFixed(3)} to {association.hi.toFixed(3)}
                  </>
                )}
                {". "}A stat line inflated by who else was on the server has no route
                into a number read from the map result. This board carries less
                of the situation. It is not free of it.
              </p>
            )}
          <p className="mt-3 max-w-3xl border-l-2 border-accent-dim pl-4 text-sm text-ink-secondary">
            <strong className="font-semibold text-ink">
              Read the intervals before the order.
            </strong>{" "}
            {plusMinusSeparated} of these {plusMinusBoard.length} totals are
            more than two standard deviations from zero. The other{" "}
            {plusMinusBoard.length - plusMinusSeparated} cannot be told apart
            from a replacement-level career, and almost no pair of rows can be
            told apart from each other. This is an ordering of estimates, not a
            ranking, and the ± column is the reason.
          </p>
          <p className="mt-3 max-w-3xl text-sm text-ink-muted">
            It covers the CDL era alone. CWL rosters barely changed inside a
            season, so there is nothing there for the method to read a season
            from, and those years hold one pooled coefficient per player for the
            whole era. Three copies of one estimate cannot be added into a
            career, so there is no all-time number on this axis and none is
            shown. It also reaches 174 of the 205 careers the board above
            qualifies; the 31 it misses all began between 2013 and 2015, where
            the archive records who won but not who was on the server.
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-normal">#</th>
                  <th className="py-2 pr-4 font-normal">Player</th>
                  <th className="py-2 pr-4 text-right font-normal">Seasons</th>
                  <th className="py-2 pr-4 text-right font-normal">Maps</th>
                  <th className="py-2 pr-4 text-right font-normal">
                    Career ± sd
                  </th>
                  <th className="py-2 pr-4 font-normal">95% interval</th>
                  <th
                    className="py-2 pr-4 text-right font-normal"
                    title="The same career crediting a quarter of the team-season term as well as the player's deviation from it. A choice with no right answer, so both are shown."
                  >
                    With team share
                  </th>
                  <th className="py-2 text-right font-normal">Box-score rank</th>
                </tr>
              </thead>
              <tbody>
                {plusMinusBoard.slice(0, 25).map((r) => (
                  <tr key={r.playerId} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                      {r.rank}
                    </td>
                    <td className="py-1.5 pr-4 font-medium">
                      <Link
                        href={`/players/${playerSlug(r.handle)}`}
                        className="hover:text-accent hover:underline"
                      >
                        {r.handle}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {r.seasons}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {r.maps.toLocaleString()}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      <span className={r.separated ? "" : "text-ink-secondary"}>
                        {r.total >= 0 ? "+" : ""}
                        {r.total.toFixed(3)}
                      </span>
                      {r.totalSd !== null && (
                        <span className="text-ink-muted">
                          {" "}
                          ±{r.totalSd.toFixed(3)}
                        </span>
                      )}
                    </td>
                    <td className="py-1.5 pr-4">
                      <IntervalBar
                        value={r.total}
                        sd={r.totalSd}
                        lo={plusMinusDomain.lo}
                        hi={plusMinusDomain.hi}
                        mark={0}
                        label={
                          r.totalSd === null
                            ? `${r.handle}: career plus-minus ${r.total.toFixed(3)}, no stored interval`
                            : `${r.handle}: career plus-minus ${r.total.toFixed(3)}, 95% ${(
                                r.total - 1.96 * r.totalSd
                              ).toFixed(3)} to ${(r.total + 1.96 * r.totalSd).toFixed(3)}`
                        }
                      />
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {r.totalWithTeam === null ? (
                        "—"
                      ) : (
                        <>
                          {r.totalWithTeam >= 0 ? "+" : ""}
                          {r.totalWithTeam.toFixed(3)}
                        </>
                      )}
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                      {r.compositeRank === null ? (
                        <span
                          className="text-ink-muted"
                          title="The box-score board does not qualify this career, which needs three qualified seasons."
                        >
                          —
                        </span>
                      ) : (
                        r.compositeRank
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 max-w-3xl text-xs text-ink-muted">
            A season coefficient is a deviation from the player&rsquo;s own
            team-season, so a career total either credits that deviation alone
            or adds a share of what the roster was worth beyond the four players
            in it. Neither is right: the first under-credits four players who
            genuinely were a great team, the second hands back the ambiguity the
            team term was added to remove. The main column is the deviation,
            because it is the quantity the model identifies; the team-share
            column is the same career under the other rule. Box-score rank is
            the career&rsquo;s place on the board above, out of 205. The full
            spec is on{" "}
            <Link href="/methodology#rapm" className="underline">
              methodology
            </Link>
            .
          </p>

          <h3 className="mt-10 font-display text-lg font-semibold uppercase tracking-wide">
            Where the two boards disagree
          </h3>
          <p className="mt-3 max-w-3xl text-sm text-ink-secondary">
            Both boards rank the {disagreements.length} careers they share, so
            the two ranks below are out of {disagreements.length} and not out of
            their own board&rsquo;s population. A career shows a different
            box-score rank here than in the table above, where that column is
            its place among all 205 qualified careers. Ranking 148 careers
            against a place out of 205 would read as a move that is really a
            change of denominator. A career moves when the map result and the
            box score tell different stories about it. A move only means
            something if it is larger than the distance this board could shift
            the career on its own, which is the <em>own band</em> column: how
            many places the career could sit either way inside its own
            interval.
          </p>
          <p className="mt-3 max-w-3xl border-l-2 border-accent-dim pl-4 text-sm text-ink-secondary">
            <strong className="font-semibold text-ink">
              {resolvedDisagreements.length} of {disagreements.length}
            </strong>{" "}
            careers disagree by more than their own band. For the other{" "}
            {disagreements.length - resolvedDisagreements.length} the two boards
            look like they differ and cannot be shown to. Only the{" "}
            {resolvedDisagreements.length} are listed.
          </p>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full max-w-3xl text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Player</th>
                  <th className="py-2 pr-4 text-right font-normal">
                    Box score
                  </th>
                  <th className="py-2 pr-4 text-right font-normal">
                    Map result
                  </th>
                  <th className="py-2 pr-4 text-right font-normal">Move</th>
                  <th className="py-2 pr-4 text-right font-normal">Own band</th>
                  <th className="py-2 font-normal">Which way</th>
                </tr>
              </thead>
              <tbody>
                {resolvedDisagreements.map((d) => (
                  <tr key={d.playerId} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-4 font-medium">
                      <Link
                        href={`/players/${playerSlug(d.handle)}`}
                        className="hover:text-accent hover:underline"
                      >
                        {d.handle}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {d.compositeRank}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {d.plusMinusRank}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {d.gap >= 0 ? "+" : ""}
                      {d.gap}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-muted">
                      ±{d.ownBand}
                    </td>
                    <td className="py-1.5 text-ink-secondary">
                      {d.gap > 0
                        ? "the map result rates the career higher"
                        : "the box score rates the career higher"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 max-w-3xl text-xs text-ink-muted">
            Move is the box-score rank minus the map-result rank, so a positive
            number is a career the map result puts higher. Neither board is the
            correction to the other, and nothing on this page is blended into
            the board above it: they are two readings of the same careers,
            published side by side because they disagree.
          </p>
        </section>
      )}
    </main>
  );
}
