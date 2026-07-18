// Drizzle mirror of db/migrations/*.sql — the SQL files are the source of
// truth; keep this file in sync when adding migrations.
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
  },
  (t) => [primaryKey({ columns: [t.eventId, t.teamId] })],
);

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

export const playerArchetypes = pgTable(
  "player_archetypes",
  {
    runId: integer("run_id")
      .notNull()
      .references(() => modelRuns.id),
    playerId: integer("player_id")
      .notNull()
      .references(() => players.id),
    seasonId: integer("season_id")
      .notNull()
      .references(() => seasons.id),
    archetype: text("archetype").notNull(),
    loadings: jsonb("loadings"),
  },
  (t) => [primaryKey({ columns: [t.runId, t.playerId, t.seasonId] })],
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
