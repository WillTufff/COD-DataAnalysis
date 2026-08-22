// Read-side queries for model outputs. Everything resolves through the
// latest model_runs row per model, so pages always render one coherent,
// versioned snapshot and never mix runs.
import { and, asc, desc, eq, inArray, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import type { SeasonEra } from "@/lib/eras";
import type { ModeCatalog } from "@/lib/modes";
import { playerSlug, teamSlug } from "@/lib/slug";
import {
  backtests,
  eventPlacements,
  events,
  gameModes,
  gamePlayerStats,
  games,
  insights,
  modelRuns,
  players,
  playerMetricSeason,
  playerRapm,
  playerSeasonAdjusted,
  playerSkill,
  playerRoleSeason,
  playerStyleSeason,
  rosterStints,
  seasons,
  series,
  teamMetricSeason,
  teamRatings,
  teams,
  titles,
} from "@/db/schema";

export type ModelRun = {
  id: number;
  model: string;
  version: string;
  codeRef: string | null;
  params: unknown;
  dataThrough: string | null;
  createdAt: Date | null;
};

// The rating is fitted at several feature-set versions on every run — the
// box-score baseline and the intangible-weighted models — so "the newest run"
// is not a well-defined answer for it. The published version is pinned here
// and must match player_rating.PUBLISHED_VERSION in the analytics package;
// the other versions stay queryable as backtest baselines.
export const PUBLISHED_RATING_VERSION = "2.1.0";

export async function latestRun(
  model: string,
  version?: string,
): Promise<ModelRun | null> {
  const rows = await db
    .select()
    .from(modelRuns)
    .where(
      version === undefined
        ? eq(modelRuns.model, model)
        : and(eq(modelRuns.model, model), eq(modelRuns.version, version)),
    )
    .orderBy(desc(modelRuns.createdAt), desc(modelRuns.id))
    .limit(1);
  return rows[0] ?? null;
}

export function latestRatingRun(): Promise<ModelRun | null> {
  return latestRun("player_rating", PUBLISHED_RATING_VERSION);
}

export { playerSlug, teamSlug };

// ---------- Insight feed ----------

export type FeedItem = {
  id: number;
  kind: string;
  headline: string;
  detail: Record<string, unknown>;
  score: number;
  subjectType: string;
  subjectId: number;
  subjectName: string | null;
  subjectSlug: string | null; // player or team page slug, by subjectType
  // What the claim is worth against chance. findingClass says whether the
  // question even applies: only a testable finding carries q, and a retracted
  // one is published with the q that retracted it rather than removed.
  findingClass: string;
  qBh: number | null;
  qBy: number | null;
  retracted: boolean;
};

/**
 * The feed of one run.
 *
 * `retracted` selects one side of the error-control verdict: false is what the
 * run still stands behind, true is the retracted set. Leaving it undefined
 * returns both, which is what the methodology page counts over.
 */
export async function getFeed(
  runId: number,
  limit = 40,
  kind?: string,
  offset = 0,
  retracted?: boolean,
): Promise<FeedItem[]> {
  const conditions = [eq(insights.runId, runId)];
  if (kind) conditions.push(eq(insights.kind, kind));
  if (retracted !== undefined)
    conditions.push(eq(insights.retracted, retracted));
  const rows = await db
    .select({
      id: insights.id,
      kind: insights.kind,
      headline: insights.headline,
      detail: insights.detail,
      score: insights.score,
      subjectType: insights.subjectType,
      subjectId: insights.subjectId,
      findingClass: insights.findingClass,
      qBh: insights.qBh,
      qBy: insights.qBy,
      retracted: insights.retracted,
      playerHandle: players.handle,
      teamName: teams.name,
    })
    .from(insights)
    .leftJoin(
      players,
      and(eq(insights.subjectType, "player"), eq(players.id, insights.subjectId)),
    )
    .leftJoin(
      teams,
      and(eq(insights.subjectType, "team"), eq(teams.id, insights.subjectId)),
    )
    .where(and(...conditions))
    .orderBy(desc(insights.score), insights.id)
    .limit(limit)
    .offset(offset);

  return rows.map((r) => ({
    id: r.id,
    kind: r.kind,
    headline: r.headline,
    detail: (r.detail ?? {}) as Record<string, unknown>,
    score: r.score,
    subjectType: r.subjectType,
    subjectId: r.subjectId,
    findingClass: r.findingClass,
    qBh: r.qBh,
    qBy: r.qBy,
    retracted: r.retracted,
    subjectName: r.playerHandle ?? r.teamName,
    subjectSlug: r.playerHandle
      ? playerSlug(r.playerHandle)
      : r.teamName
        ? teamSlug(r.teamName)
        : null,
  }));
}

/**
 * Top findings for the overview, with a per-kind quota.
 *
 * Straight score ordering makes the front page monotonous: the highest-scoring
 * kinds are the per-cohort ones, so eight slots filled by score alone came back
 * as four near-identical trade-economy lines. Taking at most `maxPerKind` of
 * each kind, still in score order, shows the range of what the generator found.
 */
export async function getFeedHighlights(
  runId: number,
  limit = 8,
  maxPerKind = 2,
): Promise<FeedItem[]> {
  // Over-fetch so there is something to choose from once the quota bites. A
  // retracted finding never leads the front page: it stays readable on
  // /findings with the q-value that retracted it, which is a different job.
  const pool = await getFeed(runId, Math.max(limit * 6, 48), undefined, 0, false);
  const perKind = new Map<string, number>();
  const picked: FeedItem[] = [];
  for (const item of pool) {
    if (picked.length >= limit) break;
    const n = perKind.get(item.kind) ?? 0;
    if (n >= maxPerKind) continue;
    perKind.set(item.kind, n + 1);
    picked.push(item);
  }
  // If the quota left the page short (few kinds in this run), backfill by score.
  for (const item of pool) {
    if (picked.length >= limit) break;
    if (!picked.includes(item)) picked.push(item);
  }
  return picked;
}

export async function getFeedKinds(
  runId: number,
  retracted?: boolean,
): Promise<{ kind: string; n: number }[]> {
  const rows = await db.execute(sql`
    SELECT kind, count(*) AS n FROM insights WHERE run_id = ${runId}
    ${retracted === undefined ? sql`` : sql`AND retracted = ${retracted}`}
    GROUP BY kind ORDER BY count(*) DESC
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    kind: String(r.kind),
    n: Number(r.n),
  }));
}

// ---------- Error control over the feed ----------

export type ErrorControl = {
  qThreshold: number;
  nFindings: number;
  nTested: number;
  nRetracted: number;
  byClass: Record<string, number>;
  sensitivity: { q: number; kept: number; declared: boolean }[];
  families: {
    kind: string;
    class: string;
    published: number;
    tested?: number;
    failsThreshold?: number;
    byDisagrees?: number;
    qMedian?: number | null;
    reason?: string;
  }[];
};

type ErrorControlPayload = {
  q_threshold: number;
  n_findings: number;
  n_tested: number;
  n_retracted: number;
  by_class: Record<string, number>;
  sensitivity: { q: number; kept: number; declared: boolean }[];
  families: {
    kind: string;
    class: string;
    published: number;
    tested?: number;
    fails_threshold?: number;
    by_disagrees?: number;
    q_bh?: { median: number } | null;
    reason?: string;
  }[];
};

/** The q distribution the current run published, family by family. */
export async function getErrorControl(): Promise<
  { runId: number; dataThrough: string | null; control: ErrorControl } | null
> {
  const run = await latestRun("error_control");
  if (!run) return null;
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${run.id} AND name = 'error_control'
  `);
  const p = (rows as unknown as { payload: ErrorControlPayload }[])[0]?.payload;
  if (!p) return null;
  return {
    runId: run.id,
    dataThrough: run.dataThrough,
    control: {
      qThreshold: p.q_threshold,
      nFindings: p.n_findings,
      nTested: p.n_tested,
      nRetracted: p.n_retracted,
      byClass: p.by_class ?? {},
      sensitivity: p.sensitivity ?? [],
      families: (p.families ?? []).map((f) => ({
        kind: f.kind,
        class: f.class,
        published: f.published,
        tested: f.tested,
        failsThreshold: f.fails_threshold,
        byDisagrees: f.by_disagrees,
        qMedian: f.q_bh?.median ?? null,
        reason: f.reason,
      })),
    },
  };
}

// ---------- Archive overview ----------

export type ArchiveStats = {
  seriesCount: number;
  maps: number;
  players: number;
  statRows: number;
  events: number;
  firstYear: number;
  lastYear: number;
  /** e.g. "2013–2026" — read off the data so a new season never leaves stale copy. */
  span: string;
};

export async function getArchiveStats(): Promise<ArchiveStats> {
  const rows = await db.execute(sql`
    SELECT (SELECT count(*) FROM series) AS series_count,
           (SELECT count(*) FROM games) AS maps,
           (SELECT count(DISTINCT player_id) FROM game_player_stats) AS players,
           (SELECT count(*) FROM game_player_stats) AS stat_rows,
           (SELECT count(*) FROM events) AS events,
           (SELECT min(year) FROM seasons) AS first_year,
           (SELECT max(year) FROM seasons) AS last_year
  `);
  const r = (rows as unknown as Record<string, unknown>[])[0];
  const firstYear = Number(r.first_year);
  const lastYear = Number(r.last_year);
  return {
    seriesCount: Number(r.series_count),
    maps: Number(r.maps),
    players: Number(r.players),
    statRows: Number(r.stat_rows),
    events: Number(r.events),
    firstYear,
    lastYear,
    span: firstYear === lastYear ? `${firstYear}` : `${firstYear}–${lastYear}`,
  };
}

// ---------- Coverage labels ----------

/** e.g. "2013–2026", or a single year when a span covers one season. */
export function formatYearSpan(firstYear: number, lastYear: number): string {
  return firstYear === lastYear ? `${firstYear}` : `${firstYear}–${lastYear}`;
}

export type LeagueSpan = { league: string; firstYear: number; lastYear: number };

function leagueSpanRows(rows: unknown): LeagueSpan[] {
  return (rows as Record<string, unknown>[]).map((r) => ({
    league: String(r.league),
    firstYear: Number(r.first_year),
    lastYear: Number(r.last_year),
  }));
}

/** The years each league ran, oldest first. */
export async function getLeagueSpans(): Promise<LeagueSpan[]> {
  const rows = await db.execute(sql`
    SELECT se.league, min(se.year) AS first_year, max(se.year) AS last_year
    FROM seasons se
    WHERE EXISTS (
      SELECT 1 FROM events e JOIN series s ON s.event_id = e.id
      WHERE e.season_id = se.id
    )
    GROUP BY se.league
    ORDER BY min(se.year)
  `);
  return leagueSpanRows(rows);
}

/** e.g. "MLG 2013–2015 · CWL 2016–2019 · CDL 2020–2026", or "archive" for an entity with none. */
export function formatLeagueSpans(spans: LeagueSpan[]): string {
  if (spans.length === 0) return "archive";
  return spans
    .map((s) => `${s.league} ${formatYearSpan(s.firstYear, s.lastYear)}`)
    .join(" · ");
}

/** The years one player appears in, from the maps they have a box score on. */
export async function getPlayerSpans(playerId: number): Promise<LeagueSpan[]> {
  const rows = await db.execute(sql`
    SELECT se.league, min(se.year) AS first_year, max(se.year) AS last_year
    FROM game_player_stats gps
    JOIN games g ON g.id = gps.game_id
    JOIN series s ON s.id = g.series_id
    JOIN events e ON e.id = s.event_id
    JOIN seasons se ON se.id = e.season_id
    WHERE gps.player_id = ${playerId}
    GROUP BY se.league
    ORDER BY min(se.year)
  `);
  return leagueSpanRows(rows);
}

/** The years one team appears in, from the series it played. */
export async function getTeamSpans(teamId: number): Promise<LeagueSpan[]> {
  const rows = await db.execute(sql`
    SELECT se.league, min(se.year) AS first_year, max(se.year) AS last_year
    FROM series s
    JOIN events e ON e.id = s.event_id
    JOIN seasons se ON se.id = e.season_id
    WHERE s.team1_id = ${teamId} OR s.team2_id = ${teamId}
    GROUP BY se.league
    ORDER BY min(se.year)
  `);
  return leagueSpanRows(rows);
}

// The site's mode vocabulary: every mode `game_modes` names, in the order they
// entered the archive. Read once per request by anything that labels or orders
// by mode, so a new mode is named the moment it is ingested.
export async function getModeCatalog(): Promise<ModeCatalog> {
  const rows = await db
    .select({ slug: gameModes.slug, name: gameModes.name })
    .from(gameModes)
    .orderBy(asc(gameModes.id));
  return {
    order: rows.map((r) => r.slug),
    names: Object.fromEntries(rows.map((r) => [r.slug, r.name])),
  };
}

// Every archived season in year order, with the league that ran it. The
// era-coloured charts read their legend from this rather than from a list of
// the seasons that existed when they were written.
export async function getSeasonEras(): Promise<SeasonEra[]> {
  const rows = await db.execute(sql`
    SELECT se.year, t.short_name AS title, se.league
    FROM seasons se
    JOIN titles t ON t.id = se.title_id
    WHERE EXISTS (SELECT 1 FROM events e WHERE e.season_id = se.id)
    ORDER BY se.year
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    year: Number(r.year),
    title: String(r.title),
    league: String(r.league),
  }));
}

// One span per season×title: the shaded era bands on the rating race chart.
export type EraSpan = {
  year: number;
  title: string;
  from: string; // ISO timestamps of first/last archived series
  to: string;
  seriesCount: number;
};

export async function getEraSpans(): Promise<EraSpan[]> {
  const rows = await db.execute(sql`
    SELECT se.year, t.short_name AS title,
           min(s.played_at) AS from_t, max(s.played_at) AS to_t,
           count(*) AS series_count
    FROM series s
    JOIN events e ON e.id = s.event_id
    JOIN seasons se ON se.id = e.season_id
    JOIN titles t ON t.id = se.title_id
    WHERE s.played_at IS NOT NULL
    GROUP BY se.year, t.short_name
    ORDER BY min(s.played_at)
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    year: Number(r.year),
    title: String(r.title),
    from: new Date(String(r.from_t)).toISOString(),
    to: new Date(String(r.to_t)).toISOString(),
    seriesCount: Number(r.series_count),
  }));
}

// One marker per archived event: the span of its rated series, for the event
// lane on the rating race chart. "Major" = open LANs and Champs; league
// phases, qualifiers and relegation are context-only (unlabeled, hover for
// the name).
export type EventMarker = {
  name: string;
  from: string; // ISO timestamps of first/last archived series
  to: string;
  major: boolean;
};

export async function getEventMarkers(): Promise<EventMarker[]> {
  const rows = await db.execute(sql`
    SELECT e.name, e.tier, min(s.played_at) AS from_t, max(s.played_at) AS to_t
    FROM series s
    JOIN events e ON e.id = s.event_id
    WHERE s.played_at IS NOT NULL
    GROUP BY e.id, e.name, e.tier
    ORDER BY min(s.played_at)
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    name: String(r.name),
    from: new Date(String(r.from_t)).toISOString(),
    to: new Date(String(r.to_t)).toISOString(),
    major: String(r.tier) === "S" || !/pro league/i.test(String(r.name)),
  }));
}

// League engagement pace per season×mode — the "raw stats are not comparable"
// evidence. Kills per player-seat per 10 minutes, from complete duration data.
export type PaceCell = {
  year: number;
  title: string;
  mode: string;
  maps: number;
  killsPer10: number;
};

export async function getPaceByMode(): Promise<PaceCell[]> {
  const rows = await db.execute(sql`
    SELECT se.year, t.short_name AS title, gm.name AS mode,
           count(DISTINCT g.id) AS maps,
           sum(gps.kills)::float / nullif(sum(g.duration_s / 600.0), 0) AS k10
    FROM game_player_stats gps
    JOIN games g ON g.id = gps.game_id
    JOIN game_modes gm ON gm.id = g.mode_id
    JOIN series s ON s.id = g.series_id
    JOIN events e ON e.id = s.event_id
    JOIN seasons se ON se.id = e.season_id
    JOIN titles t ON t.id = se.title_id
    WHERE gps.kills IS NOT NULL AND g.duration_s IS NOT NULL
    GROUP BY se.year, t.short_name, gm.name
    ORDER BY gm.name, se.year
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    year: Number(r.year),
    title: String(r.title),
    mode: String(r.mode),
    maps: Number(r.maps),
    killsPer10: Number(r.k10),
  }));
}

// ---------- Player index ----------

export type PlayerIndexRow = {
  playerId: number;
  handle: string;
  slug: string;
  maps: number; // career maps, all modes combined
  seasons: number;
  firstYear: number;
  lastYear: number;
  teamCount: number;
  latestTeam: string | null;
  bestRating: number | null; // best qualified season, null if none qualify
  bestRatingSd: number | null; // posterior sd of that rating
  bestRatingYear: number | null;
  bestRatingTitle: string | null;
};

export type PlayerIndexSort =
  | "handle"
  | "maps"
  | "seasons"
  | "teams"
  | "rating"
  | "last_year";

export type PlayerIndexQuery = {
  q?: string; // handle substring
  sort: PlayerIndexSort;
  dir: "asc" | "desc";
};

// Sorting is by whitelisted key rather than raw input: the column has to be
// interpolated unquoted, so it must never be caller-controlled text.
const PLAYER_INDEX_SORT_COLS: Record<PlayerIndexSort, string> = {
  handle: "p.handle",
  maps: "c.maps",
  seasons: "c.seasons",
  teams: "r.team_count",
  rating: "b.rating",
  last_year: "c.last_year",
};

/** Matching players, for sizing the pager before the page itself is fetched. */
export async function countPlayerIndex(
  eraRunId: number,
  q: Pick<PlayerIndexQuery, "q">,
): Promise<number> {
  const rows = await db.execute(sql`
    SELECT COUNT(DISTINCT psa.player_id) AS n
    FROM player_season_adjusted psa
    JOIN players p ON p.id = psa.player_id
    WHERE psa.run_id = ${eraRunId} AND psa.mode_id IS NULL
      AND ${q.q ? sql`p.handle ILIKE ${"%" + q.q + "%"}` : sql`TRUE`}
  `);
  return Number((rows as unknown as { n: unknown }[])[0]?.n ?? 0);
}

/**
 * One row per player with career aggregates, paged. Career totals come from
 * the era run's all-modes rows; the rating column reports each player's best
 * season at the same 30-map minimum the rating board uses, so players who
 * never cleared it read as "—" rather than being ranked on a handful of maps.
 *
 * The caller is expected to have clamped `paging` against `countPlayerIndex`
 * first: this returns whatever the offset lands on, including nothing.
 */
