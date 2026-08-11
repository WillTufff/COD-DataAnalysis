// Drizzle mirror of db/migrations/*.sql — the SQL files are the source of
// truth; keep this file in sync when adding migrations.
import { sql } from "drizzle-orm";
import {
  boolean,
  date,
  integer,
  jsonb,
  numeric,
  pgTable,
  primaryKey,
  serial,
  real,
  smallint,
  smallserial,
  text,
  timestamp,
  unique,
  uniqueIndex,
} from "drizzle-orm/pg-core";

// ===== Reference =====
export const titles = pgTable("titles", {
  id: smallserial("id").primaryKey(),
  name: text("name").notNull().unique(),
  shortName: text("short_name").notNull(),
  releaseYear: integer("release_year").notNull(),
  era: text("era", { enum: ["early", "mlg", "cwl", "cdl"] }).notNull(),
});

export const gameModes = pgTable("game_modes", {
  id: smallserial("id").primaryKey(),
  name: text("name").notNull(),
  slug: text("slug").notNull().unique(),
});

export const maps = pgTable(
  "maps",
  {
    id: serial("id").primaryKey(),
    name: text("name").notNull(),
    titleId: smallint("title_id").references(() => titles.id),
  },
  (t) => [unique().on(t.name, t.titleId)],
);

// ===== People & orgs =====
export const players = pgTable("players", {
  id: serial("id").primaryKey(),
  handle: text("handle").notNull(),
  realName: text("real_name"),
  country: text("country"),
  birthdate: date("birthdate"),
  role: text("role"),
  liquipediaPage: text("liquipedia_page").unique(),
  isActive: boolean("is_active").default(true),
  earnings: numeric("earnings"),
  earningsByYear: jsonb("earnings_by_year"),
});

export const playerAliases = pgTable(
  "player_aliases",
  {
    playerId: integer("player_id")
      .notNull()
      .references(() => players.id),
    alias: text("alias").notNull(),
  },
  (t) => [primaryKey({ columns: [t.playerId, t.alias] })],
);

export const orgs = pgTable("orgs", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  liquipediaPage: text("liquipedia_page").unique(),
});

export const teams = pgTable("teams", {
  id: serial("id").primaryKey(),
  orgId: integer("org_id").references(() => orgs.id),
  name: text("name").notNull(),
  region: text("region"),
  activeFrom: date("active_from"),
  activeTo: date("active_to"),
  liquipediaPage: text("liquipedia_page"),
  earnings: numeric("earnings"),
  createDate: date("create_date"),
  disbandDate: date("disband_date"),
});

export const rosterStints = pgTable("roster_stints", {
  id: serial("id").primaryKey(),
  playerId: integer("player_id")
    .notNull()
    .references(() => players.id),
  teamId: integer("team_id")
    .notNull()
    .references(() => teams.id),
  role: text("role"),
  startDate: date("start_date").notNull(),
  endDate: date("end_date"),
  source: text("source"),
});

// ===== Competition hierarchy =====
export const seasons = pgTable(
  "seasons",
  {
    id: serial("id").primaryKey(),
    year: integer("year").notNull(),
    titleId: smallint("title_id")
      .notNull()
      .references(() => titles.id),
    league: text("league").notNull(),
  },
  (t) => [unique().on(t.year, t.titleId, t.league)],
);

export const events = pgTable("events", {
  id: serial("id").primaryKey(),
  seasonId: integer("season_id").references(() => seasons.id),
  name: text("name").notNull(),
  tier: text("tier"),
  startDate: date("start_date"),
  endDate: date("end_date"),
  location: text("location"),
  isLan: boolean("is_lan"),
  prizePool: numeric("prize_pool"),
  liquipediaPage: text("liquipedia_page").unique(),
  tierType: text("tier_type"),
  publisherTier: text("publisher_tier"),
  format: text("format"),
});

export const stages = pgTable("stages", {
  id: serial("id").primaryKey(),
  eventId: integer("event_id")
    .notNull()
    .references(() => events.id),
  name: text("name").notNull(),
  ordinal: integer("ordinal"),
});

