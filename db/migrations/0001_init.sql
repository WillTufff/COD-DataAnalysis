-- 0001_init: full core schema.
-- Nullability is deliberate: per-map stats simply don't exist for much of
-- pre-2018 CoD; "series known, map stats unknown" must be representable.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ===== Reference =====
CREATE TABLE titles (
  id            smallserial PRIMARY KEY,
  name          text NOT NULL,          -- 'Black Ops 7'
  short_name    text NOT NULL,          -- 'BO7'
  release_year  int  NOT NULL,
  era           text NOT NULL CHECK (era IN ('early','mlg','cwl','cdl')),
  UNIQUE (name)
);

CREATE TABLE game_modes (
  id      smallserial PRIMARY KEY,
  name    text NOT NULL,
  slug    text UNIQUE NOT NULL
);

CREATE TABLE maps (
  id        serial PRIMARY KEY,
  name      text NOT NULL,
  title_id  smallint REFERENCES titles(id),
  UNIQUE (name, title_id)
);

-- ===== People & orgs =====
CREATE TABLE players (
  id              serial PRIMARY KEY,
  handle          text NOT NULL,        -- current/canonical gamertag
  real_name       text,
  country         text,                 -- ISO 3166-1 alpha-2
  birthdate       date,
  role            text,                 -- 'AR','SMG','Flex','Coach' (current)
  liquipedia_page text UNIQUE,          -- source lineage / merge key
  is_active       boolean DEFAULT true
);

CREATE TABLE player_aliases (
  player_id  int NOT NULL REFERENCES players(id),
  alias      text NOT NULL,
  PRIMARY KEY (player_id, alias)
);

CREATE TABLE orgs (                     -- stable across rebrands
  id              serial PRIMARY KEY,
  name            text NOT NULL,        -- 'OpTic'
  liquipedia_page text UNIQUE
);

CREATE TABLE teams (                    -- a branded team identity in time
  id              serial PRIMARY KEY,
  org_id          int REFERENCES orgs(id),
  name            text NOT NULL,        -- 'OpTic Texas', 'OpTic Gaming'
  region          text,
  active_from     date,
  active_to       date,
  liquipedia_page text
);

CREATE TABLE roster_stints (            -- temporal membership
  id         serial PRIMARY KEY,
  player_id  int NOT NULL REFERENCES players(id),
  team_id    int NOT NULL REFERENCES teams(id),
  role       text,
  start_date date NOT NULL,
  end_date   date,                      -- NULL = current
  source     text
);

-- ===== Competition hierarchy =====
CREATE TABLE seasons (
  id        serial PRIMARY KEY,
  year      int NOT NULL,               -- label year, e.g. 2026
  title_id  smallint NOT NULL REFERENCES titles(id),
  league    text NOT NULL,              -- 'CDL','CWL','MLG Pro League',...
  UNIQUE (year, title_id, league)
);

CREATE TABLE events (
  id              serial PRIMARY KEY,
  season_id       int REFERENCES seasons(id),
  name            text NOT NULL,
  tier            text,                 -- Liquipedia tier: 'S','A','B','C'
  start_date      date,
  end_date        date,
  location        text,
  is_lan          boolean,
  prize_pool      numeric,
  liquipedia_page text UNIQUE
);

CREATE TABLE stages (
  id        serial PRIMARY KEY,
  event_id  int NOT NULL REFERENCES events(id),
  name      text NOT NULL,              -- 'Group A','Winners Bracket',...
  ordinal   int
);

CREATE TABLE series (                   -- a best-of-N match
  id                  serial PRIMARY KEY,
  stage_id            int REFERENCES stages(id),
  event_id            int NOT NULL REFERENCES events(id),
  team1_id            int REFERENCES teams(id),
  team2_id            int REFERENCES teams(id),
  team1_score         smallint,
  team2_score         smallint,
  best_of             smallint,
  played_at           timestamptz,
  round_label         text,             -- 'WB R1','Grand Final'
  liquipedia_match_id text UNIQUE       -- LPDB match2 id
);

CREATE TABLE games (                    -- one map within a series
  id             serial PRIMARY KEY,
  series_id      int NOT NULL REFERENCES series(id),
  ordinal        smallint NOT NULL,     -- map 1..5
  map_id         int REFERENCES maps(id),
  mode_id        smallint REFERENCES game_modes(id),
  team1_score    smallint,
  team2_score    smallint,
  winner_team_id int REFERENCES teams(id),
  UNIQUE (series_id, ordinal)
);

-- ===== Stat lines (nullable by design) =====
CREATE TABLE game_player_stats (        -- per-map per-player box score
  game_id      int NOT NULL REFERENCES games(id),
  player_id    int NOT NULL REFERENCES players(id),
  -- team at match time, from source data; never derived from roster_stints
  team_id      int NOT NULL REFERENCES teams(id),
  kills        smallint CHECK (kills >= 0),
  deaths       smallint CHECK (deaths >= 0),
  assists      smallint CHECK (assists >= 0),
  damage       int,
  hill_time    smallint,                -- HP seconds
  first_bloods smallint,                -- SnD
  plants       smallint,                -- SnD
  defuses      smallint,                -- SnD
  ticks        smallint,                -- Control
  PRIMARY KEY (game_id, player_id)
);

CREATE TABLE event_placements (
  event_id      int NOT NULL REFERENCES events(id),
  team_id       int NOT NULL REFERENCES teams(id),
  placement_min smallint,               -- handles 'T3-4'
  placement_max smallint,
  prize         numeric,
  PRIMARY KEY (event_id, team_id)
);

-- ===== Lineage / ops =====
CREATE TABLE ingest_runs (
  id            serial PRIMARY KEY,
  started_at    timestamptz DEFAULT now(),
  kind          text,                   -- 'nightly','backfill'
  params        jsonb,
  status        text,                   -- 'success','failed','partial'
  rows_upserted jsonb,                  -- per-table counts
  notes         text
);

-- ===== Indexes =====
CREATE INDEX idx_maps_title            ON maps (title_id);
CREATE INDEX idx_teams_org             ON teams (org_id);
CREATE INDEX idx_stints_player         ON roster_stints (player_id);
CREATE INDEX idx_stints_team           ON roster_stints (team_id);
CREATE INDEX idx_events_season         ON events (season_id);
CREATE INDEX idx_stages_event          ON stages (event_id);
CREATE INDEX idx_series_event_played   ON series (event_id, played_at);
CREATE INDEX idx_series_stage          ON series (stage_id);
CREATE INDEX idx_series_team1          ON series (team1_id);
CREATE INDEX idx_series_team2          ON series (team2_id);
CREATE INDEX idx_games_series          ON games (series_id);
CREATE INDEX idx_games_map             ON games (map_id);
CREATE INDEX idx_games_mode            ON games (mode_id);
CREATE INDEX idx_gps_player            ON game_player_stats (player_id);
CREATE INDEX idx_gps_team              ON game_player_stats (team_id);
CREATE INDEX idx_placements_team       ON event_placements (team_id);
CREATE INDEX idx_players_handle_trgm   ON players USING gin (handle gin_trgm_ops);
CREATE INDEX idx_aliases_alias_trgm    ON player_aliases USING gin (alias gin_trgm_ops);