export async function queryPlayerIndex(
  eraRunId: number,
  ratingRunId: number,
  q: PlayerIndexQuery,
  paging: { offset: number; limit: number },
  minRatingMaps = 30,
): Promise<PlayerIndexRow[]> {
  const sortCol = PLAYER_INDEX_SORT_COLS[q.sort];
  const dir = q.dir === "asc" ? "ASC" : "DESC";
  const result = await db.execute(sql`
    WITH career AS (
      SELECT psa.player_id,
             SUM(psa.maps_played)::int AS maps,
             COUNT(DISTINCT psa.season_id)::int AS seasons,
             MIN(se.year)::int AS first_year,
             MAX(se.year)::int AS last_year
      FROM player_season_adjusted psa
      JOIN seasons se ON se.id = psa.season_id
      WHERE psa.run_id = ${eraRunId} AND psa.mode_id IS NULL
      GROUP BY psa.player_id
    ), best AS (
      SELECT DISTINCT ON (psa.player_id)
             psa.player_id, psa.rating, psa.rating_sd,
             se.year AS rating_year, t.short_name AS rating_title
      FROM player_season_adjusted psa
      JOIN seasons se ON se.id = psa.season_id
      JOIN titles t ON t.id = se.title_id
      WHERE psa.run_id = ${ratingRunId} AND psa.mode_id IS NULL
        AND psa.rating IS NOT NULL AND psa.maps_played >= ${minRatingMaps}
      ORDER BY psa.player_id, psa.rating DESC
    ), rosters AS (
      SELECT rs.player_id,
             COUNT(DISTINCT rs.team_id)::int AS team_count,
             (ARRAY_AGG(tm.name ORDER BY rs.start_date DESC))[1] AS latest_team
      FROM roster_stints rs
      JOIN teams tm ON tm.id = rs.team_id
      GROUP BY rs.player_id
    )
    SELECT p.id AS player_id, p.handle,
           c.maps, c.seasons, c.first_year, c.last_year,
           r.team_count, r.latest_team,
           b.rating, b.rating_sd, b.rating_year, b.rating_title
    FROM players p
    JOIN career c ON c.player_id = p.id
    LEFT JOIN rosters r ON r.player_id = p.id
    LEFT JOIN best b ON b.player_id = p.id
    WHERE ${q.q ? sql`p.handle ILIKE ${"%" + q.q + "%"}` : sql`TRUE`}
    ORDER BY ${sql.raw(sortCol)} ${sql.raw(dir)} NULLS LAST, p.handle ASC
    LIMIT ${paging.limit} OFFSET ${paging.offset}
  `);
  const raw = result as unknown as Record<string, unknown>[];
  return raw.map((r) => ({
    playerId: Number(r.player_id),
    handle: String(r.handle),
    slug: playerSlug(String(r.handle)),
    maps: Number(r.maps),
    seasons: Number(r.seasons),
    firstYear: Number(r.first_year),
    lastYear: Number(r.last_year),
    teamCount: r.team_count === null ? 0 : Number(r.team_count),
    latestTeam: r.latest_team === null ? null : String(r.latest_team),
    bestRating: r.rating === null ? null : Number(r.rating),
    bestRatingSd: r.rating_sd === null ? null : Number(r.rating_sd),
    bestRatingYear: r.rating_year === null ? null : Number(r.rating_year),
    bestRatingTitle: r.rating_title === null ? null : String(r.rating_title),
  }));
}

// ---------- Player page ----------

export async function getPlayerBySlug(slug: string) {
  const rows = await db
    .select()
    .from(players)
    .where(sql`lower(${players.handle}) = ${slug}`)
    .limit(1);
  return rows[0] ?? null;
}

/** Every player slug, for prerendering the player pages at build time. */
export async function getAllPlayerSlugs(): Promise<string[]> {
  const rows = await db.select({ handle: players.handle }).from(players);
  return rows.map((r) => playerSlug(r.handle));
}

export type SeasonAdjusted = {
  seasonId: number;
  year: number;
  title: string;
  modeId: number | null;
  mode: string | null;
  mapsPlayed: number;
  kdRaw: number | null;
  kdZ: number | null;
  /** Standard error of kdZ, computed by the era model against the same cohort
   *  SD the z-score used. NULL where the sample cannot support one. */
  kdZSe: number | null;
  kdPctl: number | null;
  engagementZ: number | null;
  objZ: number | null;
  completeness: number;
};

export async function getPlayerAdjusted(
  playerId: number,
  eraRunId: number,
): Promise<SeasonAdjusted[]> {
  const rows = await db
    .select({
      seasonId: playerSeasonAdjusted.seasonId,
      year: seasons.year,
      title: titles.shortName,
      modeId: playerSeasonAdjusted.modeId,
      mode: gameModes.name,
      mapsPlayed: playerSeasonAdjusted.mapsPlayed,
      kdRaw: playerSeasonAdjusted.kdRaw,
      kdZ: playerSeasonAdjusted.kdZ,
      kdZSe: playerSeasonAdjusted.kdZSe,
      kdPctl: playerSeasonAdjusted.kdPctl,
      engagementZ: playerSeasonAdjusted.engagementZ,
      objZ: playerSeasonAdjusted.objZ,
      completeness: playerSeasonAdjusted.completeness,
    })
    .from(playerSeasonAdjusted)
    .innerJoin(seasons, eq(seasons.id, playerSeasonAdjusted.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .leftJoin(gameModes, eq(gameModes.id, playerSeasonAdjusted.modeId))
    .where(
      and(
        eq(playerSeasonAdjusted.runId, eraRunId),
        eq(playerSeasonAdjusted.playerId, playerId),
      ),
    )
    .orderBy(seasons.year, playerSeasonAdjusted.modeId);
  return rows;
}

export type PlayerRatingSeason = {
  seasonId: number;
  year: number;
  title: string;
  mapsPlayed: number;
  rating: number;
  /** Posterior sd. NULL only if the run predates 0011; drawn as no band. */
  ratingSd: number | null;
  qualified: boolean; // cleared the board's map minimum
};

export type PlayerRatings = {
  seasons: PlayerRatingSeason[];
  /** Rating range over every qualified season in the run, so one player's
   *  intervals are drawn on the same scale as everyone else's. */
  scale: { lo: number; hi: number };
  minMaps: number;
};

/**
 * A player's composite rating season by season, with the posterior sd that
 * belongs to each. Returns null when the player has no rated season, so the
 * caller can drop the section rather than draw an empty axis.
 */
export async function getPlayerRatingSeasons(
  playerId: number,
  ratingRunId: number,
  minMaps = 30,
): Promise<PlayerRatings | null> {
  const rows = await db.execute(sql`
    SELECT psa.season_id, se.year, t.short_name AS title,
           psa.maps_played, psa.rating, psa.rating_sd,
           (SELECT MIN(q.rating) FROM player_season_adjusted q
             WHERE q.run_id = ${ratingRunId} AND q.mode_id IS NULL
               AND q.rating IS NOT NULL AND q.maps_played >= ${minMaps}) AS cohort_lo,
           (SELECT MAX(q.rating) FROM player_season_adjusted q
             WHERE q.run_id = ${ratingRunId} AND q.mode_id IS NULL
               AND q.rating IS NOT NULL AND q.maps_played >= ${minMaps}) AS cohort_hi
    FROM player_season_adjusted psa
    JOIN seasons se ON se.id = psa.season_id
    JOIN titles t ON t.id = se.title_id
    WHERE psa.run_id = ${ratingRunId} AND psa.mode_id IS NULL
      AND psa.player_id = ${playerId} AND psa.rating IS NOT NULL
    ORDER BY se.year
  `);
  const raw = rows as unknown as Record<string, unknown>[];
  if (raw.length === 0) return null;
  const seasons = raw.map((r) => ({
    seasonId: Number(r.season_id),
    year: Number(r.year),
    title: String(r.title),
    mapsPlayed: Number(r.maps_played),
    rating: Number(r.rating),
    ratingSd: r.rating_sd === null ? null : Number(r.rating_sd),
    qualified: Number(r.maps_played) >= minMaps,
  }));
  // An unqualified season can sit outside the qualified cohort's range, and a
  // 95% band reaches past its point; widen the scale so nothing is clipped.
  const lo = Math.min(
    Number(raw[0].cohort_lo),
    ...seasons.map((s) => s.rating - 1.96 * (s.ratingSd ?? 0)),
  );
  const hi = Math.max(
    Number(raw[0].cohort_hi),
    ...seasons.map((s) => s.rating + 1.96 * (s.ratingSd ?? 0)),
  );
  return { seasons, scale: { lo, hi }, minMaps };
}

export async function getPlayerStints(playerId: number) {
  return db
    .select({
      teamId: rosterStints.teamId,
      team: teams.name,
      startDate: rosterStints.startDate,
      endDate: rosterStints.endDate,
    })
    .from(rosterStints)
    .innerJoin(teams, eq(teams.id, rosterStints.teamId))
    .where(eq(rosterStints.playerId, playerId))
    .orderBy(rosterStints.startDate);
}

export async function getPlayerInsights(playerId: number, insightsRunId: number) {
  return db
    .select({
      id: insights.id,
      kind: insights.kind,
      headline: insights.headline,
      detail: insights.detail,
      score: insights.score,
    })
    .from(insights)
    .where(
      and(
        eq(insights.runId, insightsRunId),
        eq(insights.subjectType, "player"),
        eq(insights.subjectId, playerId),
      ),
    )
    .orderBy(desc(insights.score));
}

// ---------- Ratings page ----------

export type TeamStanding = {
  teamId: number;
  team: string;
  region: string | null;
  finalElo: number;
  peakElo: number;
  glicko: number | null;
  glickoRd: number | null;
  nSeries: number;
  lastPlayed: Date | null;
};

export async function getTeamStandings(
  eloRunId: number,
  glickoRunId: number,
): Promise<TeamStanding[]> {
  // Final = rating_post of each team's chronologically last rated series.
  const rows = await db.execute(sql`
    WITH ordered AS (
      SELECT tr.run_id, tr.team_id, tr.rating_post, tr.rating_sd, s.played_at,
             row_number() OVER (
               PARTITION BY tr.run_id, tr.team_id ORDER BY s.played_at DESC, s.id DESC
             ) AS rn
      FROM team_ratings tr JOIN series s ON s.id = tr.series_id
      WHERE tr.run_id IN (${eloRunId}, ${glickoRunId})
    ),
    elo AS (
      SELECT team_id,
             max(rating_post) FILTER (WHERE rn = 1) AS final_elo,
             count(*) AS n_series,
             max(played_at) AS last_played
      FROM ordered WHERE run_id = ${eloRunId} GROUP BY team_id
    ),
    elo_peak AS (
      SELECT team_id, max(rating_post) AS peak_elo
      FROM team_ratings WHERE run_id = ${eloRunId} GROUP BY team_id
    ),
    gl AS (
      SELECT team_id, rating_post AS glicko, rating_sd AS glicko_rd
      FROM ordered WHERE run_id = ${glickoRunId} AND rn = 1
    )
    SELECT t.id AS team_id, t.name AS team, t.region,
           elo.final_elo, elo_peak.peak_elo, gl.glicko, gl.glicko_rd,
           elo.n_series, elo.last_played
    FROM elo
    JOIN elo_peak USING (team_id)
    LEFT JOIN gl USING (team_id)
    JOIN teams t ON t.id = elo.team_id
    ORDER BY elo.final_elo DESC
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    teamId: Number(r.team_id),
    team: String(r.team),
    region: r.region === null ? null : String(r.region),
    finalElo: Number(r.final_elo),
    peakElo: Number(r.peak_elo),
    glicko: r.glicko === null ? null : Number(r.glicko),
    glickoRd: r.glicko_rd === null ? null : Number(r.glicko_rd),
    nSeries: Number(r.n_series),
    lastPlayed: r.last_played ? new Date(String(r.last_played)) : null,
  }));
}

// `rd` is the rating deviation stored alongside the rating: Glicko-2 writes it,
// Elo leaves it NULL (fit.py has no uncertainty to report). Consumers that draw
// a band must handle the null rather than assume zero — an unknown deviation is
// not a confident one.
export type EloPoint = { t: string; rating: number; rd: number | null };
export type EloTimeline = { teamId: number; team: string; points: EloPoint[] };

export async function getEloTimelines(
  eloRunId: number,
  teamIds: number[],
): Promise<EloTimeline[]> {
  if (teamIds.length === 0) return [];
  const rows = await db
    .select({
      teamId: teamRatings.teamId,
      team: teams.name,
      playedAt: series.playedAt,
      rating: teamRatings.ratingPost,
      rd: teamRatings.ratingSd,
    })
    .from(teamRatings)
    .innerJoin(series, eq(series.id, teamRatings.seriesId))
    .innerJoin(teams, eq(teams.id, teamRatings.teamId))
    .where(and(eq(teamRatings.runId, eloRunId), inArray(teamRatings.teamId, teamIds)))
    .orderBy(series.playedAt, series.id);

  const byTeam = new Map<number, EloTimeline>();
  for (const r of rows) {
    let tl = byTeam.get(r.teamId);
    if (!tl) {
      tl = { teamId: r.teamId, team: r.team, points: [] };
      byTeam.set(r.teamId, tl);
    }
    tl.points.push({
      t: r.playedAt?.toISOString() ?? "",
      rating: r.rating,
      rd: r.rd,
    });
  }
  // Preserve caller's ranking order.
  return teamIds.map((id) => byTeam.get(id)).filter((x): x is EloTimeline => !!x);
}

export type LeaderboardRow = {
  playerId: number;
  handle: string;
  slug: string;
  year: number;
  title: string;
  mapsPlayed: number;
  kdRaw: number | null;
  kdZ: number | null;
  /** Standard error of kdZ from the era model; NULL where the sample is too
   *  thin for one. Carried so the board can show what it does not know. */
  kdZSe: number | null;
  kdPctl: number | null;
};


export async function getPlayerLeaderboard(
  eraRunId: number,
  minMaps = 30,
): Promise<LeaderboardRow[]> {
  const rows = await db
    .select({
      playerId: playerSeasonAdjusted.playerId,
      handle: players.handle,
      year: seasons.year,
      title: titles.shortName,
      mapsPlayed: playerSeasonAdjusted.mapsPlayed,
      kdRaw: playerSeasonAdjusted.kdRaw,
      kdZ: playerSeasonAdjusted.kdZ,
      kdZSe: playerSeasonAdjusted.kdZSe,
      kdPctl: playerSeasonAdjusted.kdPctl,
    })
    .from(playerSeasonAdjusted)
    .innerJoin(players, eq(players.id, playerSeasonAdjusted.playerId))
    .innerJoin(seasons, eq(seasons.id, playerSeasonAdjusted.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .where(
      and(
        eq(playerSeasonAdjusted.runId, eraRunId),
        sql`${playerSeasonAdjusted.modeId} IS NULL`,
        sql`${playerSeasonAdjusted.mapsPlayed} >= ${minMaps}`,
        sql`${playerSeasonAdjusted.kdZ} IS NOT NULL`,
      ),
    );
  return rows.map((r) => ({ ...r, slug: playerSlug(r.handle) }));
}

// ---------- Methodology ----------

export type BacktestCard = {
  model: string;
  version: string;
  runId: number;
  params: unknown;
  windowFrom: string;
  windowTo: string;
  n: number;
  brier: number | null;
  logLoss: number | null;
  accuracy: number | null;
  calibration: { lo: number; hi: number; n: number; mean_pred?: number; frac_won?: number }[];
};

export async function getBacktestCards(runIds: number[]): Promise<BacktestCard[]> {
  if (runIds.length === 0) return [];
  const rows = await db
    .select({
      model: modelRuns.model,
      version: modelRuns.version,
      runId: backtests.runId,
      params: modelRuns.params,
      windowFrom: backtests.windowFrom,
      windowTo: backtests.windowTo,
      n: backtests.nPredictions,
      brier: backtests.brier,
      logLoss: backtests.logLoss,
      accuracy: backtests.accuracy,
      calibration: backtests.calibration,
    })
    .from(backtests)
    .innerJoin(modelRuns, eq(modelRuns.id, backtests.runId))
    .where(inArray(backtests.runId, runIds));
  return rows.map((r) => ({
    ...r,
    calibration: (r.calibration ?? []) as BacktestCard["calibration"],
  }));
}

export type CoverageRow = {
  year: number;
  title: string;
  events: number;
  seriesCount: number;
  games: number;
  playerMapRows: number;
  hillTimePct: number;
  extrasPct: number;
};

export async function getCoverage(): Promise<CoverageRow[]> {
  const rows = await db.execute(sql`
    SELECT se.year, t.short_name AS title,
           count(DISTINCT e.id) AS events,
           count(DISTINCT s.id) AS series_count,
           count(DISTINCT g.id) AS games,
           count(gps.*) AS player_map_rows,
           avg((gps.hill_time IS NOT NULL)::int)::float AS hill_time_pct,
           avg((gps.extras ? 'ekia')::int)::float AS extras_pct
    FROM seasons se
    JOIN titles t ON t.id = se.title_id
    JOIN events e ON e.season_id = se.id
    JOIN series s ON s.event_id = e.id
    JOIN games g ON g.series_id = s.id
    JOIN game_player_stats gps ON gps.game_id = g.id
    GROUP BY se.year, t.short_name
    ORDER BY se.year
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    year: Number(r.year),
    title: String(r.title),
    events: Number(r.events),
    seriesCount: Number(r.series_count),
    games: Number(r.games),
    playerMapRows: Number(r.player_map_rows),
    hillTimePct: Number(r.hill_time_pct),
    extrasPct: Number(r.extras_pct),
  }));
}

// ---------- Open player rating (player_rating_v1) ----------

export type RatingRow = {
  playerId: number;
  handle: string;
  slug: string;
  year: number;
  title: string;
  mapsPlayed: number;
  rating: number;
  ratingSd: number | null;
  kdRaw: number | null; // context from the era run, same season
};