export const series = pgTable("series", {
  id: serial("id").primaryKey(),
  stageId: integer("stage_id").references(() => stages.id),
  eventId: integer("event_id")
    .notNull()
    .references(() => events.id),
  team1Id: integer("team1_id").references(() => teams.id),
  team2Id: integer("team2_id").references(() => teams.id),
  team1Score: smallint("team1_score"),
  team2Score: smallint("team2_score"),
  bestOf: smallint("best_of"),
  playedAt: timestamp("played_at", { withTimezone: true }),
  roundLabel: text("round_label"),
  liquipediaMatchId: text("liquipedia_match_id").unique(),
  dataSource: text("data_source", { enum: ["cwl_archive", "cito", "lpdb"] }).notNull(),
  sourceUid: text("source_uid").unique(),
});

export const games = pgTable(
  "games",
  {
    id: serial("id").primaryKey(),
    seriesId: integer("series_id")
      .notNull()
      .references(() => series.id),
    ordinal: smallint("ordinal").notNull(),
    mapId: integer("map_id").references(() => maps.id),
    modeId: smallint("mode_id").references(() => gameModes.id),
    team1Score: smallint("team1_score"),
    team2Score: smallint("team2_score"),
    winnerTeamId: integer("winner_team_id").references(() => teams.id),
    durationS: integer("duration_s"),
    endedAt: timestamp("ended_at", { withTimezone: true }),
    sourceUid: text("source_uid").unique(),
    dataSource: text("data_source", { enum: ["cwl_archive", "cito", "lpdb"] }).notNull(),
  },
  (t) => [unique().on(t.seriesId, t.ordinal)],
);

// ===== Stat lines =====
export const gamePlayerStats = pgTable(
  "game_player_stats",
  {
    gameId: integer("game_id")
      .notNull()
      .references(() => games.id),
    playerId: integer("player_id")
      .notNull()
      .references(() => players.id),
    teamId: integer("team_id")
      .notNull()
      .references(() => teams.id),
    kills: smallint("kills"),
    deaths: smallint("deaths"),
    assists: smallint("assists"),
    damage: integer("damage"),
    hillTime: smallint("hill_time"),
    firstBloods: smallint("first_bloods"),
    plants: smallint("plants"),
    defuses: smallint("defuses"),
    ticks: smallint("ticks"),
    extras: jsonb("extras"),
    dataSource: text("data_source", { enum: ["cwl_archive", "cito", "lpdb"] }).notNull(),
    contestedHillTime: smallint("contested_hill_time"),
    firstDeaths: smallint("first_deaths"),
    captures: smallint("captures"),
    highestStreak: smallint("highest_streak"),
    nonTradedKills: smallint("non_traded_kills"),
    sndRounds: smallint("snd_rounds"),
    clutch1v1: smallint("clutch_1v1"),
    clutch1v2: smallint("clutch_1v2"),
    clutch1v3: smallint("clutch_1v3"),
    clutch1v4: smallint("clutch_1v4"),
    ctlAttackRounds: smallint("ctl_attack_rounds"),
    ctlDefenseRounds: smallint("ctl_defense_rounds"),
    ctlZoneCaptures: smallint("ctl_zone_captures"),
  },
  (t) => [primaryKey({ columns: [t.gameId, t.playerId] })],
);

export const eventPlacements = pgTable(
  "event_placements",
  {
    eventId: integer("event_id")
      .notNull()
      .references(() => events.id),
    teamId: integer("team_id")
      .notNull()
      .references(() => teams.id),
    placementMin: smallint("placement_min"),
    placementMax: smallint("placement_max"),
    prize: numeric("prize"),
    individualPrize: numeric("individual_prize"),
    dataSource: text("data_source", { enum: ["cwl_archive", "cito", "lpdb"] }).notNull(),
  },
  (t) => [primaryKey({ columns: [t.eventId, t.teamId] })],
);

