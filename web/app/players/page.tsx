import type { Metadata } from "next";
import Link from "next/link";
import { CarryParams } from "@/components/table/CarryParams";
import type { SortState } from "@/components/table/tableState";
import { PlayersIndexTable } from "./PlayersIndexTable";
import {
  type PlayerIndexSort,
  getRatingComparison,
  getRatingLeaderboard,
  latestRatingRun,
  latestRun,
  queryPlayerIndex,
} from "@/lib/analytics";
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