// Blended (all-mode) season ratings from the player_rating run, with the era
// run's raw K/D joined on for context — the rating and the stat it re-weighs,
// side by side.
export async function getRatingLeaderboard(
  ratingRunId: number,
  eraRunId: number,
  minMaps = 30,
  limit = 20,
): Promise<RatingRow[]> {
  const rows = await db.execute(sql`
    SELECT pr.player_id, p.handle, se.year, t.short_name AS title,
           pr.maps_played, pr.rating, pr.rating_sd, era.kd_raw
    FROM player_season_adjusted pr
    JOIN players p ON p.id = pr.player_id
    JOIN seasons se ON se.id = pr.season_id
    JOIN titles t ON t.id = se.title_id
    LEFT JOIN player_season_adjusted era
      ON era.run_id = ${eraRunId} AND era.player_id = pr.player_id
     AND era.season_id = pr.season_id AND era.mode_id IS NULL
    WHERE pr.run_id = ${ratingRunId} AND pr.mode_id IS NULL
      AND pr.rating IS NOT NULL AND pr.maps_played >= ${minMaps}
    ORDER BY pr.rating DESC
    LIMIT ${limit}
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    playerId: Number(r.player_id),
    handle: String(r.handle),
    slug: playerSlug(String(r.handle)),
    year: Number(r.year),
    title: String(r.title),
    mapsPlayed: Number(r.maps_played),
    rating: Number(r.rating),
    ratingSd: r.rating_sd === null ? null : Number(r.rating_sd),
    kdRaw: r.kd_raw === null ? null : Number(r.kd_raw),
  }));
}

export type ModeWeightCohort = {
  year: number;
  title: string;
  mode: string;
  nMaps: number;
  weights: Record<string, number>;
  labels: Record<string, string>;
  restFeatures: string[]; // the non-slaying keys the ratio averaged over
  restVsSlay: number; // mean |weight| beyond the gunfight / mean |slaying weight|
  // 95% percentile bootstrap over the cohort's maps. Null on runs written before
  // the bootstrap existed — absent, not wide, so the chart draws no whisker
  // rather than an invented one.
  restVsSlayCi: [number, number] | null;
};

// The learned map-outcome regression weights, one cohort per (season × mode).
//
// Which features are the slaying pair is read off the artifact, never hardcoded:
// feature sets differ per cohort (SnD counts kills and deaths per round, respawn
// modes per 10 minutes) and per version, so a fixed key list silently reads zero
// the moment the published version changes. The pair is read jointly because a
// team's kills mirror its opponent's deaths, leaving the two near-collinear and
// their shared weight split by the ridge penalty.
//
// This mirrors what_wins in insights.py — the two must agree, because the chart
// and the finding make the same claim.
export async function getModeWeights(ratingRunId: number): Promise<ModeWeightCohort[]> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'mode_weights'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | {
        cohorts: {
          year: number;
          title: string;
          mode: string;
          n_maps: number;
          weights: Record<string, number>;
          slaying_features?: string[];
          labels?: Record<string, string>;
          rest_vs_slay?: number;
          rest_vs_slay_ci?: [number, number];
        }[];
      }
    | undefined;
  if (!payload) return [];
  const mean = (keys: string[], w: Record<string, number>) =>
    keys.reduce((s, k) => s + Math.abs(w[k] ?? 0), 0) / keys.length;
  return payload.cohorts.map((c) => {
    const slaying = c.slaying_features?.length
      ? c.slaying_features
      : ["kills_p10", "deaths_p10"];
    const rest = Object.keys(c.weights).filter((k) => !slaying.includes(k));
    const slay = mean(slaying, c.weights);
    // The fit publishes the ratio alongside its interval; recomputing it is the
    // fallback for runs written before it did.
    const computed = slay > 0 && rest.length > 0 ? mean(rest, c.weights) / slay : 0;
    return {
      year: c.year,
      title: c.title,
      mode: c.mode,
      nMaps: c.n_maps,
      weights: c.weights,
      labels: c.labels ?? {},
      restFeatures: rest,
      restVsSlay: c.rest_vs_slay ?? computed,
      restVsSlayCi: c.rest_vs_slay_ci ?? null,
    };
  });
}

export type ModelGapPair = {
  a: string;
  b: string;
  delta: number;
  lo: number;
  hi: number;
  excludesZero: boolean;
  dmT: number | null;
  dmP: number | null;
  mde80: number; // smallest gap this many series could resolve at 80% power
  accuracyDelta: number;
  accuracyLo: number;
  accuracyHi: number;
  accuracyExcludesZero: boolean;
};

export type ModelGaps = {
  nSeries: number;
  bootstrapB: number;
  models: Record<
    string,
    {
      brier: number;
      brierLo: number;
      brierHi: number;
      accuracy: number;
      accuracyLo: number;
      accuracyHi: number;
    }
  >;
  pairs: ModelGapPair[];
  // Power on the momentum question: the smallest form coefficient the archive
  // could have detected, and what it would be worth in win probability.
  formBeta: number | null;
  formSwingPp: number | null;
};

// Paired intervals on the backtest table's gaps, stored with the winprob run.
// The table is sorted by Brier, which reads as a ranking; this is what says
// which of those orderings the data actually supports.
export async function getModelGaps(winprobRunId: number): Promise<ModelGaps | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${winprobRunId} AND name = 'model_gaps'
  `);
  type Raw = {
    available?: boolean;
    n_series: number;
    bootstrap_b: number;
    models: Record<string, Record<string, number>>;
    pairs: Record<string, string | number | boolean | null>[];
    form_power?: { beta_detectable: number | null; swing_pp: number | null };
  };
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | Raw
    | undefined;
  if (!payload?.available) return null;
  return {
    nSeries: payload.n_series,
    bootstrapB: payload.bootstrap_b,
    models: Object.fromEntries(
      Object.entries(payload.models).map(([k, m]) => [
        k,
        {
          brier: m.brier,
          brierLo: m.brier_lo,
          brierHi: m.brier_hi,
          accuracy: m.accuracy,
          accuracyLo: m.accuracy_lo,
          accuracyHi: m.accuracy_hi,
        },
      ]),
    ),
    pairs: payload.pairs.map((p) => ({
      a: String(p.a),
      b: String(p.b),
      delta: Number(p.delta),
      lo: Number(p.lo),
      hi: Number(p.hi),
      excludesZero: Boolean(p.excludes_zero),
      dmT: p.dm_t === null ? null : Number(p.dm_t),
      dmP: p.dm_p === null ? null : Number(p.dm_p),
      mde80: Number(p.mde80),
      accuracyDelta: Number(p.accuracy_delta),
      accuracyLo: Number(p.accuracy_lo),
      accuracyHi: Number(p.accuracy_hi),
      accuracyExcludesZero: Boolean(p.accuracy_excludes_zero),
    })),
    formBeta: payload.form_power?.beta_detectable ?? null,
    formSwingPp: payload.form_power?.swing_pp ?? null,
  };
}

export type WinprobArtifact = {
  finalWeights: Record<string, number>;
  finalIntercept: number;
  minTrain: number;
  refitEvery: number;
  formWindow: number;
  // The rating settings this run's features were built from, carried on the
  // artifact so the page can say which fit the pass-through phase reproduces.
  // Absent on runs written before winprob took them from the caller.
  period: string | null;
  eloK: number | null;
  glickoTau: number | null;
};

// ---------- Teams ----------

export async function getTeamBySlug(slug: string) {
  const rows = await db.select().from(teams);
  return rows.find((t) => teamSlug(t.name) === slug) ?? null;
}

/** Every team slug, for prerendering the team pages at build time. */
export async function getAllTeamSlugs(): Promise<string[]> {
  const rows = await db.select({ name: teams.name }).from(teams);
  return rows.map((t) => teamSlug(t.name));
}

export type SeriesRecord = { wins: number; losses: number };

// Decided-series win/loss record per team, whole archive.
export async function getSeriesRecords(): Promise<Map<number, SeriesRecord>> {
  const rows = await db.execute(sql`
    SELECT team_id, sum(win) AS wins, count(*) - sum(win) AS losses FROM (
      SELECT s.team1_id AS team_id, (s.team1_score > s.team2_score)::int AS win
      FROM series s
      WHERE s.team1_id IS NOT NULL AND s.team1_score IS NOT NULL
        AND s.team2_score IS NOT NULL AND s.team1_score <> s.team2_score
      UNION ALL
      SELECT s.team2_id, (s.team2_score > s.team1_score)::int
      FROM series s
      WHERE s.team2_id IS NOT NULL AND s.team1_score IS NOT NULL
        AND s.team2_score IS NOT NULL AND s.team1_score <> s.team2_score
    ) x GROUP BY team_id
  `);
  const m = new Map<number, SeriesRecord>();
  for (const r of rows as unknown as Record<string, unknown>[]) {
    m.set(Number(r.team_id), { wins: Number(r.wins), losses: Number(r.losses) });
  }
  return m;
}

export type PlacementRow = {
  eventId: number;
  event: string;
  startDate: string | null;
  year: number | null;
  placementMin: number | null;
  placementMax: number | null;
  prize: number | null;
};

export async function getTeamPlacements(teamId: number): Promise<PlacementRow[]> {
  const rows = await db
    .select({
      eventId: eventPlacements.eventId,
      event: events.name,
      startDate: events.startDate,
      year: seasons.year,
      placementMin: eventPlacements.placementMin,
      placementMax: eventPlacements.placementMax,
      prize: eventPlacements.prize,
    })
    .from(eventPlacements)
    .innerJoin(events, eq(events.id, eventPlacements.eventId))
    .leftJoin(seasons, eq(seasons.id, events.seasonId))
    .where(eq(eventPlacements.teamId, teamId))
    .orderBy(events.startDate);
  return rows.map((r) => ({
    ...r,
    placementMin: r.placementMin === null ? null : Number(r.placementMin),
    placementMax: r.placementMax === null ? null : Number(r.placementMax),
    prize: r.prize === null ? null : Number(r.prize),
  }));
}

export type TeamStint = {
  playerId: number;
  handle: string;
  slug: string;
  role: string | null;
  startDate: string;
  endDate: string | null;
  /** "lpdb" for a dated roster move, "cwl-archive" for one inferred from play. */
  source: string | null;
};

export async function getTeamStints(teamId: number): Promise<TeamStint[]> {
  const rows = await db
    .select({
      playerId: rosterStints.playerId,
      handle: players.handle,
      role: rosterStints.role,
      startDate: rosterStints.startDate,
      endDate: rosterStints.endDate,
      source: rosterStints.source,
    })
    .from(rosterStints)
    .innerJoin(players, eq(players.id, rosterStints.playerId))
    .where(eq(rosterStints.teamId, teamId))
    .orderBy(rosterStints.startDate, players.handle);
  return rows.map((r) => ({ ...r, slug: playerSlug(r.handle) }));
}

export type ModeSplit = { mode: string; maps: number; wins: number };

// Map win rate per mode for one team, decided maps only.
export async function getTeamModeSplits(teamId: number): Promise<ModeSplit[]> {
  const rows = await db.execute(sql`
    SELECT gm.name AS mode, count(*) AS maps,
           sum((g.winner_team_id = ${teamId})::int) AS wins
    FROM games g
    JOIN series s ON s.id = g.series_id
    JOIN game_modes gm ON gm.id = g.mode_id
    WHERE g.winner_team_id IS NOT NULL
      AND (s.team1_id = ${teamId} OR s.team2_id = ${teamId})
    GROUP BY gm.name
    ORDER BY count(*) DESC
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    mode: String(r.mode),
    maps: Number(r.maps),
    wins: Number(r.wins),
  }));
}

export type ModeStrengthRow = {
  mode: string;
  // Mode rating minus global rating, then minus the field's mean gap in that
  // mode. See getTeamModeStrength for why the second subtraction is required.
  rel: number;
  nMaps: number;
};

export type ModeStrength = {
  rows: ModeStrengthRow[];
  minModeMaps: number;
  // The permutation null this quantity has to be read against.
  nullSd: number;
  nullLo: number;
  nullHi: number;
  observedSd: number;
  pValue: number;
  exceedsNull: boolean;
};

// Per-mode team strength, adjusted for opposition, for one team.
//
// The map_elo run publishes a mode_ratings artifact carrying each qualified
// (team, mode) cell's rating alongside that team's global rating. Its `delta`
// is not usable as published: across the 98 qualified cells it averages -34
// and is negative in 72 of them, because a mode rating is fit on a fraction of
// the maps the global rating sees and regresses further toward the initial
// value. Printed raw it would tell a reader that almost every team is worse at
// every mode than they are overall, which cannot be true of a set of modes that
// make up the whole. The offset is mode-specific -- control -57, hardpoint -24
// -- so it is removed per mode, leaving a gap against the field rather than
// against an artifact of the estimator.
//
// The specialization null travels with the rows and is not optional. It reports
// that the spread across cells does not exceed what shuffling produces, so no
// caller may present these as established mode specialisms.
export async function getTeamModeStrength(
  teamId: number,
): Promise<ModeStrength | null> {
  const elo = await getMapElo();
  if (!elo?.modeRatings || !elo.specialization.available) return null;

  const all = elo.modeRatings.rows;
  const mine = all.filter((r) => r.team_id === teamId);
  if (mine.length === 0) return null;

  const fieldMean = new Map<string, number>();
  for (const mode of new Set(all.map((r) => r.mode))) {
    const cells = all.filter((r) => r.mode === mode);
    fieldMean.set(
      mode,
      cells.reduce((a, r) => a + r.delta, 0) / cells.length,
    );
  }

  const s = elo.specialization;
  return {
    rows: mine
      .map((r) => ({
        mode: r.mode,
        rel: r.delta - (fieldMean.get(r.mode) ?? 0),
        nMaps: r.n_maps,
      }))
      .sort((a, b) => b.rel - a.rel),
    minModeMaps: elo.modeRatings.min_mode_maps,
    nullSd: s.null_mean_sd ?? 0,
    nullLo: s.null_lo ?? 0,
    nullHi: s.null_hi ?? 0,
    observedSd: s.observed_sd ?? 0,
    pValue: s.p_value ?? 1,
    exceedsNull: s.exceeds_null ?? false,
  };
}

export type H2HRow = {
  opponentId: number;
  opponent: string;
  opponentSlug: string;
  wins: number;
  losses: number;
};

// Decided-series record vs each opponent, most-played first.
export async function getTeamH2H(teamId: number, limit = 12): Promise<H2HRow[]> {
  const rows = await db.execute(sql`
    SELECT opp.id AS opponent_id, opp.name AS opponent,
           sum(x.win) AS wins, count(*) - sum(x.win) AS losses
    FROM (
      SELECT s.team2_id AS opponent_id, (s.team1_score > s.team2_score)::int AS win
      FROM series s
      WHERE s.team1_id = ${teamId} AND s.team2_id IS NOT NULL
        AND s.team1_score IS NOT NULL AND s.team2_score IS NOT NULL
        AND s.team1_score <> s.team2_score
      UNION ALL
      SELECT s.team1_id, (s.team2_score > s.team1_score)::int
      FROM series s
      WHERE s.team2_id = ${teamId} AND s.team1_id IS NOT NULL
        AND s.team1_score IS NOT NULL AND s.team2_score IS NOT NULL
        AND s.team1_score <> s.team2_score
    ) x
    JOIN teams opp ON opp.id = x.opponent_id
    GROUP BY opp.id, opp.name
    ORDER BY count(*) DESC, opp.name
    LIMIT ${limit}
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    opponentId: Number(r.opponent_id),
    opponent: String(r.opponent),
    opponentSlug: teamSlug(String(r.opponent)),
    wins: Number(r.wins),
    losses: Number(r.losses),
  }));
}

export type H2HCell = { rowId: number; colId: number; wins: number; losses: number };

// Pairwise decided-series records among a set of teams.
export async function getH2HMatrix(teamIds: number[]): Promise<H2HCell[]> {
  if (teamIds.length < 2) return [];
  const rows = await db.execute(sql`
    SELECT a AS row_id, b AS col_id, sum(win) AS wins, count(*) - sum(win) AS losses
    FROM (
      SELECT s.team1_id AS a, s.team2_id AS b,
             (s.team1_score > s.team2_score)::int AS win
      FROM series s
      WHERE s.team1_id IN ${sql.raw(`(${teamIds.join(",")})`)}
        AND s.team2_id IN ${sql.raw(`(${teamIds.join(",")})`)}
        AND s.team1_score IS NOT NULL AND s.team2_score IS NOT NULL
        AND s.team1_score <> s.team2_score
      UNION ALL
      SELECT s.team2_id, s.team1_id, (s.team2_score > s.team1_score)::int
      FROM series s
      WHERE s.team1_id IN ${sql.raw(`(${teamIds.join(",")})`)}
        AND s.team2_id IN ${sql.raw(`(${teamIds.join(",")})`)}
        AND s.team1_score IS NOT NULL AND s.team2_score IS NOT NULL
        AND s.team1_score <> s.team2_score
    ) x GROUP BY a, b
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    rowId: Number(r.row_id),
    colId: Number(r.col_id),
    wins: Number(r.wins),
    losses: Number(r.losses),
  }));
}

// ---------- Cohort distributions ----------

export type SeasonSpread = { year: number; title: string; values: number[] };

// Raw K/D of every qualified all-modes player-season, grouped by season.
export async function getSeasonKdSpread(
  eraRunId: number,
  minMaps = 30,
): Promise<SeasonSpread[]> {
  const rows = await db.execute(sql`
    SELECT se.year, t.short_name AS title, psa.kd_raw
    FROM player_season_adjusted psa
    JOIN seasons se ON se.id = psa.season_id
    JOIN titles t ON t.id = se.title_id
    WHERE psa.run_id = ${eraRunId} AND psa.mode_id IS NULL
      AND psa.maps_played >= ${minMaps} AND psa.kd_raw IS NOT NULL
    ORDER BY se.year, psa.kd_raw
  `);
  const byYear = new Map<number, SeasonSpread>();
  for (const r of rows as unknown as Record<string, unknown>[]) {
    const year = Number(r.year);
    let s = byYear.get(year);
    if (!s) {
      s = { year, title: String(r.title), values: [] };
      byYear.set(year, s);
    }
    s.values.push(Number(r.kd_raw));
  }
  return [...byYear.values()];
}

export async function getWinprobArtifact(
  winprobRunId: number,
): Promise<WinprobArtifact | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${winprobRunId} AND name = 'coefficients'
  `);
  const p = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | {
        final_weights: Record<string, number>;
        final_intercept: number;
        min_train: number;
        refit_every: number;
        form_window: number;
        period?: string;
        elo_k?: number;
        glicko_tau?: number;
      }
    | undefined;
  if (!p) return null;
  return {
    finalWeights: p.final_weights,
    finalIntercept: p.final_intercept,
    minTrain: p.min_train,
    refitEvery: p.refit_every,
    formWindow: p.form_window,
    period: p.period ?? null,
    eloK: p.elo_k ?? null,
    glickoTau: p.glicko_tau ?? null,
  };
}

// ---------- Metric layer ----------

export type MetricCatalogEntry = {
  key: string;
  label: string;
  category: string;
  tier: string;
  unit: string;
  higher_is_better: boolean;
  formula: string;
  denom_kind: string;
  min_denom: number;
  sources: string[];
  titles: string[];
  modes: string[];
  note: string | null;
};

export type UntrackedColumn = {
  title: string;
  column: string;
  rows: number;
  nonzero: number;
};

export type KillFeedConstants = {
  trade_window_ms: number;
  trade: string;
  advantage_state: string;
  clutch: string;
  reconciliation: string;
};

export type MetricCatalog = {
  version: string;
  min_nonzero_rows: number;
  metrics: MetricCatalogEntry[];
  // Published from 2.0.0; entries omit the per-title coverage fields.
  team_metrics?: Omit<MetricCatalogEntry, "sources" | "titles">[];
  untracked_columns: UntrackedColumn[];
  kill_feed_constants?: KillFeedConstants;
};

export type ReconciliationTitle = {
  title: string;
  player_maps: number;
  reconciled: number;
  box_deaths: number;
  feed_deaths: number;
  rate: number;
};

export async function getKillFeedReconciliation(): Promise<
  { dataThrough: string | null; byTitle: ReconciliationTitle[] } | null
> {
  const run = await latestRun("kill_feed_reconciliation");
  if (!run) return null;
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${run.id} AND name = 'kill_feed_reconciliation'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | { by_title?: ReconciliationTitle[] }
    | undefined;
  if (!payload) return null;
  return { dataThrough: run.dataThrough, byTitle: payload.by_title ?? [] };
}

export async function getMetricCatalog(
  metricRunId: number,
): Promise<MetricCatalog | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${metricRunId} AND name = 'metric_catalog'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | MetricCatalog
    | undefined;
  return payload ?? null;
}

/**
 * The team metrics, from the same catalog artifact's `team_metrics` array.
 * Published entries omit the per-title coverage fields the player entries
 * carry, so those are defaulted rather than left undefined — the report
 * builder treats every team metric as chartable.
 */
export async function getTeamMetricCatalog(
  metricRunId: number,
): Promise<MetricCatalogEntry[]> {
  const rows = await db.execute(sql`
    SELECT payload->'team_metrics' AS team_metrics FROM model_artifacts
    WHERE run_id = ${metricRunId} AND name = 'metric_catalog'
  `);
  const entries = (rows as unknown as { team_metrics: unknown }[])[0]
    ?.team_metrics as Partial<MetricCatalogEntry>[] | null | undefined;
  if (!entries) return [];
  return entries.map((m) => ({
    sources: [],
    titles: ["all"],
    ...m,
  })) as MetricCatalogEntry[];
}

export type MetricRow = {
  playerId: number;
  handle: string;
  slug: string;
  year: number;
  title: string;
  mode: string | null;
  value: number;
  denom: number;
  z: number | null;
  pctl: number | null;
  qualified: boolean;
};

export type MetricQuery = {
  metric: string;
  year?: number;
  modeSlug?: string;
  qualifiedOnly: boolean;
  dir: "asc" | "desc";
};