export const transfers = pgTable("transfers", {
  id: serial("id").primaryKey(),
  playerId: integer("player_id")
    .notNull()
    .references(() => players.id),
  transferDate: date("transfer_date").notNull(),
  fromTeamId: integer("from_team_id").references(() => teams.id),
  toTeamId: integer("to_team_id").references(() => teams.id),
  role: text("role"),
  platform: text("platform"),
  reference: text("reference"),
  dataSource: text("data_source", { enum: ["cwl_archive", "cito", "lpdb"] }).notNull(),
});

export const ingestRuns = pgTable("ingest_runs", {
  id: serial("id").primaryKey(),
  startedAt: timestamp("started_at", { withTimezone: true }).defaultNow(),
  kind: text("kind"),
  params: jsonb("params"),
  status: text("status"),
  rowsUpserted: jsonb("rows_upserted"),
  notes: text("notes"),
});

// ===== Analytics layer (0003_analytics.sql) =====
export const modelRuns = pgTable(
  "model_runs",
  {
    id: serial("id").primaryKey(),
    model: text("model").notNull(),
    version: text("version").notNull(),
    codeRef: text("code_ref"),
    params: jsonb("params"),
    dataThrough: date("data_through"),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  },
  (t) => [unique().on(t.model, t.version, t.dataThrough)],
);

export const teamRatings = pgTable(
  "team_ratings",
  {
    runId: integer("run_id")
      .notNull()
      .references(() => modelRuns.id),
    teamId: integer("team_id")
      .notNull()
      .references(() => teams.id),
    seriesId: integer("series_id")
      .notNull()
      .references(() => series.id),
    ratingPre: real("rating_pre").notNull(),
    ratingPost: real("rating_post").notNull(),
    ratingSd: real("rating_sd"),
  },
  (t) => [primaryKey({ columns: [t.runId, t.teamId, t.seriesId] })],
);

export const playerSeasonAdjusted = pgTable("player_season_adjusted", {
  runId: integer("run_id")
    .notNull()
    .references(() => modelRuns.id),
  playerId: integer("player_id")
    .notNull()
    .references(() => players.id),
  seasonId: integer("season_id")
    .notNull()
    .references(() => seasons.id),
  modeId: smallint("mode_id").references(() => gameModes.id),
  mapsPlayed: integer("maps_played").notNull(),
  kdRaw: real("kd_raw"),
  kdZ: real("kd_z"),
  kdZSe: real("kd_z_se"),
  kdPctl: real("kd_pctl"),
  engagementZ: real("engagement_z"),
  objZ: real("obj_z"),
  rating: real("rating"),
  ratingSd: real("rating_sd"),
  completeness: real("completeness").notNull(),
});

export const careerCurves = pgTable(
  "career_curves",
  {
    runId: integer("run_id")
      .notNull()
      .references(() => modelRuns.id),
    playerId: integer("player_id")
      .notNull()
      .references(() => players.id),
    ageOrSeq: real("age_or_seq").notNull(),
    fitted: real("fitted").notNull(),
    lo95: real("lo95"),
    hi95: real("hi95"),
  },
  (t) => [primaryKey({ columns: [t.runId, t.playerId, t.ageOrSeq] })],
);

export const insights = pgTable("insights", {
  id: serial("id").primaryKey(),
  runId: integer("run_id").references(() => modelRuns.id),
  subjectType: text("subject_type").notNull(),
  subjectId: integer("subject_id").notNull(),
  kind: text("kind").notNull(),
  headline: text("headline").notNull(),
  detail: jsonb("detail").notNull(),
  score: real("score").notNull(),
  validThrough: date("valid_through"),
});

export const backtests = pgTable("backtests", {
  runId: integer("run_id")
    .notNull()
    .references(() => modelRuns.id)
    .primaryKey(),
  windowFrom: date("window_from").notNull(),
  windowTo: date("window_to").notNull(),
  nPredictions: integer("n_predictions").notNull(),
  brier: real("brier"),
  logLoss: real("log_loss"),
  accuracy: real("accuracy"),
  calibration: jsonb("calibration").notNull(),
});

