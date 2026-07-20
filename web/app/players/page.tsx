import type { Metadata } from "next";
import Link from "next/link";
import { Pager } from "@/components/Pager";
import {
  type PlayerIndexSort,
  countPlayerIndex,
  getRatingComparison,
  getRatingLeaderboard,
  latestRatingRun,
  latestRun,
  queryPlayerIndex,
  teamSlug,
} from "@/lib/analytics";
import {
  DEFAULT_PER,
  type SearchParams,
  buildQuery,
  clampPage,
  one,
  parsePaging,
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

/**
 * A column header that sorts. Clicking the active column flips direction;
 * clicking any other column starts at that column's natural direction. Sort
 * changes reset to page 1, since the row a reader was looking at will not be
 * on the same page under a different order.
 */
function SortHeader({
  col,
  label,
  align = "left",
  sort,
  dir,
  searchParams,
}: {
  col: PlayerIndexSort;
  label: string;
  align?: "left" | "right";
  sort: PlayerIndexSort;
  dir: "asc" | "desc";
  searchParams: SearchParams;
}) {
  const active = sort === col;
  const next = active ? (dir === "asc" ? "desc" : "asc") : SORTS[col];
  const href = `/players${buildQuery(searchParams, {
    sort: col === DEFAULT_SORT ? null : col,
    dir: next === SORTS[col] ? null : next,
    page: null,
  })}`;
  return (
    <th
      className={`py-2 pr-4 font-normal ${align === "right" ? "text-right" : ""}`}
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <Link
        href={href}
        className={active ? "text-ink hover:text-accent" : "hover:text-ink"}
      >
        {label}
        <span aria-hidden="true" className="ml-1 text-accent">
          {active ? (dir === "asc" ? "▲" : "▼") : ""}
        </span>
      </Link>
    </th>
  );
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

  const total = await countPlayerIndex(eraRun.id, { q });
  const paging = clampPage(parsePaging(sp), total);
  const rows = await queryPlayerIndex(
    eraRun.id,
    ratingRun.id,
    { q, sort, dir },
    paging,
  );

  // The composite rating board, ranking whole seasons rather than careers.
  const [ratingBoard, comparison] = await Promise.all([
    getRatingLeaderboard(ratingRun.id, eraRun.id),
    getRatingComparison(ratingRun.id),
  ]);
  const brierGain = comparison
    ? 1 -
      comparison.overall[comparison.published].brier /
        comparison.overall[comparison.baseline].brier
    : null;

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        {total.toLocaleString()} players · CWL 2017–2019
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
        {/* The sort and row count are links elsewhere on the page; carry the
            current values through so filtering does not silently reset them. */}
        {sort !== DEFAULT_SORT && (
          <input type="hidden" name="sort" value={sort} />
        )}
        {dir !== SORTS[sort] && <input type="hidden" name="dir" value={dir} />}
        {paging.per !== DEFAULT_PER && (
          <input type="hidden" name="per" value={paging.per} />
        )}
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
        <>
          <div className="mt-6 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-3 font-normal">#</th>
                  <SortHeader
                    col="handle"
                    label="Player"
                    sort={sort}
                    dir={dir}
                    searchParams={sp}
                  />
                  <SortHeader
                    col="last_year"
                    label="Active"
                    sort={sort}
                    dir={dir}
                    searchParams={sp}
                  />
                  <th className="py-2 pr-4 font-normal">Team</th>
                  <SortHeader
                    col="teams"
                    label="Teams"
                    align="right"
                    sort={sort}
                    dir={dir}
                    searchParams={sp}
                  />
                  <SortHeader
                    col="seasons"
                    label="Seasons"
                    align="right"
                    sort={sort}
                    dir={dir}
                    searchParams={sp}
                  />
                  <SortHeader
                    col="maps"
                    label="Maps"
                    align="right"
                    sort={sort}
                    dir={dir}
                    searchParams={sp}
                  />
                  <SortHeader
                    col="rating"
                    label="Best rating"
                    align="right"
                    sort={sort}
                    dir={dir}
                    searchParams={sp}
                  />
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.playerId} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-3 font-mono text-xs tabular-nums text-ink-muted">
                      {paging.offset + i + 1}
                    </td>
                    <td className="py-1.5 pr-4 font-medium">
                      <Link
                        href={`/players/${r.slug}`}
                        className="hover:text-accent hover:underline"
                      >
                        {r.handle}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-4 font-mono text-xs tabular-nums text-ink-secondary">
                      {r.firstYear === r.lastYear
                        ? r.firstYear
                        : `${r.firstYear}–${r.lastYear}`}
                    </td>
                    <td className="py-1.5 pr-4 text-ink-secondary">
                      {r.latestTeam ? (
                        <Link
                          href={`/teams/${teamSlug(r.latestTeam)}`}
                          className="hover:text-accent hover:underline"
                        >
                          {r.latestTeam}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {r.teamCount || "—"}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums text-ink-secondary">
                      {r.seasons}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {r.maps}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {r.bestRating !== null ? (
                        <>
                          {r.bestRating.toFixed(2)}
                          <span className="ml-1 text-ink-muted">
                            {r.bestRatingYear}
                          </span>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager
            basePath="/players"
            searchParams={sp}
            paging={paging}
            total={total}
            unit="players"
          />
        </>
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

      {ratingBoard.length > 0 && (
        <section className="mt-16 border-t border-hairline pt-8">
          <h2 className="lower-third">
            Player rating
            <span className="lt-note">top seasons, 30 maps minimum</span>
          </h2>
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
          <p className="mt-3 max-w-3xl text-xs text-ink-muted">
            The rating weights each stat by its regression coefficient for
            winning maps in that title and mode, shrinks small samples toward
            the league mean, and scales the result so an average qualified
            season is 1.00. Which stats it reads is measured per cohort, and
            covers first bloods, survival, time per life and — where a kill feed
            exists — trades. The ±sd comes from a map-resampling bootstrap.
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