/**
 * The season + mode cohort filter, shared by the single-metric path and the
 * report pivot so a cohort means the same thing in both. `year` undefined and
 * `years` empty both mean all covered seasons; `modeSlug` undefined = the
 * all-modes rows (mode_id NULL). Seasons combine (the report builder picks a
 * set of them); modes never do, because comparing modes is the whole point of
 * separating them.
 */
function cohortConditions(q: {
  year?: number;
  years?: number[];
  modeSlug?: string;
}) {
  const conditions = [];
  if (q.year !== undefined) conditions.push(eq(seasons.year, q.year));
  if (q.years !== undefined && q.years.length > 0) {
    conditions.push(inArray(seasons.year, q.years));
  }
  if (q.modeSlug !== undefined) {
    conditions.push(eq(gameModes.slug, q.modeSlug));
  } else {
    conditions.push(sql`${playerMetricSeason.modeId} IS NULL`);
  }
  return conditions;
}

/** The filter shared by the row query and its count, so the two cannot drift. */
function metricConditions(metricRunId: number, q: MetricQuery) {
  const conditions = [
    eq(playerMetricSeason.runId, metricRunId),
    eq(playerMetricSeason.metric, q.metric),
    ...cohortConditions(q),
  ];
  if (q.qualifiedOnly) conditions.push(eq(playerMetricSeason.qualified, true));
  return and(...conditions);
}