export const modelArtifacts = pgTable(
  "model_artifacts",
  {
    runId: integer("run_id")
      .notNull()
      .references(() => modelRuns.id),
    name: text("name").notNull(),
    payload: jsonb("payload").notNull(),
  },
  (t) => [primaryKey({ columns: [t.runId, t.name] })],
);

export const playerMetricSeason = pgTable("player_metric_season", {
  runId: integer("run_id")
    .notNull()
    .references(() => modelRuns.id),
  playerId: integer("player_id")
    .notNull()
    .references(() => players.id),
  seasonId: integer("season_id")
    .notNull()
    .references(() => seasons.id),
  modeId: smallint("mode_id").references(() => gameModes.id),
  metric: text("metric").notNull(),
  value: real("value").notNull(),
  denom: real("denom").notNull(),
  z: real("z"),
  pctl: real("pctl"),
  qualified: boolean("qualified").notNull(),
});

export const teamMetricSeason = pgTable("team_metric_season", {
  runId: integer("run_id")
    .notNull()
    .references(() => modelRuns.id),
  teamId: integer("team_id")
    .notNull()
    .references(() => teams.id),
  seasonId: integer("season_id")
    .notNull()
    .references(() => seasons.id),
  modeId: smallint("mode_id").references(() => gameModes.id),
  metric: text("metric").notNull(),
  value: real("value").notNull(),
  denom: real("denom").notNull(),
  z: real("z"),
  pctl: real("pctl"),
  qualified: boolean("qualified").notNull(),
});

// 0012_player_style. Not a cluster label: the archetype partition does not beat
// a cloud with no clusters in it, so what is stored is a position on the
// retained style axes. The axis's meaning lives in the run's player_style
// artifact, which carries the loadings.
export const playerStyleSeason = pgTable("player_style_season", {
  runId: integer("run_id")
    .notNull()
    .references(() => modelRuns.id),
  playerId: integer("player_id")
    .notNull()
    .references(() => players.id),
  seasonId: integer("season_id")
    .notNull()
    .references(() => seasons.id),
  axis: smallint("axis").notNull(),
  score: real("score").notNull(),
  pctl: real("pctl").notNull(),
});

// RAPM coefficients. See 0013 — `se` is not optional decoration here, it is
// larger than the coefficient spread for most of the table.
//
// Since 0017 the table holds two shapes at once. A career row is one per player
// per rating run, with a null `season_id`. A season row belongs to the
// season-varying fit, lands under that model's own run, and there are several
// per player: one per season per `scope`, where an era-resolution coefficient
// is filed against every season it covers. So (run_id, player_id) is no longer
// unique and no longer the key — two partial unique indexes cover the halves
// separately, and a reader must say which half it wants. `getPlayerRapm` asks
// for the rating run, which by construction holds only career rows.
export const playerRapm = pgTable(
  "player_rapm",
  {
    runId: integer("run_id")
      .notNull()
      .references(() => modelRuns.id),
    playerId: integer("player_id")
      .notNull()
      .references(() => players.id),
    maps: integer("maps").notNull(),
    coef: real("coef").notNull(),
    se: real("se").notNull(),
    teammateConcentration: real("teammate_concentration").notNull(),
    // Null on a career row, which is what tells the two halves apart.
    seasonId: integer("season_id").references(() => seasons.id),
    // 'career' | 'smoothed' | 'filtered'. Only 'filtered' may be read forward:
    // the smoothed fit's penalty is two-sided and has seen the next season.
    scope: text("scope").notNull(),
    // 'career' | 'era' | 'season' — what one coefficient actually covers.
    resolution: text("resolution").notNull(),
    // Share of the column's prior variance surviving into the posterior. Read
    // against k/(k+1) — 0.80 at 4v4 — never against a flat threshold. Null on
    // career rows, which predate the statistic.
    penaltyShare: real("penalty_share"),
  },
  (t) => [
    uniqueIndex("player_rapm_career_key")
      .on(t.runId, t.playerId)
      .where(sql`season_id IS NULL`),
    uniqueIndex("player_rapm_season_key")
      .on(t.runId, t.scope, t.playerId, t.seasonId)
      .where(sql`season_id IS NOT NULL`),
  ],
);
