// Read-side queries for model outputs. Everything resolves through the
// latest model_runs row per model, so pages always render one coherent,
// versioned snapshot and never mix runs.
import { and, desc, eq, inArray, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import {
  backtests,
  gameModes,
  insights,
  modelRuns,
  players,
  playerSeasonAdjusted,
  rosterStints,
  seasons,
  series,
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

export async function latestRun(model: string): Promise<ModelRun | null> {
  const rows = await db
    .select()
    .from(modelRuns)
    .where(eq(modelRuns.model, model))
    .orderBy(desc(modelRuns.createdAt), desc(modelRuns.id))
    .limit(1);
  return rows[0] ?? null;
}

export function playerSlug(handle: string): string {
  return handle.toLowerCase();
}

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
  subjectSlug: string | null; // players only — teams have no page yet
};

export async function getFeed(
  runId: number,
  limit = 40,
  kind?: string,
): Promise<FeedItem[]> {
  const conditions = [eq(insights.runId, runId)];
  if (kind) conditions.push(eq(insights.kind, kind));
  const rows = await db
    .select({
      id: insights.id,
      kind: insights.kind,
      headline: insights.headline,
      detail: insights.detail,
      score: insights.score,
      subjectType: insights.subjectType,
      subjectId: insights.subjectId,
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
    .limit(limit);

  return rows.map((r) => ({
    id: r.id,
    kind: r.kind,
    headline: r.headline,
    detail: (r.detail ?? {}) as Record<string, unknown>,
    score: r.score,
    subjectType: r.subjectType,
    subjectId: r.subjectId,
    subjectName: r.playerHandle ?? r.teamName,
    subjectSlug: r.playerHandle ? playerSlug(r.playerHandle) : null,
  }));
}

export async function getFeedKinds(runId: number): Promise<{ kind: string; n: number }[]> {
  const rows = await db.execute(sql`
    SELECT kind, count(*) AS n FROM insights WHERE run_id = ${runId}
    GROUP BY kind ORDER BY count(*) DESC
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    kind: String(r.kind),
    n: Number(r.n),
  }));
}

// ---------- Archive overview ----------

export type ArchiveStats = {
  seriesCount: number;
  maps: number;
  players: number;
  statRows: number;
  events: number;
};

export async function getArchiveStats(): Promise<ArchiveStats> {
  const rows = await db.execute(sql`
    SELECT (SELECT count(*) FROM series) AS series_count,
           (SELECT count(*) FROM games) AS maps,
           (SELECT count(DISTINCT player_id) FROM game_player_stats) AS players,
           (SELECT count(*) FROM game_player_stats) AS stat_rows,
           (SELECT count(*) FROM events) AS events
  `);
  const r = (rows as unknown as Record<string, unknown>[])[0];
  return {
    seriesCount: Number(r.series_count),
    maps: Number(r.maps),
    players: Number(r.players),
    statRows: Number(r.stat_rows),
    events: Number(r.events),
  };
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

// ---------- Player-season explorer (/players) ----------

export type ExplorerFilters = {
  year?: number;
  modeSlug?: string; // undefined = all modes combined (mode_id IS NULL rows)
  minMaps: number;
  sort: "kd_z" | "kd_raw" | "kd_pctl" | "maps" | "engagement_z" | "obj_z";
  dir: "asc" | "desc";
  q?: string; // handle substring
  limit: number;
};

export type ExplorerRow = {
  playerId: number;
  handle: string;
  slug: string;
  year: number;
  title: string;
  mode: string | null;
  mapsPlayed: number;
  kdRaw: number | null;
  kdZ: number | null;
  kdPctl: number | null;
  engagementZ: number | null;
  objZ: number | null;
  completeness: number;
};

const EXPLORER_SORT_COLS: Record<ExplorerFilters["sort"], string> = {
  kd_z: "psa.kd_z",
  kd_raw: "psa.kd_raw",
  kd_pctl: "psa.kd_pctl",
  maps: "psa.maps_played",
  engagement_z: "psa.engagement_z",
  obj_z: "psa.obj_z",
};

export async function queryPlayerSeasons(
  eraRunId: number,
  f: ExplorerFilters,
): Promise<ExplorerRow[]> {
  const sortCol = EXPLORER_SORT_COLS[f.sort];
  const rows = await db.execute(sql`
    SELECT psa.player_id, p.handle, se.year, t.short_name AS title,
           gm.name AS mode, psa.maps_played, psa.kd_raw, psa.kd_z, psa.kd_pctl,
           psa.engagement_z, psa.obj_z, psa.completeness
    FROM player_season_adjusted psa
    JOIN players p ON p.id = psa.player_id
    JOIN seasons se ON se.id = psa.season_id
    JOIN titles t ON t.id = se.title_id
    LEFT JOIN game_modes gm ON gm.id = psa.mode_id
    WHERE psa.run_id = ${eraRunId}
      AND psa.maps_played >= ${f.minMaps}
      AND ${f.modeSlug ? sql`gm.slug = ${f.modeSlug}` : sql`psa.mode_id IS NULL`}
      AND ${f.year ? sql`se.year = ${f.year}` : sql`TRUE`}
      AND ${f.q ? sql`p.handle ILIKE ${"%" + f.q + "%"}` : sql`TRUE`}
    ORDER BY ${sql.raw(sortCol)} ${sql.raw(f.dir === "asc" ? "ASC" : "DESC")} NULLS LAST,
             p.handle ASC
    LIMIT ${f.limit}
  `);
  return (rows as unknown as Record<string, unknown>[]).map((r) => ({
    playerId: Number(r.player_id),
    handle: String(r.handle),
    slug: playerSlug(String(r.handle)),
    year: Number(r.year),
    title: String(r.title),
    mode: r.mode === null ? null : String(r.mode),
    mapsPlayed: Number(r.maps_played),
    kdRaw: r.kd_raw === null ? null : Number(r.kd_raw),
    kdZ: r.kd_z === null ? null : Number(r.kd_z),
    kdPctl: r.kd_pctl === null ? null : Number(r.kd_pctl),
    engagementZ: r.engagement_z === null ? null : Number(r.engagement_z),
    objZ: r.obj_z === null ? null : Number(r.obj_z),
    completeness: Number(r.completeness),
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

export type SeasonAdjusted = {
  seasonId: number;
  year: number;
  title: string;
  modeId: number | null;
  mode: string | null;
  mapsPlayed: number;
  kdRaw: number | null;
  kdZ: number | null;
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
    SELECT t.id AS team_id, t.name AS team,
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
    finalElo: Number(r.final_elo),
    peakElo: Number(r.peak_elo),
    glicko: r.glicko === null ? null : Number(r.glicko),
    glickoRd: r.glicko_rd === null ? null : Number(r.glicko_rd),
    nSeries: Number(r.n_series),
    lastPlayed: r.last_played ? new Date(String(r.last_played)) : null,
  }));
}

export type EloPoint = { t: string; rating: number };
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
    tl.points.push({ t: r.playedAt?.toISOString() ?? "", rating: r.rating });
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
  objVsSlay: number; // objective weight / mean |slaying| weight
};

// The learned map-outcome regression weights, one cohort per (season × mode).
// objVsSlay reads the kills/deaths pair jointly (they are near-collinear in
// respawn modes, so ridge splits their shared weight).
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
        }[];
      }
    | undefined;
  if (!payload) return [];
  return payload.cohorts.map((c) => {
    const slay =
      (Math.abs(c.weights.kills_p10 ?? 0) + Math.abs(c.weights.deaths_p10 ?? 0)) / 2;
    return {
      year: c.year,
      title: c.title,
      mode: c.mode,
      nMaps: c.n_maps,
      weights: c.weights,
      objVsSlay: slay > 0 ? Math.max(c.weights.obj_p10 ?? 0, 0) / slay : 0,
    };
  });
}

export type WinprobArtifact = {
  finalWeights: Record<string, number>;
  finalIntercept: number;
  minTrain: number;
  refitEvery: number;
  formWindow: number;
};

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
      }
    | undefined;
  if (!p) return null;
  return {
    finalWeights: p.final_weights,
    finalIntercept: p.final_intercept,
    minTrain: p.min_train,
    refitEvery: p.refit_every,
    formWindow: p.form_window,
  };
}