/** Matching rows, for sizing the pager before the page itself is fetched. */
export async function countMetric(
  metricRunId: number,
  q: MetricQuery,
): Promise<number> {
  const rows = await db
    .select({ n: sql<number>`COUNT(*)` })
    .from(playerMetricSeason)
    .innerJoin(players, eq(players.id, playerMetricSeason.playerId))
    .innerJoin(seasons, eq(seasons.id, playerMetricSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .leftJoin(gameModes, eq(gameModes.id, playerMetricSeason.modeId))
    .where(metricConditions(metricRunId, q));
  return Number(rows[0]?.n ?? 0);
}

export async function queryMetric(
  metricRunId: number,
  q: MetricQuery,
  paging: { offset: number; limit: number },
): Promise<MetricRow[]> {
  const rows = await db
    .select({
      playerId: playerMetricSeason.playerId,
      handle: players.handle,
      year: seasons.year,
      title: titles.shortName,
      mode: gameModes.slug,
      value: playerMetricSeason.value,
      denom: playerMetricSeason.denom,
      z: playerMetricSeason.z,
      pctl: playerMetricSeason.pctl,
      qualified: playerMetricSeason.qualified,
    })
    .from(playerMetricSeason)
    .innerJoin(players, eq(players.id, playerMetricSeason.playerId))
    .innerJoin(seasons, eq(seasons.id, playerMetricSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .leftJoin(gameModes, eq(gameModes.id, playerMetricSeason.modeId))
    .where(metricConditions(metricRunId, q))
    // Ties are broken on handle so paging is stable: without a total order,
    // the same row can appear on two pages or on none.
    .orderBy(
      q.dir === "asc" ? playerMetricSeason.value : desc(playerMetricSeason.value),
      players.handle,
    )
    .limit(paging.limit)
    .offset(paging.offset);
  return rows.map((r) => ({ ...r, slug: playerSlug(r.handle) }));
}

/** Seasons and modes that actually have rows for a metric, for the pickers. */
export async function getMetricScope(
  metricRunId: number,
  metric: string,
): Promise<{ years: number[]; modes: string[] }> {
  const rows = await db
    .select({ year: seasons.year, mode: gameModes.slug })
    .from(playerMetricSeason)
    .innerJoin(seasons, eq(seasons.id, playerMetricSeason.seasonId))
    .leftJoin(gameModes, eq(gameModes.id, playerMetricSeason.modeId))
    .where(
      and(
        eq(playerMetricSeason.runId, metricRunId),
        eq(playerMetricSeason.metric, metric),
      ),
    )
    .groupBy(seasons.year, gameModes.slug);
  const years = [...new Set(rows.map((r) => r.year))].sort();
  const modes = [
    ...new Set(rows.map((r) => r.mode).filter((m): m is string => m !== null)),
  ].sort();
  return { years, modes };
}

// ── Report builder: many metrics as columns, players as rows ────────────────
//
// The pivot of the single-metric path. `queryMetric` pulls one metric across
// all players; `queryReport` pulls several at once for a fixed cohort and
// pivots them into a wide table. Qualification is per *cell* (a player can
// clear the sample minimum for one column and not another), so cells are
// sparse — the renderer greys an unqualified cell and shows "—" for an absent
// one, and never drops a row for it.

/** One metric's value for one player, in one cohort. */
export type ReportCell = {
  value: number;
  denom: number;
  z: number | null;
  pctl: number | null;
  qualified: boolean;
};

/** A column descriptor, so the client renderer can format without the catalog. */
export type ReportColumn = {
  key: string;
  label: string;
  unit: string;
  higherIsBetter: boolean;
  denomKind: string;
  minDenom: number;
};

/** One player in the cohort, with a sparse map of metric → cell. */
export type ReportRow = {
  playerId: number;
  handle: string;
  slug: string;
  year: number;
  title: string;
  mode: string | null;
  cells: Record<string, ReportCell>;
};

export type ReportQuery = {
  metrics: string[]; // ordered column keys
  years?: number[]; // empty/undefined = all covered seasons
  modeSlug?: string; // undefined = all-modes rows (mode_id IS NULL)
  players?: string[]; // player slugs; empty/undefined = everyone
  teams?: string[]; // team slugs; empty/undefined = every team
  qualifiedOnly: boolean; // gate rows on the SORT metric's qualified flag
  sort: string; // a metric key, or "player"
  dir: "asc" | "desc";
};

/**
 * The wide report for a cohort: one query over all selected metrics, pivoted
 * in JS by `(playerId, year, mode)`, then sorted on the chosen column. Cells
 * are sparse; a player missing the sort metric sorts to the bottom in either
 * direction (an absent value is never "best" or "worst", just absent). Rows are
 * returned whole and unpaged — the client table pages and re-sorts them, like
 * the single-metric path's `FETCH_ALL`.
 *
 * `catalog` is passed in rather than re-fetched: the caller already resolved it
 * to validate the requested keys, and it carries the column metadata (unit,
 * direction, sample floor) the renderer needs.
 */
export async function queryReport(
  metricRunId: number,
  q: ReportQuery,
  catalog: MetricCatalogEntry[],
): Promise<{ columns: ReportColumn[]; rows: ReportRow[] }> {
  const byKey = new Map(catalog.map((m) => [m.key, m]));
  // Preserve the requested column order; silently drop keys the catalog no
  // longer knows (a stale link must degrade, not error).
  const columns: ReportColumn[] = q.metrics
    .map((k) => byKey.get(k))
    .filter((m): m is MetricCatalogEntry => m !== undefined)
    .map((m) => ({
      key: m.key,
      label: m.label,
      unit: m.unit,
      higherIsBetter: m.higher_is_better,
      denomKind: m.denom_kind,
      minDenom: m.min_denom,
    }));
  const keys = columns.map((c) => c.key);
  if (keys.length === 0) return { columns, rows: [] };

  const raw = await db
    .select({
      metric: playerMetricSeason.metric,
      playerId: playerMetricSeason.playerId,
      handle: players.handle,
      year: seasons.year,
      title: titles.shortName,
      mode: gameModes.slug,
      value: playerMetricSeason.value,
      denom: playerMetricSeason.denom,
      z: playerMetricSeason.z,
      pctl: playerMetricSeason.pctl,
      qualified: playerMetricSeason.qualified,
    })
    .from(playerMetricSeason)
    .innerJoin(players, eq(players.id, playerMetricSeason.playerId))
    .innerJoin(seasons, eq(seasons.id, playerMetricSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .leftJoin(gameModes, eq(gameModes.id, playerMetricSeason.modeId))
    .where(
      and(
        eq(playerMetricSeason.runId, metricRunId),
        inArray(playerMetricSeason.metric, keys),
        ...cohortConditions(q),
      ),
    );

  // Pivot: one row per (player, season, mode); each source row drops into its
  // metric's cell. With a single-season + single-mode cohort this is one row
  // per player, but the key stays general so all-modes / all-seasons cohorts
  // pivot correctly too.
  const rowsById = new Map<string, ReportRow>();
  for (const r of raw) {
    const id = `${r.playerId}-${r.year}-${r.mode ?? "all"}`;
    let row = rowsById.get(id);
    if (!row) {
      row = {
        playerId: r.playerId,
        handle: r.handle,
        slug: playerSlug(r.handle),
        year: r.year,
        title: r.title,
        mode: r.mode,
        cells: {},
      };
      rowsById.set(id, row);
    }
    row.cells[r.metric] = {
      value: r.value,
      denom: r.denom,
      z: r.z,
      pctl: r.pctl,
      qualified: r.qualified,
    };
  }
  let rows = [...rowsById.values()];

  // The player and team filters narrow rows, never the cohort: percentiles and
  // z-scores were scored against the full field, so a filtered table still
  // reads "Simp vs everyone", not "Simp vs the two players next to him". Slugs
  // are derived from names in JS, which is why this lives after the pivot and
  // not in SQL. A team pick keeps the player-seasons that team fielded, so a
  // multi-season report shows each roster as it was that year.
  const picked = q.players && q.players.length > 0 ? new Set(q.players) : null;
  if (picked) {
    rows = rows.filter((row) => picked.has(row.slug));
  }
  const teamPicked = q.teams !== undefined && q.teams.length > 0;
  if (teamPicked) {
    const members = await teamMemberSeasons(q.teams!);
    rows = rows.filter((row) => members.has(`${row.playerId}-${row.year}`));
  }

  return { columns, rows: gateAndSortReportRows(rows, q, keys, !!picked || teamPicked) };
}

/**
 * The report's shared tail, after the pivot and any row filters. "Qualified
 * only" gates each row on the sort column's cell: a row without a qualified
 * value in the column being ranked has no business being ranked. Other columns
 * keep their cells (greyed when below their own minimum). When sorting by name
 * there is no metric to gate on, so the flag is a no-op. An explicit row
 * filter (`filtered`) suspends the gate too — asking for Scump, or for OpTic,
 * means seeing those rows (cells still grey themselves), not a table missing
 * whoever fell short of one column's sample floor.
 */
function gateAndSortReportRows(
  rows: ReportRow[],
  q: ReportQuery,
  keys: string[],
  filtered: boolean,
): ReportRow[] {
  const sortIsMetric = keys.includes(q.sort);
  if (q.qualifiedOnly && sortIsMetric && !filtered) {
    rows = rows.filter((row) => row.cells[q.sort]?.qualified === true);
  }

  const factor = q.dir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    if (!sortIsMetric) return factor * a.handle.localeCompare(b.handle);
    const av = a.cells[q.sort]?.value ?? null;
    const bv = b.cells[q.sort]?.value ?? null;
    // Absent values sink to the bottom regardless of direction, then name
    // breaks ties so paging is stable.
    if (av === null && bv === null) return a.handle.localeCompare(b.handle);
    if (av === null) return 1;
    if (bv === null) return -1;
    if (av === bv) return a.handle.localeCompare(b.handle);
    return factor * (av - bv);
  });

  return rows;
}

/**
 * `queryReport`'s mirror over the team metric layer: teams as rows, the same
 * cohort contract, pivoted by `(teamId, year, mode)`. Rows reuse the
 * `ReportRow` shape — `playerId`/`handle`/`slug` carry the team's id, name and
 * slug — so the table, export and paging machinery serve both entities. The
 * `teams` filter matches rows directly by slug here (the rows *are* teams);
 * the `players` filter does not apply.
 */
export async function queryTeamReport(
  metricRunId: number,
  q: ReportQuery,
  catalog: MetricCatalogEntry[],
): Promise<{ columns: ReportColumn[]; rows: ReportRow[] }> {
  const byKey = new Map(catalog.map((m) => [m.key, m]));
  const columns: ReportColumn[] = q.metrics
    .map((k) => byKey.get(k))
    .filter((m): m is MetricCatalogEntry => m !== undefined)
    .map((m) => ({
      key: m.key,
      label: m.label,
      unit: m.unit,
      higherIsBetter: m.higher_is_better,
      denomKind: m.denom_kind,
      minDenom: m.min_denom,
    }));
  const keys = columns.map((c) => c.key);
  if (keys.length === 0) return { columns, rows: [] };

  const raw = await db
    .select({
      metric: teamMetricSeason.metric,
      teamId: teamMetricSeason.teamId,
      name: teams.name,
      year: seasons.year,
      title: titles.shortName,
      mode: gameModes.slug,
      value: teamMetricSeason.value,
      denom: teamMetricSeason.denom,
      z: teamMetricSeason.z,
      pctl: teamMetricSeason.pctl,
      qualified: teamMetricSeason.qualified,
    })
    .from(teamMetricSeason)
    .innerJoin(teams, eq(teams.id, teamMetricSeason.teamId))
    .innerJoin(seasons, eq(seasons.id, teamMetricSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .leftJoin(gameModes, eq(gameModes.id, teamMetricSeason.modeId))
    .where(
      and(
        eq(teamMetricSeason.runId, metricRunId),
        inArray(teamMetricSeason.metric, keys),
        ...teamCohortConditions(q),
      ),
    );

  const rowsById = new Map<string, ReportRow>();
  for (const r of raw) {
    const id = `${r.teamId}-${r.year}-${r.mode ?? "all"}`;
    let row = rowsById.get(id);
    if (!row) {
      row = {
        playerId: r.teamId,
        handle: r.name,
        slug: teamSlug(r.name),
        year: r.year,
        title: r.title,
        mode: r.mode,
        cells: {},
      };
      rowsById.set(id, row);
    }
    row.cells[r.metric] = {
      value: r.value,
      denom: r.denom,
      z: r.z,
      pctl: r.pctl,
      qualified: r.qualified,
    };
  }
  let rows = [...rowsById.values()];

  const picked = q.teams && q.teams.length > 0 ? new Set(q.teams) : null;
  if (picked) {
    rows = rows.filter((row) => picked.has(row.slug));
  }

  return { columns, rows: gateAndSortReportRows(rows, q, keys, !!picked) };
}

/** `cohortConditions`, for the team metric table. */
function teamCohortConditions(q: { years?: number[]; modeSlug?: string }) {
  const conditions = [];
  if (q.years !== undefined && q.years.length > 0) {
    conditions.push(inArray(seasons.year, q.years));
  }
  if (q.modeSlug !== undefined) {
    conditions.push(eq(gameModes.slug, q.modeSlug));
  } else {
    conditions.push(sql`${teamMetricSeason.modeId} IS NULL`);
  }
  return conditions;
}

/** A player the report's picker can offer. */
export type ScopePlayer = {
  handle: string;
  slug: string; // what rides the URL and matches ReportRow.slug
};

/**
 * Every player with a row in the run, for the picker's search list. Run-wide
 * rather than cohort-scoped on purpose: the pick survives switching seasons or
 * modes, and a picked player with no rows in the new cohort simply contributes
 * nothing rather than being silently unpicked.
 */
export async function getReportPlayers(
  metricRunId: number,
): Promise<ScopePlayer[]> {
  const rows = await db
    .select({ handle: players.handle })
    .from(playerMetricSeason)
    .innerJoin(players, eq(players.id, playerMetricSeason.playerId))
    .where(eq(playerMetricSeason.runId, metricRunId))
    .groupBy(players.handle);
  return rows
    .map((r) => ({ handle: r.handle, slug: playerSlug(r.handle) }))
    .sort((a, b) => a.handle.localeCompare(b.handle));
}

/** A team the report's picker can offer. */
export type ScopeTeam = {
  name: string;
  slug: string; // what rides the URL — teamSlug(name), the site-wide identity
};

/**
 * Every team that fielded a stat line, for the picker's search list. Identity
 * is the slug: two DB team rows sharing a name (an org re-registered across
 * years) are one pickable team, which is how the rest of the site treats them.
 */
export async function getReportTeams(): Promise<ScopeTeam[]> {
  const rows = await db
    .select({ name: teams.name })
    .from(gamePlayerStats)
    .innerJoin(teams, eq(teams.id, gamePlayerStats.teamId))
    .groupBy(teams.name);
  const bySlug = new Map<string, ScopeTeam>();
  for (const r of rows) {
    const slug = teamSlug(r.name);
    if (!bySlug.has(slug)) bySlug.set(slug, { name: r.name, slug });
  }
  return [...bySlug.values()].sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * The player-seasons a set of teams fielded, as `${playerId}-${year}` keys.
 * Membership is ground truth, not roster paperwork: a player was on the team
 * for a season if they have at least one stat line for it that season. A
 * mid-season mover therefore counts for both teams, which is the honest answer
 * to "show me this team's players".
 */
async function teamMemberSeasons(teamSlugs: string[]): Promise<Set<string>> {
  const wanted = new Set(teamSlugs);
  const allTeams = await db
    .select({ id: teams.id, name: teams.name })
    .from(teams);
  const ids = allTeams
    .filter((t) => wanted.has(teamSlug(t.name)))
    .map((t) => t.id);
  if (ids.length === 0) return new Set();
  const rows = await db
    .select({ playerId: gamePlayerStats.playerId, year: seasons.year })
    .from(gamePlayerStats)
    .innerJoin(games, eq(games.id, gamePlayerStats.gameId))
    .innerJoin(series, eq(series.id, games.seriesId))
    .innerJoin(events, eq(events.id, series.eventId))
    .innerJoin(seasons, eq(seasons.id, events.seasonId))
    .where(inArray(gamePlayerStats.teamId, ids))
    .groupBy(gamePlayerStats.playerId, seasons.year);
  return new Set(rows.map((r) => `${r.playerId}-${r.year}`));
}

/** A season the cohort picker can offer, with both the ways it gets named. */
export type ScopeSeason = {
  year: number;
  code: string; // title short name, e.g. "CW" — how a multi-pick reads
  name: string; // full title name — how a single pick reads
};

/**
 * Seasons and modes with rows for *any* of the chosen metrics — the union, so
 * the cohort pickers offer a season if at least one selected column covers it.
 * Columns that don't cover the picked cohort render as "—" in their cells.
 */
export async function getReportScope(
  metricRunId: number,
  metrics: string[],
): Promise<{
  years: number[];
  seasons: ScopeSeason[];
  modes: string[];
  allModes: boolean;
}> {
  if (metrics.length === 0) {
    return { years: [], seasons: [], modes: [], allModes: false };
  }
  const rows = await db
    .select({
      year: seasons.year,
      code: titles.shortName,
      name: titles.name,
      mode: gameModes.slug,
    })
    .from(playerMetricSeason)
    .innerJoin(seasons, eq(seasons.id, playerMetricSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .leftJoin(gameModes, eq(gameModes.id, playerMetricSeason.modeId))
    .where(
      and(
        eq(playerMetricSeason.runId, metricRunId),
        inArray(playerMetricSeason.metric, metrics),
      ),
    )
    .groupBy(seasons.year, titles.shortName, titles.name, gameModes.slug);
  // One entry per year: a year is one title in practice, and the picker's unit
  // of selection is the year, so the first title seen names it.
  const byYear = new Map<number, ScopeSeason>();
  for (const r of rows) {
    if (!byYear.has(r.year)) {
      byYear.set(r.year, { year: r.year, code: r.code, name: r.name });
    }
  }
  const seasonList = [...byYear.values()].sort((a, b) => a.year - b.year);
  const modes = [
    ...new Set(rows.map((r) => r.mode).filter((m): m is string => m !== null)),
  ].sort();
  // Any all-modes (mode_id NULL) row means "All modes combined" is a valid pick.
  const allModes = rows.some((r) => r.mode === null);
  return {
    years: seasonList.map((s) => s.year),
    seasons: seasonList,
    modes,
    allModes,
  };
}

/** `getReportScope`, for the team metric table. */
export async function getTeamReportScope(
  metricRunId: number,
  metrics: string[],
): Promise<{
  years: number[];
  seasons: ScopeSeason[];
  modes: string[];
  allModes: boolean;
}> {
  if (metrics.length === 0) {
    return { years: [], seasons: [], modes: [], allModes: false };
  }
  const rows = await db
    .select({
      year: seasons.year,
      code: titles.shortName,
      name: titles.name,
      mode: gameModes.slug,
    })
    .from(teamMetricSeason)
    .innerJoin(seasons, eq(seasons.id, teamMetricSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .leftJoin(gameModes, eq(gameModes.id, teamMetricSeason.modeId))
    .where(
      and(
        eq(teamMetricSeason.runId, metricRunId),
        inArray(teamMetricSeason.metric, metrics),
      ),
    )
    .groupBy(seasons.year, titles.shortName, titles.name, gameModes.slug);
  const byYear = new Map<number, ScopeSeason>();
  for (const r of rows) {
    if (!byYear.has(r.year)) {
      byYear.set(r.year, { year: r.year, code: r.code, name: r.name });
    }
  }
  const seasonList = [...byYear.values()].sort((a, b) => a.year - b.year);
  const modes = [
    ...new Set(rows.map((r) => r.mode).filter((m): m is string => m !== null)),
  ].sort();
  const allModes = rows.some((r) => r.mode === null);
  return {
    years: seasonList.map((s) => s.year),
    seasons: seasonList,
    modes,
    allModes,
  };
}

export type MetaEntry = {
  name: string;
  share: number;
  map_win_rate: number | null;
  n_player_maps: number;
};

export type MetaGroup = {
  season_id: number;
  title: string;
  mode: string;
  n_player_maps: number;
  entries: MetaEntry[];
};

export type MetaArtifact = {
  name: string;
  key: string;
  min_player_maps: number;
  groups: MetaGroup[];
};

const META_ARTIFACT_NAMES = [
  "meta_weapons",
  "meta_specialists",
  "meta_divisions",
  "meta_training",
  "meta_scorestreaks",
  "meta_rigs",
  "meta_payloads",
  "meta_traits",
] as const;

export async function getMetaArtifacts(
  metricRunId: number,
): Promise<MetaArtifact[]> {
  const rows = await db.execute(sql`
    SELECT name, payload FROM model_artifacts
    WHERE run_id = ${metricRunId} AND name = ANY(${sql.param(
      META_ARTIFACT_NAMES as unknown as string[],
    )})
  `);
  const list = rows as unknown as { name: string; payload: unknown }[];
  const byName = new Map(list.map((r) => [r.name, r.payload]));
  return META_ARTIFACT_NAMES.flatMap((name) => {
    const payload = byName.get(name) as Omit<MetaArtifact, "name"> | undefined;
    return payload ? [{ name, ...payload }] : [];
  });
}

// ---------- Rounds (kill-feed) ----------

export type ClutchByN = {
  n: number;
  attempts: number;
  wins: number;
  rate: number | null;
};

export type RoundsGroup = {
  year: number;
  title: string;
  mode: string;
  deaths: number;
  traded_share: number | null;
  ttfb: number[];
  advantage?: {
    adv_rounds: number;
    adv_conversion: number | null;
    disadv_rounds: number;
    disadv_steal: number | null;
  };
  clutch?: ClutchByN[];
};

export type RoundsOverview = {
  trade_window_ms: number;
  ttfb_edges_s: number[];
  groups: RoundsGroup[];
};

export async function getRoundsOverview(
  metricRunId: number,
): Promise<RoundsOverview | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${metricRunId} AND name = 'rounds_overview'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | RoundsOverview
    | undefined;
  return payload ?? null;
}

export type DensityMap = {
  title: string;
  map: string;
  n_kills: number;
  bins: number;
  peak: number;
  grid: number[][];
};

export type KillDensity = {
  bins: number;
  min_kills: number;
  maps: DensityMap[];
};

export async function getKillDensity(
  metricRunId: number,
): Promise<KillDensity | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${metricRunId} AND name = 'kill_density'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | KillDensity
    | undefined;
  return payload ?? null;
}

export type PlayerMetricValue = {
  metric: string;
  mode: string | null;
  year: number;
  title: string;
  value: number;
  denom: number;
  z: number | null;
  pctl: number | null;
  qualified: boolean;
};

export async function getPlayerMetrics(
  metricRunId: number,
  playerId: number,
): Promise<PlayerMetricValue[]> {
  return db
    .select({
      metric: playerMetricSeason.metric,
      mode: gameModes.slug,
      year: seasons.year,
      title: titles.shortName,
      value: playerMetricSeason.value,
      denom: playerMetricSeason.denom,
      z: playerMetricSeason.z,
      pctl: playerMetricSeason.pctl,
      qualified: playerMetricSeason.qualified,
    })
    .from(playerMetricSeason)
    .innerJoin(seasons, eq(seasons.id, playerMetricSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .leftJoin(gameModes, eq(gameModes.id, playerMetricSeason.modeId))
    .where(
      and(
        eq(playerMetricSeason.runId, metricRunId),
        eq(playerMetricSeason.playerId, playerId),
      ),
    );
}

// ---------- Rating version comparison ----------

export type RatingScore = {
  n: number;
  brier: number;
  log_loss: number;
  accuracy: number;
};

export type RatingComparisonCohort = {
  season_id: number;
  year: number;
  title: string;
  mode: string;
  n_maps: number;
  versions: Record<string, RatingScore>;
};

export type RatingComparison = {
  versions: string[];
  baseline: string;
  published: string;
  common_maps: number;
  maps_predicted: Record<string, number>;
  overall: Record<string, RatingScore>;
  delta_vs_baseline: Record<string, { brier: number; log_loss: number }>;
  by_cohort: RatingComparisonCohort[];
};

export async function getRatingComparison(
  ratingRunId: number,
): Promise<RatingComparison | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'rating_model_comparison'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | RatingComparison
    | undefined;
  return payload ?? null;
}

// ---------- Outcome leakage: what one column already knows ----------
//
// The map backtest's companion. For each feature, the accuracy of the crudest
// possible rule — the sign of the team differential — scored on the same maps
// the fitted model predicted. A column near the model's own accuracy is the
// win condition arriving as a feature. See docs/methodology.md.

export type SignBaselineFeature = {
  key: string;
  label: string;
  accuracy: number;
  n: number;
  direction: number; // +1, or -1 where the column points the other way (deaths)
  slaying: boolean;
};

export type SignBaselineCohort = {
  season_id: number;
  year: number;
  title: string;
  mode: string;
  n_maps: number;
  model_accuracy: number;
  features: SignBaselineFeature[];
  best_feature: SignBaselineFeature | null;
  model_gain: number | null;
};

export type SignBaseline = {
  version: string;
  rule: string;
  by_cohort: SignBaselineCohort[];
};

export async function getSignBaseline(
  ratingRunId: number,
): Promise<SignBaseline | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'feature_sign_baseline'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | SignBaseline
    | undefined;
  return payload ?? null;
}

// ---------- The shrinkage prior, estimated rather than assumed ----------
//
// Per (season × mode): the empirical-Bayes prior strength k in m/(m+k), from the
// ratio of within-player to between-player score variance in that cohort. The
// pipeline used to assert 15 everywhere; `vs_fallback` is how far each cohort
// actually sits from it. See docs/methodology.md.

export type ShrinkageCohort = {
  season_id: number;
  year: number;
  title: string;
  mode_id: number;
  mode: string;
  n_players: number;
  n_maps: number;
  within_var: number;
  between_var: number;
  shrink_maps: number;
  half_signal_maps: number;
  vs_fallback: number;
  estimated: boolean;
};

export type RatingShrinkage = {
  version: string;
  estimator: string;
  fallback: number;
  n_fell_back: number;
  median: number | null;
  min: number | null;
  max: number | null;
  cohorts: ShrinkageCohort[];
};

export async function getRatingShrinkage(
  ratingRunId: number,
): Promise<RatingShrinkage | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'rating_shrinkage'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | RatingShrinkage
    | undefined;
  return payload ?? null;
}

// ---------- The rating as a posterior ----------
//
// The two-level normal-normal fit behind the published rating: per (season ×
// mode), the spread of true player skill (tau), the per-map noise around it
// (sigma), and what the pair implies for pooling. `signal_share` is tau over the
// observed spread of season scores — how much of the leaderboard's range is real
// difference between players. See docs/methodology.md#the-rating-is-a-posterior.

export type PosteriorCohort = {
  season_id: number;
  year: number;
  title: string;
  mode_id: number;
  mode: string;
  n_players: number;
  n_maps: number;
  n_replicated: number;
  tau: number;
  sigma: number;
  implied_k: number;
  moment_k: number;
  signal_share: number | null;
  iterations: number;
  converged: boolean;
  collapsed: boolean;
  estimated: boolean;
  fallback: string | null;
  loglik: number | null;
  calibration: {
    n: number;
    ratio: number | null;
    applied: number;
  } | null;
};

export type RatingPosterior = {
  version: string;
  model: string;
  estimator: string;
  n_cohorts: number;
  n_fell_back: number;
  n_collapsed: number;
  median_k: number | null;
  cohorts: PosteriorCohort[];
  // How far the published leaderboard moved when the estimator changed, and how
  // the posterior interval compares with the bootstrap it replaced.
  vs_z_shrink:
    | { available: false; reason: string }
    | {
        available: true;
        n_player_seasons: number;
        n_qualified: number;
        mean_abs_delta: number;
        max_abs_delta: number;
        spearman: number | null;
        top10_unchanged: number;
        interval: {
          what: string;
          n: number;
          median: number | null;
          p25: number | null;
          p75: number | null;
        };
      };
  calibration:
    | { available: false; reason: string }
    | {
        available: true;
        what: string;
        bootstrap_b: number;
        n_cohorts: number;
        n_player_seasons: number;
        median: number;
        min: number;
        max: number;
      };
};

export async function getRatingPosterior(
  ratingRunId: number,
): Promise<RatingPosterior | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'rating_posterior'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | RatingPosterior
    | undefined;
  return payload ?? null;
}

// ---------- Out-of-sample: the two tests the rating can fail ----------

export type Interval = {
  lo: number | null;
  hi: number | null;
};

export type PersistenceCell = Interval & { n: number; r: number | null };

export type PersistenceContrast = Interval & {
  what: string;
  delta_r: number | null;
  excludes_zero: boolean;
};

export type RatingPersistence = {
  min_maps_each_side: number;
  bootstrap_b: number;
  n_pairs: number;
  transitions: {
    from_year: number;
    from_title: string;
    to_year: number;
    to_title: string;
    n: number;
  }[];
  // Keys are "rating->rating", "rating->kd", "kd->rating", "kd->kd".
  cells: Record<string, PersistenceCell>;
  // Keyed by target: "rating" and "kd".
  contrasts: Record<string, PersistenceContrast>;
};

export type ForecastScore = {
  n: number;
  brier: number;
  log_loss: number;
  accuracy: number;
};

export type BrierContrast = Interval & {
  what: string;
  delta: number;
  excludes_zero: boolean;
};

export type AccuracyInterval = Interval & {
  accuracy: number;
  beats_coin_flip: boolean;
};

export type RosterForecast = {
  version: string;
  min_train_maps: number;
  skipped_no_history: number;
  skipped_no_roster: number;
  per_season: {
    season_id: number;
    year: number;
    title: string;
    n_events: number;
    n_scored: number;
  }[];
  predictors: Record<string, ForecastScore>;
  coin_flip_brier: number;
  common:
    | { available: false; reason: string }
    | {
        available: true;
        n_maps: number;
        predictors: Record<string, ForecastScore>;
      };
  contrasts:
    | { available: false; reason: string }
    | {
        available: true;
        n_maps: number;
        bootstrap_b: number;
        brier: Record<string, BrierContrast>;
        accuracy: Record<string, AccuracyInterval>;
        // Every predictor against the 0.5 floor, with what this many maps
        // could have resolved. Optional: runs written before RAPM landed
        // have the rating-anchored block only.
        vs_coin_flip?: Record<string, BrierContrast & { mde80: number | null }>;
      };
};

export type RapmPlayer = {
  player_id: number;
  maps: number;
  coef: number;
  se: number;
  z: number | null;
  teammate_concentration: number;
};

export type Rapm =
  | { available: false; reason: string }
  | {
      available: true;
      l2: number;
      min_maps: number;
      n_maps: number;
      n_players: number;
      converged: boolean;
      leaders: RapmPlayer[];
      trailers: RapmPlayer[];
      n_resolved: number;
      n_concentrated: number;
      concentration_median: number;
      coef_sd: number;
      se_median: number;
      ridge_path: {
        l2: number;
        n_players: number;
        sd: number;
        max_abs: number;
        corr_with_l2_min?: number;
      }[];
      blend:
        | { available: false; reason: string }
        | {
            available: true;
            what: string;
            n_players: number;
            corr_with_plain: number;
            prior_sd: number;
          };
    };

export async function getRapm(ratingRunId: number): Promise<Rapm | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'rapm'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | Rapm
    | undefined;
  return payload ?? null;
}

// rapm.py refuses to read a coefficient as a player's own above this. Kept in
// step with rapm.artifact's `n_concentrated`, which counts on the same rule.
export const RAPM_CONCENTRATION_LIMIT = 0.9;

export type PlayerRapm = {
  coef: number;
  se: number;
  maps: number;
  concentration: number;
  // Does the 95% interval clear zero? False for 189 of the 196 players in the
  // current fit, so callers must render this, not infer it from the sign.
  resolved: boolean;
  // At or above the limit the coefficient is substantially the team's.
  entangled: boolean;
};

// One player's RAPM from the published rating run. Null when the player did not
// clear rapm.MIN_MAPS, which is the common case and not an error.
export async function getPlayerRapm(
  ratingRunId: number,
  playerId: number,
): Promise<PlayerRapm | null> {
  const rows = await db
    .select({
      coef: playerRapm.coef,
      se: playerRapm.se,
      maps: playerRapm.maps,
      concentration: playerRapm.teammateConcentration,
    })
    .from(playerRapm)
    .where(
      and(eq(playerRapm.runId, ratingRunId), eq(playerRapm.playerId, playerId)),
    )
    .limit(1);
  const r = rows[0];
  if (!r) return null;
  return {
    ...r,
    resolved: Math.abs(r.coef) > 1.96 * r.se,
    entangled: r.concentration >= RAPM_CONCENTRATION_LIMIT,
  };
}

export async function getRatingPersistence(
  ratingRunId: number,
): Promise<RatingPersistence | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'rating_persistence'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | RatingPersistence
    | undefined;
  return payload ?? null;
}

export async function getRosterForecast(
  ratingRunId: number,
): Promise<RosterForecast | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'roster_forecast'
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | RosterForecast
    | undefined;
  return payload ?? null;
}

// ---------- Map-level and per-mode team ratings ----------

export type MapArmScore = {
  n: number;
  brier: number;
  log_loss: number;
  accuracy: number;
};

// The same paired-gap shape `model_gaps` uses, keyed by map or by series
// depending on which artifact it came from.
export type MapPairedGaps = {
  available: boolean;
  n?: number;
  unit?: string;
  models?: Record<
    string,
    {
      brier: number;
      brier_lo: number;
      brier_hi: number;
      accuracy: number;
      accuracy_lo: number;
      accuracy_hi: number;
    }
  >;
  pairs?: {
    a: string;
    b: string;
    delta: number;
    lo: number;
    hi: number;
    excludes_zero: boolean;
    dm_p: number | null;
    mde80: number;
  }[];
};

export type MapModeRow = {
  mode: string;
  n_maps: number;
  arms: Record<string, MapArmScore>;
  gaps: MapPairedGaps;
};

export type MapElo = {
  dataThrough: string | null;
  mapBacktest: {
    n_maps: number;
    k: number;
    blend_k: number;
    published_arm: string;
    arms: Record<string, MapArmScore>;
    coin_flip_brier: number;
    gaps: MapPairedGaps;
    by_mode: MapModeRow[];
  };
  seriesRollup: {
    n_series: number;
    n_series_no_rotation: number;
    wins_needed: number;
    rotation: Record<string, string[]>;
    method: string;
    arms: Record<string, MapArmScore>;
    gaps: MapPairedGaps;
  };
  // The null on whether per-mode strength exists at all. Never rendered apart
  // from the mode table it judges.
  specialization: {
    available: boolean;
    reason?: string;
    n_cells?: number;
    min_mode_maps?: number;
    observed_sd?: number;
    null_mean_sd?: number;
    null_lo?: number;
    null_hi?: number;
    null_permutations?: number;
    p_value?: number;
    exceeds_null?: boolean;
    excess_sd?: number;
  };
  modeRatings: {
    min_mode_maps: number;
    rows: {
      team_id: number;
      team: string;
      mode: string;
      rating: number;
      global_rating: number;
      delta: number;
      n_maps: number;
    }[];
  } | null;
};

export async function getMapElo(): Promise<MapElo | null> {
  const run = await latestRun("map_elo");
  if (!run) return null;
  const rows = await db.execute(sql`
    SELECT name, payload FROM model_artifacts WHERE run_id = ${run.id}
  `);
  const byName = new Map(
    (rows as unknown as { name: string; payload: unknown }[]).map((r) => [
      r.name,
      r.payload,
    ]),
  );
  const mapBacktest = byName.get("map_backtest") as
    | MapElo["mapBacktest"]
    | undefined;
  const seriesRollup = byName.get("series_rollup") as
    | MapElo["seriesRollup"]
    | undefined;
  if (!mapBacktest || !seriesRollup) return null;
  return {
    dataThrough: run.dataThrough,
    mapBacktest,
    seriesRollup,
    specialization: (byName.get("mode_specialization") as
      | MapElo["specialization"]
      | undefined) ?? { available: false },
    modeRatings:
      (byName.get("mode_ratings") as MapElo["modeRatings"] | undefined) ?? null,
  };
}

// ---------- Round win probability (event tier) ----------

export type RoundWpCell = {
  own: number;
  opp: number;
  n: number;
  p: number;
  se: number;
};

export type RoundWpPair = {
  a: string;
  b: string;
  what: string;
  delta: number;
  lo: number;
  hi: number;
  excludes_zero: boolean;
  dm_t: number | null;
  dm_p: number | null;
  mde80: number;
};

export type RoundWinProb = {
  mode: string;
  scope: string;
  bomb_state: string;
  table: {
    n_rounds: number;
    n_states: number;
    cells: RoundWpCell[];
    parametric: {
      intercept: number;
      weights: Record<string, number>;
    };
  };
  backtest:
    | { available: false; reason: string }
    | {
        available: true;
        n_rounds: number;
        n_events_scored: number;
        models: { model: string; brier: number; brier_lo: number; brier_hi: number }[];
        pairs: RoundWpPair[];
        nested: RoundWpPair[];
        calibration: {
          lo: number;
          hi: number;
          n: number;
          mean_pred?: number;
          frac_won?: number;
        }[];
      };
};

export type RoundWpaReliabilityCell = {
  key: string;
  what: string;
  r: number;
  lo: number;
  hi: number;
  spearman_brown: number | null;
  excludes_zero: boolean;
};

export type RoundWpa = {
  n_rounds: number;
  n_players: number;
  min_rounds: number;
  leaders: {
    player_id: number;
    rounds: number;
    kills: number;
    wpa: number;
    wpa_per_round: number;
    kills_per_round: number;
  }[];
  reliability:
    | { available: false; reason: string; n_players?: number }
    | {
        available: true;
        n_players: number;
        min_rounds: number;
        split: string;
        cells: RoundWpaReliabilityCell[];
        corr_wpa_kills: number;
        r_detectable: number;
      };
};

export type RoundTimelineBin = {
  t_s: number;
  /** Rounds still undecided at this instant, of those with a usable span. */
  n_live: number;
  live_share: number;
  winner_alive: number | null;
  loser_alive: number | null;
  /** Mean table probability for the eventual winner, over every live round —
   *  not comparable to `leader_wins`, which conditions on being ahead. */
  p_winner: number | null;
  p_winner_se: number | null;
  /** Live rounds where the sides differ: the subset the next two describe. */
  leader_n: number;
  p_leader: number | null; // what the table says the side ahead is worth
  leader_wins: number | null; // what that side actually did
  leader_wins_se: number | null;
  n_deaths: number;
  traded_share: number | null;
  traded_se: number | null;
};

export type RoundTimeline = {
  bin_ms: number;
  max_ms: number;
  n_rounds: number;
  n_rounds_spanned: number;
  n_rounds_span_conflict: number;
  in_sample: string;
  bins: RoundTimelineBin[];
  leader_drift: {
    t_s: number;
    n: number;
    model: number;
    observed: number;
    gap: number;
    se: number;
    excludes_zero: boolean;
  }[];
  trade_latency: {
    window_ms: number;
    bin_ms: number;
    max_ms: number;
    n_deaths: number;
    n_answered: number;
    n_within_window: number;
    within_of_answered: number | null;
    median_ms: number | null;
    bins: { lo_s: number; hi_s: number; n: number; share: number; in_window: boolean }[];
    beyond: { n: number; share: number };
    never: { n: number; share: number };
  };
};

// Every artifact comes off the same run, so they are fetched together and a
// page can never render the table from one run beside the reliability test
// from another.
export async function getRoundWinProb(): Promise<{
  dataThrough: string | null;
  winProb: RoundWinProb;
  wpa: RoundWpa | null;
  timeline: RoundTimeline | null;
} | null> {
  const run = await latestRun("round_wp");
  if (!run) return null;
  const rows = await db.execute(sql`
    SELECT name, payload FROM model_artifacts
    WHERE run_id = ${run.id}
      AND name IN ('round_win_prob', 'round_wpa', 'round_timeline')
  `);
  const byName = new Map(
    (rows as unknown as { name: string; payload: unknown }[]).map((r) => [
      r.name,
      r.payload,
    ]),
  );
  const winProb = byName.get("round_win_prob") as RoundWinProb | undefined;
  if (!winProb) return null;
  return {
    dataThrough: run.dataThrough,
    winProb,
    wpa: (byName.get("round_wpa") as RoundWpa | undefined) ?? null,
    timeline: (byName.get("round_timeline") as RoundTimeline | undefined) ?? null,
  };
}

// ---------- Segment win probability ----------

// The same question round_wp asks inside a round, asked of a whole map for the
// era the kill feed never covered. Every cell carries the race baseline it is
// being judged against, because the finding here is the comparison and not
// either number alone.
export type SegmentCell = {
  own: number;
  opp: number;
  n: number;
  p: number;
  se: number;
  baseline: number;
};

export type SegmentBacktest =
  | { available: false; reason: string }
  | {
      available: true;
      kind: string;
      n_maps: number;
      n_events_scored: number;
      models: { model: string; brier: number; brier_lo: number; brier_hi: number }[];
      pairs: {
        a: string;
        b: string;
        delta: number;
        lo: number;
        hi: number;
        excludes_zero: boolean;
        detectable: number;
      }[];
      calibration: {
        lo: number;
        hi: number;
        n: number;
        mean_pred?: number;
        frac_won?: number;
      }[];
    };

export type SegmentMode = {
  table: {
    kind: string;
    n_maps: number;
    n_states: number;
    laplace: number;
    bucketed: boolean;
    cells: SegmentCell[];
  };
  backtest: SegmentBacktest;
  seasons: number[];
  win_types?: {
    kind: string;
    n_rounds: number;
    types: { win_type: string; n: number; share: number | null; mean_swing: number }[];
  };
  swing?: { kind: string; hills: { hill: number; n: number; mean_abs_swing: number }[] };
};

export type SegmentWinProb = {
  scope: string;
  holes: {
    seasons_absent: number[];
    hardpoint_2026: string;
    control_seasons: number[];
    rule: string;
  };
  anomaly_rules: {
    round: string;
    hill: string;
    map: string;
    maps_truncated: number;
    segments_dropped: number;
    maps_kept: Record<string, number>;
    maps_dropped: Record<string, Record<string, number>>;
  };
  by_mode: Record<string, SegmentMode>;
  two_era_snd: {
    modern: { n_maps: number; seasons: number[] };
    feed: {
      n_maps: number;
      seasons: number[];
      excluded_for_a_different_race: number;
      race_reached_by_season: Record<string, Record<string, number>>;
    };
    cells: {
      own: number;
      opp: number;
      modern_n: number;
      modern_p: number;
      feed_n: number;
      feed_p: number;
      delta: number;
      z: number | null;
    }[];
    largest_disagreement: {
      own: number;
      opp: number;
      modern_p: number;
      feed_p: number;
      delta: number;
      z: number | null;
    } | null;
  };
};

export type SegmentCompetitiveness = {
  definition: string;
  consumed_by: string;
  by_kind: Record<
    string,
    { n: number; mean: number; p10: number; median: number; p90: number }
  >;
  maps: { game_id: number; kind: string; year: number; weight: number }[];
};

// Both artifacts come off the same run, so a page can never show the table from
// one run beside the map weights from another.
export async function getSegmentWinProb(): Promise<{
  dataThrough: string | null;
  winProb: SegmentWinProb;
  competitiveness: SegmentCompetitiveness | null;
} | null> {
  const run = await latestRun("segment_wp");
  if (!run) return null;
  const rows = await db.execute(sql`
    SELECT name, payload FROM model_artifacts
    WHERE run_id = ${run.id}
      AND name IN ('segment_win_prob', 'segment_competitiveness')
  `);
  const byName = new Map(
    (rows as unknown as { name: string; payload: unknown }[]).map((r) => [
      r.name,
      r.payload,
    ]),
  );
  const winProb = byName.get("segment_win_prob") as SegmentWinProb | undefined;
  if (!winProb) return null;
  return {
    dataThrough: run.dataThrough,
    winProb,
    competitiveness:
      (byName.get("segment_competitiveness") as SegmentCompetitiveness | undefined) ??
      null,
  };
}

// ---------- Career value and aging ----------

export type CareerValue = {
  available: boolean;
  team_share: number;
  qualified_maps: number;
  replacement: string;
  populations: {
    composite_player_seasons: number;
    plus_minus_player_seasons: number;
    seasons: number;
    split_player_seasons: number;
  };
  rows_by_key: Record<string, number>;
  credit_rules: {
    n_players: number;
    rank_correlation: number | null;
    top_ten_overlap: number | null;
    largest_moves: {
      player_id: number;
      rank_deviation: number;
      rank_with_team: number;
    }[];
  };
  separation: Record<
    string,
    {
      n: number;
      n_with_interval: number;
      n_clear_of_zero: number;
      share_clear: number | null;
    }
  >;
  statement: string;
};

export type AgingFit = {
  peak: number | null;
  peak_lo: number | null;
  peak_hi: number | null;
  n_observations: number;
  n_players: number;
  curve: { x: number; y: number }[];
};

export type Aging = {
  available: boolean;
  min_age_support: number;
  players_with_birthdate: number;
  seasons: number;
  populations: Record<
    string,
    {
      n_observations: number;
      n_players: number;
      age_window: [number, number] | null;
      fits: Record<string, AgingFit>;
      peak_interval: {
        lo: number | null;
        hi: number | null;
        point_estimates: number[];
        spread: number | null;
        fits_locating_a_peak: number;
      };
    }
  >;
  two_component: {
    available: boolean;
    reason?: string;
    intervals_overlap?: boolean;
    separated?: boolean;
  };
  statement: string;
};

export function latestCareerRun(): Promise<ModelRun | null> {
  return latestRun("career_value");
}

export function latestAgingRun(): Promise<ModelRun | null> {
  return latestRun("aging");
}

export function getCareerValue(runId: number): Promise<CareerValue | null> {
  return artifactPayload<CareerValue>(runId, "career_value");
}

export function getAging(runId: number): Promise<Aging | null> {
  return artifactPayload<Aging>(runId, "aging");
}

// One player's career totals, every way of counting them. The page shows the
// composite total first and the two plus-minus credit columns beside it,
// because the credit rule is a choice and the reader should see both.
export type PlayerCareerRow = {
  axis: string;
  credit: string;
  eraScope: string;
  seasons: number;
  maps: number;
  total: number;
  totalSd: number | null;
  peak: number;
  peakSeasonYear: number | null;
  bestThree: number | null;
  bestThreeStartYear: number | null;
};

export async function getPlayerCareer(
  runId: number,
  playerId: number,
): Promise<PlayerCareerRow[]> {
  const rows = await db.execute(sql`
    SELECT c.axis, c.credit, c.era_scope, c.seasons, c.maps, c.total,
           c.total_sd, c.peak, ps.year AS peak_year, c.best_three,
           bs.year AS best_three_year
    FROM player_career c
    LEFT JOIN seasons ps ON ps.id = c.peak_season_id
    LEFT JOIN seasons bs ON bs.id = c.best_three_start_season_id
    WHERE c.run_id = ${runId} AND c.player_id = ${playerId}
    ORDER BY c.axis, c.credit, c.era_scope
  `);
  return (
    rows as unknown as {
      axis: string;
      credit: string;
      era_scope: string;
      seasons: number;
      maps: number;
      total: number;
      total_sd: number | null;
      peak: number;
      peak_year: number | null;
      best_three: number | null;
      best_three_year: number | null;
    }[]
  ).map((r) => ({
    axis: r.axis,
    credit: r.credit,
    eraScope: r.era_scope,
    seasons: Number(r.seasons),
    maps: Number(r.maps),
    total: Number(r.total),
    totalSd: r.total_sd === null ? null : Number(r.total_sd),
    peak: Number(r.peak),
    peakSeasonYear: r.peak_year === null ? null : Number(r.peak_year),
    bestThree: r.best_three === null ? null : Number(r.best_three),
    bestThreeStartYear:
      r.best_three_year === null ? null : Number(r.best_three_year),
  }));
}

// ---------- Career rank ----------
//
// A second, independent all-time axis: peak/best-three/total over the
// gold-tier metric basket (plus award credit) instead of over VALUE or
// SKILL, published by `career_rank.engine`. See
// docs/methodology.md#career-rank for what it is measuring and why it
// disagrees with `player_career` where it does.

export function latestCareerRankRun(): Promise<ModelRun | null> {
  return latestRun("career_rank");
}

// The whole engine run, for the methodology page — the aggregate counts and
// the top-ten-by-* lists it publishes, not a per-player row.
export type CareerRankArtifact = {
  n_players_scored: number;
  basket_size: number;
  restricted: boolean;
  publish_from_year: number;
  shrinkage: {
    k: number;
    rule: string;
    refit: { k: number; n: number; fitted: number };
  };
  era_season_scores: {
    era: string;
    seasons: number;
    median_metrics: number;
    median_maps: number;
    sd_before: number;
    sd_after: number;
  }[];
  career: {
    min_seasons_floor: number;
    season_component_weights: Record<string, number>;
    n_players: number;
    n_qualified: number;
    n_below_floor: number;
    n_partial_coverage: number;
    seasons_uncovered: number;
    top_ten_by_total: { player_id: number; total: number; total_sd: number | null }[];
  };
};

export function getCareerRankArtifact(
  runId: number,
): Promise<CareerRankArtifact | null> {
  return artifactPayload<CareerRankArtifact>(runId, "career_rank");
}

export type CareerRankLeaderboardRow = {
  playerId: number;
  handle: string;
  nSeasons: number;
  /** Seasons the board could score. Below nSeasons when the box-score archive
   *  does not reach every season the career has. */
  seasonsCovered: number;
  /** Earliest year a box score reaches this player, null when none does. */
  coverageFromYear: number | null;
  total: number;
  totalSd: number | null;
  peak: number;
  peakSeasonYear: number | null;
  bestThree: number | null;
  bestThreeStartYear: number | null;
};

export async function getCareerRankLeaderboard(
  runId: number,
  limit: number,
): Promise<CareerRankLeaderboardRow[]> {
  const rows = await db.execute(sql`
    SELECT c.player_id, p.handle, c.n_seasons, c.seasons_covered,
           c.coverage_from_year, c.total, c.total_sd, c.peak,
           ps.year AS peak_year, c.best_three, bs.year AS best_three_year
    FROM player_career_rank c
    JOIN players p ON p.id = c.player_id
    LEFT JOIN seasons ps ON ps.id = c.peak_season_id
    LEFT JOIN seasons bs ON bs.id = c.best_three_start_season_id
    WHERE c.run_id = ${runId} AND c.qualified
    ORDER BY c.total DESC
    LIMIT ${limit}
  `);
  return (
    rows as unknown as {
      player_id: number;
      handle: string;
      n_seasons: number;
      seasons_covered: number | null;
      coverage_from_year: number | null;
      total: number;
      total_sd: number | null;
      peak: number;
      peak_year: number | null;
      best_three: number | null;
      best_three_year: number | null;
    }[]
  ).map((r) => ({
    playerId: r.player_id,
    handle: r.handle,
    nSeasons: Number(r.n_seasons),
    seasonsCovered:
      r.seasons_covered === null ? Number(r.n_seasons) : Number(r.seasons_covered),
    coverageFromYear:
      r.coverage_from_year === null ? null : Number(r.coverage_from_year),
    total: Number(r.total),
    totalSd: r.total_sd === null ? null : Number(r.total_sd),
    peak: Number(r.peak),
    peakSeasonYear: r.peak_year === null ? null : Number(r.peak_year),
    bestThree: r.best_three === null ? null : Number(r.best_three),
    bestThreeStartYear:
      r.best_three_year === null ? null : Number(r.best_three_year),
  }));
}

export type PlayerCareerRankSummary = {
  qualified: boolean;
  nSeasons: number;
  seasonsCovered: number;
  coverageFromYear: number | null;
  total: number;
  totalSd: number | null;
  peak: number;
  peakSeasonYear: number | null;
  bestThree: number | null;
  bestThreeStartYear: number | null;
};

export type PlayerCareerRankSeason = {
  seasonId: number;
  year: number;
  league: string;
  /** Null when the season carries no box score. Not a zero: the season was
   *  not measured, and the row says which components it did carry. */
  score: number | null;
  sd: number | null;
  componentsPresent: string[];
  netOfTeammates: number | null;
  opponentStrength: number | null;
};

export async function getPlayerCareerRank(
  runId: number,
  playerId: number,
): Promise<{
  summary: PlayerCareerRankSummary | null;
  seasons: PlayerCareerRankSeason[];
}> {
  const [summaryRows, seasonRows] = await Promise.all([
    db.execute(sql`
      SELECT c.qualified, c.n_seasons, c.seasons_covered, c.coverage_from_year,
             c.total, c.total_sd, c.peak,
             ps.year AS peak_year, c.best_three, bs.year AS best_three_year
      FROM player_career_rank c
      LEFT JOIN seasons ps ON ps.id = c.peak_season_id
      LEFT JOIN seasons bs ON bs.id = c.best_three_start_season_id
      WHERE c.run_id = ${runId} AND c.player_id = ${playerId}
    `),
    db.execute(sql`
      SELECT r.season_id, s.year, s.league, r.score, r.sd,
             coalesce(r.components_present, '{}') AS components_present,
             r.net_of_teammates, r.opponent_strength
      FROM player_season_rank r
      JOIN seasons s ON s.id = r.season_id
      WHERE r.run_id = ${runId} AND r.player_id = ${playerId}
      ORDER BY s.year
    `),
  ]);
  const s = (
    summaryRows as unknown as {
      qualified: boolean;
      n_seasons: number;
      seasons_covered: number | null;
      coverage_from_year: number | null;
      total: number;
      total_sd: number | null;
      peak: number;
      peak_year: number | null;
      best_three: number | null;
      best_three_year: number | null;
    }[]
  )[0];
  return {
    summary: s
      ? {
          qualified: s.qualified,
          nSeasons: Number(s.n_seasons),
          seasonsCovered:
            s.seasons_covered === null
              ? Number(s.n_seasons)
              : Number(s.seasons_covered),
          coverageFromYear:
            s.coverage_from_year === null ? null : Number(s.coverage_from_year),
          total: Number(s.total),
          totalSd: s.total_sd === null ? null : Number(s.total_sd),
          peak: Number(s.peak),
          peakSeasonYear: s.peak_year === null ? null : Number(s.peak_year),
          bestThree: s.best_three === null ? null : Number(s.best_three),
          bestThreeStartYear:
            s.best_three_year === null ? null : Number(s.best_three_year),
        }
      : null,
    seasons: (
      seasonRows as unknown as {
        season_id: number;
        year: number;
        league: string;
        score: number | null;
        sd: number | null;
        components_present: string[] | null;
        net_of_teammates: number | null;
        opponent_strength: number | null;
      }[]
    ).map((r) => ({
      seasonId: r.season_id,
      year: Number(r.year),
      league: r.league,
      score: r.score === null ? null : Number(r.score),
      sd: r.sd === null ? null : Number(r.sd),
      componentsPresent: r.components_present ?? [],
      netOfTeammates:
        r.net_of_teammates === null ? null : Number(r.net_of_teammates),
      opponentStrength:
        r.opponent_strength === null ? null : Number(r.opponent_strength),
    })),
  };
}

/** The seasons the career-rank engine published a score for.
 *
 *  A season that scored nothing is not in here, so the page can name the
 *  seasons that carry no score without knowing the engine's own rules. */
export async function getCareerRankSeasons(runId: number): Promise<number[]> {
  const rows = await db.execute(sql`
    SELECT DISTINCT s.year
    FROM player_season_rank r
    JOIN seasons s ON s.id = r.season_id
    WHERE r.run_id = ${runId}
    ORDER BY s.year
  `);
  return (rows as unknown as { year: number }[]).map((r) => Number(r.year));
}

// ---------- Series dynamics ----------

// Every observed rate carries two benchmarks: `rating` is independence at the
// frozen ratings, `quality` is independence once the ratings are allowed to
// have understated how far apart the teams were. Reading the first without the
// second is how a race to three gets mistaken for momentum.
export type SeriesGap = {
  expected: number;
  delta: number;
  lo: number;
  hi: number;
  excludes_zero: boolean;
  mde80: number | null;
};

export type SeriesRate = {
  event: string;
  n_series: number;
  observed: number;
  observed_lo: number;
  observed_hi: number;
  vs: Record<string, SeriesGap>;
};

export type SeriesEraCell = {
  era: string;
  n_series: number;
  qualified: boolean;
} & Record<
  string,
  | string
  | number
  | boolean
  | {
      observed: number;
      observed_lo: number;
      observed_hi: number;
      expected: Record<string, number>;
    }
>;

export type SeriesDynamics = {
  scope: string;
  benchmarks: Record<string, string>;
  n_series: number;
  n_series_loaded: number;
  dropped: Record<string, number>;
  bootstrap_b: number;
  min_era_series: number;
  map1: {
    observed: number;
    coin_flip: number;
    vs: Record<string, SeriesGap>;
    note: string;
  };
  rates: SeriesRate[];
  by_era: SeriesEraCell[];
  strength_check:
    | { available: false; reason: string }
    | {
        available: true;
        n_maps: number;
        mean_predicted: number;
        observed: number;
        brier: number;
        calibration_slope: number;
      };
};

export type SeriesTerm = {
  term: string;
  beta: number;
  lo: number;
  hi: number;
  excludes_zero: boolean;
  se: number;
  z: number | null;
  p: number | null;
  mde80: number | null;
  swing_pp?: number;
  mde80_swing_pp?: number;
};

export type SeriesFit =
  | { available: false; reason: string; n_maps: number }
  | {
      available: true;
      rows: string;
      n_maps: number;
      n_series: number;
      cluster: string;
      specs: { spec: string; intercept: number; terms: SeriesTerm[] }[];
    };

export type SeriesQualityArm = {
  arm: string;
  n_series: number;
  n_maps: number;
  gamma: number;
  gamma_lo: number;
  gamma_hi: number;
  excludes_zero: boolean;
  p: number;
  swing_pp: number;
  swing_pp_lo: number;
  swing_pp_hi: number;
};

export type SeriesMomentum = {
  question: string;
  coding: string;
  map2: SeriesFit;
  consecutive: SeriesFit;
  quality: {
    available: true;
    n_series: number;
    n_maps: number;
    null: { a: number; sigma: number; loglik: number };
    full: {
      a: number;
      sigma: number;
      sigma_lo: number;
      sigma_hi: number;
      gamma: number;
      gamma_lo: number;
      gamma_hi: number;
      loglik: number;
    };
    excludes_zero: boolean;
    lr_stat: number;
    p: number;
    swing_pp: number;
    swing_pp_lo: number;
    swing_pp_hi: number;
    mde80: number | null;
    mde80_swing_pp: number | null;
    sigma_swing_pp: number;
    first_three: SeriesQualityArm;
    interpretation: string;
  };
};

// Both artifacts come off one run, so a page can never render the rates from
// one fit beside the null from another.
export async function getSeriesDynamics(): Promise<{
  dataThrough: string | null;
  dynamics: SeriesDynamics;
  momentum: SeriesMomentum | null;
} | null> {
  const run = await latestRun("series_dynamics");
  if (!run) return null;
  const rows = await db.execute(sql`
    SELECT name, payload FROM model_artifacts
    WHERE run_id = ${run.id} AND name IN ('series_dynamics', 'series_momentum')
  `);
  const byName = new Map(
    (rows as unknown as { name: string; payload: unknown }[]).map((r) => [
      r.name,
      r.payload,
    ]),
  );
  const dynamics = byName.get("series_dynamics") as SeriesDynamics | undefined;
  if (!dynamics) return null;
  return {
    dataThrough: run.dataThrough,
    dynamics,
    momentum: (byName.get("series_momentum") as SeriesMomentum | undefined) ?? null,
  };
}

export type TeamMetricValue = {
  metric: string;
  mode: string | null;
  year: number;
  value: number;
  denom: number;
  z: number | null;
  pctl: number | null;
  qualified: boolean;
};

export async function getTeamMetrics(
  metricRunId: number,
  teamId: number,
): Promise<TeamMetricValue[]> {
  return db
    .select({
      metric: teamMetricSeason.metric,
      mode: gameModes.slug,
      year: seasons.year,
      value: teamMetricSeason.value,
      denom: teamMetricSeason.denom,
      z: teamMetricSeason.z,
      pctl: teamMetricSeason.pctl,
      qualified: teamMetricSeason.qualified,
    })
    .from(teamMetricSeason)
    .innerJoin(seasons, eq(seasons.id, teamMetricSeason.seasonId))
    .leftJoin(gameModes, eq(gameModes.id, teamMetricSeason.modeId))
    .where(
      and(
        eq(teamMetricSeason.runId, metricRunId),
        eq(teamMetricSeason.teamId, teamId),
      ),
    );
}

// ---------- Player style axes (and the archetypes that aren't there) ----------

export type StyleAxis = {
  index: number;
  name: string;
  share: number;
  loadings: { column: string; loading: number }[];
};

export type StyleNullBand = { lo: number; hi: number; mean: number };

export type StyleKResult = {
  k: number;
  gap: number;
  s_k: number;
  silhouette: number | null;
  stability: number[];
  silhouette_null: StyleNullBand | null;
  stability_null: StyleNullBand | null;
  beats_null: boolean;
};

export type StyleBasis = {
  basis: string;
  league: string;
  era: string;
  years: number[];
  n: number;
  n_columns: number;
  columns: string[];
  coverage: { season_id: number; eligible: number; kept: number; share: number }[];
  quality_share: number;
  n_components: number;
  component_share: number[];
  eigenvalues: number[];
  null95: number[];
  axes: StyleAxis[];
  clustering: StyleKResult[];
  gap_k: number;
  surviving_k: number;
  taxonomy: boolean;
};

// The metric layer's columns change wholesale where the two archives meet, so
// style is fitted once per era and the axes never travel across the seam.
export type StyleEra = {
  league: string;
  era: string;
  years: number[];
  published_basis: string;
  n_subjects_total: number;
  ineligible_columns: { column: string; coverage: Record<string, number> }[];
};

export type PlayerStyle = {
  min_season_coverage: number;
  published_bases: string[];
  eras: StyleEra[];
  bases: StyleBasis[];
};

export async function getPlayerStyleArtifact(): Promise<{
  dataThrough: string | null;
  runId: number;
  style: PlayerStyle;
} | null> {
  const run = await latestRun("player_style");
  if (!run) return null;
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${run.id} AND name = 'player_style'
  `);
  const payload = (rows as unknown as { payload: PlayerStyle }[])[0]?.payload;
  if (!payload) return null;
  return { dataThrough: run.dataThrough, runId: run.id, style: payload };
}

export type PlayerStylePoint = {
  year: number;
  title: string;
  axis: number;
  score: number;
  pctl: number;
};

export async function getPlayerStyle(
  styleRunId: number,
  playerId: number,
): Promise<PlayerStylePoint[]> {
  return db
    .select({
      year: seasons.year,
      title: titles.shortName,
      axis: playerStyleSeason.axis,
      score: playerStyleSeason.score,
      pctl: playerStyleSeason.pctl,
    })
    .from(playerStyleSeason)
    .innerJoin(seasons, eq(seasons.id, playerStyleSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .where(
      and(
        eq(playerStyleSeason.runId, styleRunId),
        eq(playerStyleSeason.playerId, playerId),
      ),
    )
    .orderBy(seasons.year, playerStyleSeason.axis);
}

// ---------- Role: the opening engagement ----------

export type RoleSeason = {
  year: number;
  title: string;
  maps: number;
  contactRate: number;
  contactWinRate: number;
  contactPctl: number;
  kdRaw: number | null;
  kdAdjustment: number | null;
  kdAdjusted: number | null;
};

export type RoleModel = {
  statement: string;
  mode: string;
  nContactSeasons: number;
  eraSplit: { recoveryEra: string; costEra: string; why: string };
  entryCost: {
    outcome: string;
    slope: number;
    lo95: number;
    hi95: number;
    nSeasons: number;
    nPlayers: number;
    separates: boolean;
  }[];
  recovery: {
    accuracy: number;
    baseRate: number;
    nSeasons: number;
    nPlayers: number;
    nAxes: number;
    verdict: string;
    rule: { carriesAt: number; ambiguousAt: number };
    aucByAxis: Record<string, number>;
  } | null;
  weaponTable: {
    nWeapons: number;
    verifiedAgainstFeed: string[];
    observed: Record<string, string>;
    disagreements: string[];
  };
  adjustmentAudit: {
    player: string;
    year: number;
    raw: number;
    adjustment: number;
    adjusted: number;
  }[];
};

type RolePayload = {
  statement: string;
  mode: string;
  n_contact_seasons: number;
  era_split: { recovery_era: string; cost_era: string; why: string };
  entry_cost: {
    outcome: string;
    slope: number;
    lo95: number;
    hi95: number;
    n_seasons: number;
    n_players: number;
    separates: boolean;
  }[];
  recovery: {
    accuracy: number;
    base_rate: number;
    n_seasons: number;
    n_players: number;
    n_axes: number;
    verdict: string;
    rule: { carries_at: number; ambiguous_at: number };
    auc_by_axis: Record<string, number>;
  } | null;
  weapon_table: {
    n_weapons: number;
    verified_against_feed: string[];
    observed: Record<string, string>;
    disagreements: string[];
  };
  adjustment_audit: {
    player: string;
    year: number;
    raw: number;
    adjustment: number;
    adjusted: number;
  }[];
};

export async function getRole(): Promise<
  { runId: number; dataThrough: string | null; role: RoleModel } | null
> {
  const run = await latestRun("role");
  if (!run) return null;
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${run.id} AND name = 'role'
  `);
  const p = (rows as unknown as { payload: RolePayload }[])[0]?.payload;
  if (!p) return null;
  return {
    runId: run.id,
    dataThrough: run.dataThrough,
    role: {
      statement: p.statement,
      mode: p.mode,
      nContactSeasons: p.n_contact_seasons,
      eraSplit: {
        recoveryEra: p.era_split.recovery_era,
        costEra: p.era_split.cost_era,
        why: p.era_split.why,
      },
      entryCost: p.entry_cost.map((c) => ({
        outcome: c.outcome,
        slope: c.slope,
        lo95: c.lo95,
        hi95: c.hi95,
        nSeasons: c.n_seasons,
        nPlayers: c.n_players,
        separates: c.separates,
      })),
      recovery: p.recovery
        ? {
            accuracy: p.recovery.accuracy,
            baseRate: p.recovery.base_rate,
            nSeasons: p.recovery.n_seasons,
            nPlayers: p.recovery.n_players,
            nAxes: p.recovery.n_axes,
            verdict: p.recovery.verdict,
            rule: {
              carriesAt: p.recovery.rule.carries_at,
              ambiguousAt: p.recovery.rule.ambiguous_at,
            },
            aucByAxis: p.recovery.auc_by_axis,
          }
        : null,
      weaponTable: {
        nWeapons: p.weapon_table.n_weapons,
        verifiedAgainstFeed: p.weapon_table.verified_against_feed,
        observed: p.weapon_table.observed,
        disagreements: p.weapon_table.disagreements,
      },
      adjustmentAudit: p.adjustment_audit,
    },
  };
}

/** One player's position at the opening engagement, season by season. */
export async function getPlayerRole(
  roleRunId: number,
  playerId: number,
): Promise<RoleSeason[]> {
  return db
    .select({
      year: seasons.year,
      title: titles.shortName,
      maps: playerRoleSeason.maps,
      contactRate: playerRoleSeason.contactRate,
      contactWinRate: playerRoleSeason.contactWinRate,
      contactPctl: playerRoleSeason.contactPctl,
      kdRaw: playerRoleSeason.kdRaw,
      kdAdjustment: playerRoleSeason.kdAdjustment,
      kdAdjusted: playerRoleSeason.kdAdjusted,
    })
    .from(playerRoleSeason)
    .innerJoin(seasons, eq(seasons.id, playerRoleSeason.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .where(
      and(
        eq(playerRoleSeason.runId, roleRunId),
        eq(playerRoleSeason.playerId, playerId),
      ),
    )
    .orderBy(seasons.year);
}

/** The seasons the role fit published, which is where the kill feed exists. */
export async function getRoleSeasons(roleRunId: number): Promise<number[]> {
  const rows = await db.execute(sql`
    SELECT DISTINCT s.year
    FROM player_role_season r
    JOIN seasons s ON s.id = r.season_id
    WHERE r.run_id = ${roleRunId}
    ORDER BY s.year
  `);
  return (rows as unknown as { year: number }[]).map((r) => r.year);
}

// ---------- Map meta: what the map pool was, season by season ----------

export type MapRow = {
  map: string;
  games: number;
  share: number;
  // Hardpoint/Control carry a running score, so the average winning and losing
  // score describe how close the map plays. Search & Destroy is a race to 6,
  // where the losing score alone carries that.
  avgWinScore: number | null;
  avgLoseScore: number | null;
  sweepShare: number | null;
  decidersShare: number;
};

export type MapModeGroup = {
  mode: string;
  games: number;
  maps: MapRow[];
};

export type MapSeason = {
  year: number;
  title: string;
  league: string;
  games: number;
  modes: MapModeGroup[];
};

/**
 * The map pool per season and mode, counted off decided games.
 *
 * Maps are named by the source that recorded the game, so a map renamed
 * between titles appears under each name it was played as — the season is the
 * unit here, not the map's identity across a decade.
 */
export async function getMapMeta(): Promise<MapSeason[]> {
  const rows = (await db.execute(sql`
    WITH decided AS (
      SELECT se.year, t.short_name AS title, se.league,
             gm.slug AS mode, m.name AS map,
             greatest(g.team1_score, g.team2_score) AS win_score,
             least(g.team1_score, g.team2_score) AS lose_score,
             (s.team1_score + s.team2_score) AS series_maps,
             g.ordinal
      FROM games g
      JOIN maps m       ON m.id = g.map_id
      JOIN game_modes gm ON gm.id = g.mode_id
      JOIN series s     ON s.id = g.series_id
      JOIN events e     ON e.id = s.event_id
      JOIN seasons se   ON se.id = e.season_id
      JOIN titles t     ON t.id = se.title_id
      WHERE g.winner_team_id IS NOT NULL
    )
    SELECT year, title, league, mode, map,
           count(*)::int AS games,
           avg(win_score) FILTER (WHERE win_score IS NOT NULL) AS avg_win,
           avg(lose_score) FILTER (WHERE lose_score IS NOT NULL) AS avg_lose,
           -- A decider is the last map of a series that went the distance.
           (count(*) FILTER (WHERE ordinal = series_maps AND series_maps >= 5))::float
             / count(*) AS deciders_share
    FROM decided
    GROUP BY year, title, league, mode, map
    ORDER BY year, mode, games DESC
  `)) as unknown as {
    year: number;
    title: string;
    league: string;
    mode: string;
    map: string;
    games: number;
    avg_win: string | number | null;
    avg_lose: string | number | null;
    deciders_share: string | number;
  }[];

  const num = (v: string | number | null): number | null =>
    v === null ? null : typeof v === "number" ? v : Number(v);

  const seasonsOut = new Map<number, MapSeason>();
  for (const r of rows) {
    let season = seasonsOut.get(r.year);
    if (!season) {
      season = { year: r.year, title: r.title, league: r.league, games: 0, modes: [] };
      seasonsOut.set(r.year, season);
    }
    let mode = season.modes.find((m) => m.mode === r.mode);
    if (!mode) {
      mode = { mode: r.mode, games: 0, maps: [] };
      season.modes.push(mode);
    }
    mode.maps.push({
      map: r.map,
      games: r.games,
      share: 0,
      avgWinScore: num(r.avg_win),
      avgLoseScore: num(r.avg_lose),
      sweepShare: null,
      decidersShare: num(r.deciders_share) ?? 0,
    });
    mode.games += r.games;
    season.games += r.games;
  }
  const out = [...seasonsOut.values()].sort((a, b) => b.year - a.year);
  for (const season of out) {
    for (const mode of season.modes) {
      for (const m of mode.maps) m.share = mode.games > 0 ? m.games / mode.games : 0;
    }
    season.modes.sort((a, b) => b.games - a.games);
  }
  return out;
}

// ---------- The phases fitted after the career rating ----------
//
// Season plus-minus, the opponent-adjustment ladder, the openskill baseline,
// SKILL and the pinned evaluation harness each open their own model run. None
// of them share the rating run, so every reader below takes the run its own
// model wrote, and a page that resolves the rating run alone reads nothing
// here. The evaluation population is the exception: it is written onto the
// metric-diff run, because that is the run that freezes it.

async function artifactPayload<T>(
  runId: number,
  name: string,
): Promise<T | null> {
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${runId} AND name = ${name}
  `);
  const payload = (rows as unknown as { payload: unknown }[])[0]?.payload as
    | T
    | undefined;
  return payload ?? null;
}

export function latestSeasonRapmRun(): Promise<ModelRun | null> {
  return latestRun("rapm_season");
}

export function latestOpponentAdjustRun(): Promise<ModelRun | null> {
  return latestRun("opponent_adjust");
}

export function latestOpenskillRun(): Promise<ModelRun | null> {
  return latestRun("openskill");
}

export function latestSkillRun(): Promise<ModelRun | null> {
  return latestRun("skill_prior");
}

export function latestEvaluationRun(): Promise<ModelRun | null> {
  return latestRun("evaluation");
}

// ---------- Season plus-minus ----------

export type SeasonRapmCell = {
  cell: string;
  resolution: string;
  player_columns: number;
  pooled_players: number;
  coef_sd: number;
  se_median: number;
  replacement_coef: number;
  replacement_se: number;
  penalty_dominated_share: number;
};

export type SeasonRapmGraph = {
  cell: string;
  maps: number;
  resolution: string;
  lineup_pools: number;
  rank_ceiling: number;
  distinct_lineups: number;
  teammate_graph: {
    nodes: number;
    edges: number;
    bridges: number;
    components: number;
    isolated_nodes: number;
    algebraic_connectivity: number;
    largest_component_share: number;
  };
};

export type SeasonRapm = {
  available: boolean;
  what: string;
  how_to_read: string;
  n_maps: number;
  // resolution is 'era' where one cell covers three seasons at once and
  // 'season' where it does not. The CWL years reach only the first, which is
  // why those seasons carry one repeated coefficient.
  by_cell: SeasonRapmCell[];
  graphs: SeasonRapmGraph[];
  scopes: {
    rule: string;
    stored: string[];
    career_retained_in: string;
    smoothed_vs_filtered_r: number;
  };
  admission: { rule: string; min_maps: number; pooled_players: number };
  publication: {
    rows: number;
    min_maps: number;
    player_cells_published: number;
  };
  columns: {
    total: number;
    player_cells: number;
    team_seasons: number;
    replacement_buckets: number;
  };
  penalties: {
    sigma2: number;
    lambda0: number;
    lambda_walk: number;
    effective_df: number;
    converged: boolean;
    chosen_by: string;
  };
  response: {
    published: string;
    dropped_maps: number;
    dropped_reason: string;
    sensitivity: {
      response: string;
      n_maps: number;
      coef_sd: number;
      rank_corr_with_published_response: number;
    }[];
  };
  reliability: {
    available: boolean;
    what: string;
    unit: string;
    r: number;
    lo: number;
    hi: number;
    spearman_brown: number;
    bootstrap_b: number;
    n_player_cells: number;
    by_cell: {
      cell: string;
      maps: number;
      n_players: number;
      r: number;
      spearman_brown: number;
    }[];
  };
};

export function getSeasonRapm(runId: number): Promise<SeasonRapm | null> {
  return artifactPayload<SeasonRapm>(runId, "rapm_season");
}

// Only 'filtered' may be read forward: the smoothed family's penalty is
// two-sided and has already seen the season after the one it reports.
export const FORWARD_RAPM_SCOPE = "filtered";

export type PlayerSeasonRapm = {
  seasonId: number;
  year: number;
  title: string;
  resolution: string;
  maps: number;
  coef: number;
  se: number;
  concentration: number;
  penaltyShare: number | null;
  resolved: boolean;
  entangled: boolean;
};

// One player's season coefficients from the season run. Empty where the player
// never cleared the publication floor, which is the common case. In the CWL
// years the resolution is 'era', so three seasons carry one estimate.
export async function getPlayerSeasonRapm(
  seasonRapmRunId: number,
  playerId: number,
  scope: string = FORWARD_RAPM_SCOPE,
): Promise<PlayerSeasonRapm[]> {
  const rows = await db
    .select({
      seasonId: playerRapm.seasonId,
      year: seasons.year,
      title: titles.name,
      resolution: playerRapm.resolution,
      maps: playerRapm.maps,
      coef: playerRapm.coef,
      se: playerRapm.se,
      concentration: playerRapm.teammateConcentration,
      penaltyShare: playerRapm.penaltyShare,
    })
    .from(playerRapm)
    .innerJoin(seasons, eq(seasons.id, playerRapm.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .where(
      and(
        eq(playerRapm.runId, seasonRapmRunId),
        eq(playerRapm.playerId, playerId),
        eq(playerRapm.scope, scope),
      ),
    )
    .orderBy(asc(seasons.year));
  return rows.map((r) => ({
    ...r,
    seasonId: r.seasonId as number,
    resolved: Math.abs(r.coef) > 1.96 * r.se,
    entangled: r.concentration >= RAPM_CONCENTRATION_LIMIT,
  }));
}

// ---------- Opponent adjustment ----------

export type LadderRung = {
  fits: number;
  mean_abs_dz_median: number;
  mean_abs_dz_p90: number;
  mean_abs_dz_max: number;
  top_n_churn_total: number;
  placebo_ratio_median: number | null;
  reliability_measured: number;
  reliability_gain_median: number | null;
  reliability_gain_positive: number;
};

export type LadderVerdict = {
  rung: string;
  clears: boolean;
  clears_movement: boolean;
  clears_placebo: boolean;
  clears_reliability: boolean;
  mean_abs_dz_median: number;
  placebo_ratio_median: number | null;
  reliability_gain_median: number | null;
};

export type OpponentAdjustment = {
  version: string;
  // What the ladder adjusts, which is not the plus-minus.
  adjusts: string;
  rungs: string[];
  ladder: Record<string, LadderRung>;
  stop_rule: {
    adopted: string | null;
    per_rung: LadderVerdict[];
    thresholds: {
      mean_abs_dz: number;
      placebo_ratio: number;
      reliability_gain: number;
    };
  };
  // The share of lines whose opponent the first rung cannot see. A rung that is
  // blind on part of the record cannot be read as an adjustment of it.
  coverage: {
    lines: number;
    blind_share: number;
    opponent_rated: number;
    opponent_at_prior: number;
    opponent_missing: number;
    blind_by_season: Record<string, number>;
  };
};

export function getOpponentAdjustment(
  runId: number,
): Promise<OpponentAdjustment | null> {
  return artifactPayload<OpponentAdjustment>(runId, "opponent_adjustment");
}

// ---------- Match context ----------
//
// The venue, the stage and the map. Every number here is an adjusted
// association: the design holds the player and the opposing lineup fixed, so a
// coefficient is what is left after those, and it is still not a cause.

export type ContextFamilyStats = {
  fits: number;
  verdict?: string;
  cohorts_improved?: number;
  cohorts_measured?: number;
  top_n_churn?: number;
  leaderboard_move?: { n: number; median: number; p90: number; max: number };
  oof_rmse_delta?: { n: number; median: number; p90: number; max: number };
};

export type ContextVenuePlayer = {
  season: number;
  league: string;
  title: string;
  mode: string;
  feature: string;
  player_id: number;
  player: string;
  raw: number | null;
  pooled: number | null;
  deviation: number | null;
  common: number | null;
  se: number | null;
  lo: number | null;
  hi: number | null;
  in_cohort_sd: number | null;
  common_in_cohort_sd: number | null;
  lan_maps: number;
  online_maps: number;
  survives_opponent: number | null;
  clears_interval: boolean;
};

export type ContextHostRow = {
  season: number;
  league: string;
  mode: string;
  feature: string;
  coefficient: number | null;
  se: number | null;
  lo: number | null;
  hi: number | null;
  in_cohort_sd: number | null;
  host_lines: number;
  clears_interval: boolean;
};

export type ContextMapRow = {
  season: number;
  league: string;
  mode: string;
  feature: string;
  map: string;
  effect: number | null;
};

export type MatchContext = {
  version: string;
  adjusts: string;
  claim: string;
  families: string[];
  ablation: {
    declared_before_fitting: boolean;
    keep_share: number;
    by_family: Record<string, ContextFamilyStats>;
    verdicts: Record<string, string>;
  };
  venue_effect: {
    unit: string;
    min_rows: number;
    min_rows_per_side: number;
    players: ContextVenuePlayer[];
    n_clearing_interval: number;
    n_players: number;
  };
  host_effect: {
    unit: string;
    source: string;
    per_cohort: ContextHostRow[];
    n_clearing_interval: number;
  };
  map_identity: {
    pooling: string;
    min_rows: number;
    per_cohort: ContextMapRow[];
  };
  coverage: {
    lines_without_context: number;
    by_era: Record<string, Record<string, number>>;
    by_stakes: Record<string, number>;
  };
  stakes: { levels: string[]; base: string };
};

export function latestMatchContextRun(): Promise<ModelRun | null> {
  return latestRun("match_context");
}

export function getMatchContext(runId: number): Promise<MatchContext | null> {
  return artifactPayload<MatchContext>(runId, "match_context");
}

// ---------- The openskill baseline ----------

export type OpenskillBaseline = {
  what: string;
  model: string;
  n_maps: number;
  n_players: number;
  n_player_seasons_published: number;
  n_player_seasons_thin: number;
  filtered_by_construction: string;
  params: { mu: number; sigma: number; min_maps_season: number };
  leaders: { player: string; mu: number; sigma: number; ordinal: number }[];
};

export function getOpenskillBaseline(
  runId: number,
): Promise<OpenskillBaseline | null> {
  return artifactPayload<OpenskillBaseline>(runId, "openskill_baseline");
}

// ---------- SKILL ----------

export type SkillPrior = {
  what: string;
  version: string;
  feature_set_version: string;
  // The verdict the phase reached, in one sentence, written by the model.
  statement: string;
  ladder: { rule: string; what: string; published_arm: string };
  design: { n_columns: number; columns: string[] };
  target: { n: number; players: number; scope: string; resolution: string };
  blend: {
    n: number;
    what: string;
    seasons: number[];
    mean_weight_prior: number;
    min_weight_prior: number;
    max_weight_prior: number;
    corr_with_prior: number;
    corr_with_coef: number;
  };
  // tau2 near zero is the finding: the target holds no between-player variance
  // for the blend to defend, so the weight falls on the prior.
  weights: { tau2: number; what: string };
  target_signal: {
    available: boolean;
    n: number;
    tau: number;
    tau2: number;
    sd_coef: number;
    mean_se: number;
    mean_obs_var: number;
    collapsed: boolean;
    distinguishable: boolean;
    reading: string;
  };
  coefficients: {
    available: boolean;
    arm: string;
    lambda: number;
    intercept: number;
    effective_df: number;
    by_column: { column: string; beta: number }[];
  };
};

export function getSkillPrior(runId: number): Promise<SkillPrior | null> {
  return artifactPayload<SkillPrior>(runId, "skill_prior");
}

export type PlayerSkillSeason = {
  seasonId: number;
  year: number;
  title: string;
  priorMean: number;
  priorSd: number;
  coef: number;
  se: number;
  skill: number;
  skillSd: number;
  weightPrior: number;
  model: string;
};

// One player's SKILL by season. Empty before 2021: the first season has
// nothing before it to train the prior on, and the CWL years carry no
// season-resolution coefficient to blend with.
export async function getPlayerSkill(
  skillRunId: number,
  playerId: number,
): Promise<PlayerSkillSeason[]> {
  return db
    .select({
      seasonId: playerSkill.seasonId,
      year: seasons.year,
      title: titles.name,
      priorMean: playerSkill.priorMean,
      priorSd: playerSkill.priorSd,
      coef: playerSkill.coef,
      se: playerSkill.se,
      skill: playerSkill.skill,
      skillSd: playerSkill.skillSd,
      weightPrior: playerSkill.weightPrior,
      model: playerSkill.model,
    })
    .from(playerSkill)
    .innerJoin(seasons, eq(seasons.id, playerSkill.seasonId))
    .innerJoin(titles, eq(titles.id, seasons.titleId))
    .where(
      and(eq(playerSkill.runId, skillRunId), eq(playerSkill.playerId, playerId)),
    )
    .orderBy(asc(seasons.year));
}

export type SkillLeaderRow = {
  playerId: number;
  handle: string;
  seasonId: number;
  year: number;
  skill: number;
  skillSd: number;
  weightPrior: number;
};

// The SKILL leaderboard for one season. Seasons before 2021 hold no rows, and
// a caller must render that as an absence rather than an empty leaderboard.
export async function getSkillLeaderboard(
  skillRunId: number,
  seasonId: number,
  limit = 25,
): Promise<SkillLeaderRow[]> {
  return db
    .select({
      playerId: playerSkill.playerId,
      handle: players.handle,
      seasonId: playerSkill.seasonId,
      year: seasons.year,
      skill: playerSkill.skill,
      skillSd: playerSkill.skillSd,
      weightPrior: playerSkill.weightPrior,
    })
    .from(playerSkill)
    .innerJoin(players, eq(players.id, playerSkill.playerId))
    .innerJoin(seasons, eq(seasons.id, playerSkill.seasonId))
    .where(
      and(eq(playerSkill.runId, skillRunId), eq(playerSkill.seasonId, seasonId)),
    )
    .orderBy(desc(playerSkill.skill))
    .limit(limit);
}

// Which seasons SKILL covers at all, so a page can degrade to VALUE on the ones
// it does not cover instead of rendering an empty surface.
export async function getSkillSeasons(
  skillRunId: number,
): Promise<{ seasonId: number; year: number; players: number }[]> {
  const rows = await db.execute(sql`
    SELECT ps.season_id, s.year, count(*)::int AS players
    FROM player_skill ps
    JOIN seasons s ON s.id = ps.season_id
    WHERE ps.run_id = ${skillRunId}
    GROUP BY ps.season_id, s.year
    ORDER BY s.year
  `);
  return (
    rows as unknown as { season_id: number; year: number; players: number }[]
  ).map((r) => ({ seasonId: r.season_id, year: r.year, players: r.players }));
}

// ---------- The pinned evaluation harness ----------

export type EvaluationGap = {
  what: string;
  delta_r: number;
  lo: number;
  hi: number;
  se: number;
  mde80: number;
  clears_mde: boolean;
  excludes_zero: boolean;
  beats_baseline: boolean;
};

export type EvaluationDeclaration = {
  name: string;
  role: string;
  what: string;
  target: string;
  statistic: string;
  baselines: string[];
  predictors: string[];
  resampling_unit: string;
  significance_claimed: boolean;
};

export type EvaluationPrimary = {
  available: boolean;
  panel: string;
  n: number;
  n_panel: number;
  baseline_r: number;
  scored_predictors: string[];
  predictors: Record<string, { r: number; lo: number; hi: number }>;
  gaps: Record<string, EvaluationGap>;
  predictor_agreement: Record<string, number>;
  resampling: { b: number; unit: string; why: string; clusters: number };
  declared: EvaluationDeclaration;
};

export type EvaluationManifest = {
  what: string;
  version: string;
  sha256: string;
  pinned_sha256: string;
  // False means the harness changed after the pin, so every number it produced
  // comes from a different test than the declared one.
  matches_pin: boolean;
  mde: string;
  bootstrap_b: number;
  power_alpha: number;
  power_target: number;
  primary: EvaluationDeclaration;
  secondary: {
    name: string;
    role: string;
    what: string;
    target: string;
    statistic: string;
    resampling_unit: string;
  }[];
  reproduces: { model: string; artifact: string }[];
  supersedes: { version: string; sha256: string; changed: string }[];
};

export type EvaluationPlacebo = {
  what: string;
  passes: boolean;
  n_run: number;
  n_failed: number;
  deferred: Record<string, string>;
  placebos: Record<
    string,
    { what: string; passes: boolean; available: boolean }
  >;
};

export type EvaluationReproduction = {
  what: string;
  reproduces: boolean;
  tolerance: number;
  n_mismatched: number;
  n_page_mismatched: number;
  recomputed: {
    what: string;
    harness: number;
    published: number;
    matches: boolean;
  }[];
  against_the_page: {
    what: string;
    run: number | string;
    page: number | string;
    matches: boolean;
  }[];
};

export type SkillPower = {
  available: boolean;
  what: string;
  statement: string;
  unit: string;
  n_panel: number;
  n_eligible: number;
  clusters: number;
  baseline_r: number;
  design_effect: number;
  distance_to_clear: number;
  eligibility: string;
  dropped: Record<string, number>;
  floors: Record<
    string,
    {
      mde80_clustered: number;
      mde80_independent: number;
      predictor_agreement: number;
    }
  >;
};

export type EvaluationPopulation = {
  path: string;
  rule: string;
  cut: string;
  sha256: string;
  frozen: boolean;
  frozen_at: string;
  readable: boolean;
  // False means the frozen population and the database have diverged, so the
  // harness scored a different set of maps than the one it declared.
  matches: boolean;
  n_maps: number;
  n_added: number;
  n_removed: number;
  eligible_now: number;
  by_season: Record<string, number>;
};

export function getEvaluationManifest(
  runId: number,
): Promise<EvaluationManifest | null> {
  return artifactPayload<EvaluationManifest>(runId, "evaluation_manifest");
}

export function getEvaluationPrimary(
  runId: number,
): Promise<EvaluationPrimary | null> {
  return artifactPayload<EvaluationPrimary>(runId, "evaluation_primary");
}

// Seven secondary tests, none of which claims significance. Only the two the
// page argues from are typed; the rest stay readable as unknown.
export type EvaluationSecondary = {
  prior_target_persistence: {
    n: number;
    what: string;
    target: string;
    resolution_read: string;
    predictors: Record<string, number>;
    significance_claimed: boolean;
  };
  season_plusminus_persistence: {
    n: number;
    kd_z: number;
    rapm_filtered: number;
    scope_read: string;
    resolution_read: string;
    significance_claimed: boolean;
  };
  [key: string]: unknown;
};

export function getEvaluationSecondary(
  runId: number,
): Promise<EvaluationSecondary | null> {
  return artifactPayload<EvaluationSecondary>(runId, "evaluation_secondary");
}

export function getEvaluationPlacebo(
  runId: number,
): Promise<EvaluationPlacebo | null> {
  return artifactPayload<EvaluationPlacebo>(runId, "evaluation_placebo");
}

export function getEvaluationReproduction(
  runId: number,
): Promise<EvaluationReproduction | null> {
  return artifactPayload<EvaluationReproduction>(
    runId,
    "evaluation_reproduction",
  );
}

export function getSkillPower(runId: number): Promise<SkillPower | null> {
  return artifactPayload<SkillPower>(runId, "skill_power");
}

// Written onto the metric-diff run, which is the run that freezes it.
export async function getEvaluationPopulation(): Promise<EvaluationPopulation | null> {
  const run = await latestRun("metric_diff");
  if (!run) return null;
  return artifactPayload<EvaluationPopulation>(run.id, "evaluation_population");
}

// ---------- Validation: the four adversarial checks ----------
//
// Its own run, written after the ratings it scores. Nothing here can move a
// coefficient, and nothing here carries a third-party rating value: the source
// licence forbids redistribution, so a named disagreement ships ranks and our
// own number and never theirs.

export type ValidationConvergent = {
  source: string;
  attribution: string;
  verdict: string;
  limits: string;
  disagreement_count: number;
  population: {
    player_seasons: number;
    axes: Record<string, number>;
    coverage: {
      year: number;
      rated_maps: number;
      unrated_maps: number;
      rated_share: number;
    }[];
  };
  axes: {
    axis: string;
    n: number;
    n_players: number;
    pearson: number;
    spearman: number;
    spearman_lo95: number | null;
    spearman_hi95: number | null;
    by_season: { year: number; n: number; pearson: number; spearman: number }[];
  }[];
  disagreements: {
    player_id: number;
    handle: string;
    year: number;
    field: number;
    our_rank: number;
    their_rank: number;
    our_rating: number;
    gap: number;
    axis: string;
  }[];
};

export type ValidationFace = {
  verdict: string;
  limits: string;
  agreement_rate: number | null;
  expected_rate: number | null;
  population: {
    awards_scored: number;
    awards_excluded: number;
    referents_loaded: number;
  };
  by_season: {
    year: number;
    team_size: number;
    selections: number;
    scored: number;
    field: number;
    in_top_n: number;
    in_top_2n: number;
    expected_top_n: number;
  }[];
  excluded: { handle: string; year: number; award: string }[];
  ranked_awards: {
    award: string;
    handle: string;
    year: number;
    cohort: number;
    rank: number | null;
    prior_rated_seasons: number;
  }[];
};

export type ValidationRetrodiction = {
  verdict: string;
  limits: string;
  stability_floor: number;
  worst_spearman: number | null;
  passes_floor: boolean | null;
  one_sided_violations: number;
  population: {
    seasons_held_out: number;
    admitted_maps: number;
    player_cells: number;
  };
  by_holdout: {
    held_out: number;
    maps_removed?: number;
    cells_after: number;
    cells_before?: number;
    cells_before_moved?: number;
    spearman: number | null;
    pearson?: number;
    note?: string;
  }[];
};

export type ValidationShockArm = {
  axis: string;
  verdict: string;
  limits: string;
  outcome?: string;
  slope?: number;
  lo95?: number;
  hi95?: number;
  slope_per_sd?: number;
  lo95_per_sd?: number;
  hi95_per_sd?: number;
  detectable_slope?: number;
  excludes_zero?: boolean;
  informative?: boolean;
  population: {
    swaps: number;
    scored: number;
    unrated: number;
    teams?: number;
  };
};

export type ValidationShock = ValidationShockArm & {
  against: ValidationShockArm;
};

export function latestValidationRun(): Promise<ModelRun | null> {
  return latestRun("validation");
}

export function getValidationConvergent(
  runId: number,
): Promise<ValidationConvergent | null> {
  return artifactPayload<ValidationConvergent>(runId, "validation_convergent");
}

export function getValidationFace(
  runId: number,
): Promise<ValidationFace | null> {
  return artifactPayload<ValidationFace>(runId, "validation_face");
}

export function getValidationRetrodiction(
  runId: number,
): Promise<ValidationRetrodiction | null> {
  return artifactPayload<ValidationRetrodiction>(
    runId,
    "validation_retrodiction",
  );
}

export function getValidationShock(
  runId: number,
): Promise<ValidationShock | null> {
  return artifactPayload<ValidationShock>(runId, "validation_shock");
}

// ---------- The identification pre-flight ----------
//
// Fitted before the season plus-minus, and the reason that model has two
// resolutions rather than one. Its own run, again.

export function latestRapmPreflightRun(): Promise<ModelRun | null> {
  return latestRun("rapm_preflight");
}

export type RapmPreflightEra = {
  league: string;
  branch: string;
  stops: boolean;
  rank_share: number;
  thin_lineups: boolean;
  recovery_within_team: number;
  median_effective_lineups: number;
  rank_below_threshold: boolean;
  recovery_below_threshold: boolean;
};

export type RapmPreflightVerdict = {
  what: string;
  branch: string;
  reason: string;
  by_era: RapmPreflightEra[];
  eras_stopped: string[];
  // True where the phase as specified would have been stopped outright, which
  // is what the fork resolves rather than overrules.
  stops_p1_as_specified: boolean;
  thresholds: {
    rank_share: number;
    recovery_floor: number;
    effective_lineups: number;
    recovery_within_team: number;
  };
};

export function getRapmPreflight(
  runId: number,
): Promise<RapmPreflightVerdict | null> {
  return artifactPayload<RapmPreflightVerdict>(runId, "rapm_preflight_verdict");
}
