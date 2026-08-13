import type { Metadata } from "next";
import Link from "next/link";
import { CarryParams } from "@/components/table/CarryParams";
import type { SortState } from "@/components/table/tableState";
import { PlayersIndexTable } from "./PlayersIndexTable";
import { IntervalBar, halfWidth, overlaps } from "@/components/charts/RatingInterval";
import {
  type PlayerIndexSort,
  formatLeagueSpans,
  getEvaluationPrimary,
  getLeagueSpans,
  getRatingComparison,
  getRatingLeaderboard,
  getSkillLeaderboard,
  getSkillPrior,
  getSkillSeasons,
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
  const [ratingBoard, comparison, leagueSpans] = await Promise.all([
    getRatingLeaderboard(ratingRun.id, eraRun.id),
    getRatingComparison(ratingRun.id),
    getLeagueSpans(),
  ]);
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
            <Link href="/methodology#skill" className="underline">
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
            <Link href="/methodology#player-rating" className="underline">
              methodology
            </Link>
            .
          </p>
        </section>
      )}
    </main>
  );
}
