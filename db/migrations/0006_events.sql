-- 0006_events: the structured event tier (kill feed + round scores).
-- The box-score spine (0001) carries per-map totals; this migration adds the
-- per-event grain underneath it, sourced from the structured JSON tarballs.
-- Only Infinite Warfare (2017) and WWII (2018) have a feed; BO4 (2019) ships
-- box scores with empty event lists, so these tables simply have no rows for it.
--
-- Two death shapes are normalized into one row here:
--   WWII  nested attacker object, 2D positions, times already in ms
--   IW    flat attacker_* fields, 3D positions (z), kill_distance, weapon_class
-- Time is stored normalized to milliseconds; the importer converts IW's raw
-- units. IW-only columns (kill_distance, weapon_class, killer_z/victim_z) are
-- NULL for WWII.

-- ===== Join key from the structured feed onto the box-score spine =====
-- The feed identifies a game by its own id; source_uid carries that id so the
-- importer can join events to games. Nullable + backfilled in the same step
-- that begins populating it (loader is idempotent), UNIQUE so a feed id maps to
-- exactly one game.
ALTER TABLE games ADD COLUMN source_uid text;
ALTER TABLE games ADD CONSTRAINT games_source_uid_key UNIQUE (source_uid);

-- ===== Per-round scores (from roundstart/roundend) =====
-- The feed hands us round-boundary scores directly, so round winners come from
-- score deltas rather than being inferred from death-clock resets. score1/score2
-- are the cumulative scoreboard as of round end (hill ticks for HP, round wins
-- for SnD, etc.); winner_side is the importer's delta call, NULL if degenerate.
CREATE TABLE game_rounds (
  game_id       int      NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  round         smallint NOT NULL CHECK (round >= 1),
  score1        int,                                    -- team1 cumulative at round end
  score2        int,                                    -- team2 cumulative at round end
  start_time_ms int  CHECK (start_time_ms >= 0),        -- roundstart, normalized ms
  end_time_ms   int  CHECK (end_time_ms   >= 0),        -- roundend,   normalized ms
  winner_side   smallint CHECK (winner_side IN (1, 2)), -- from score delta; NULL if tie/unknown
  PRIMARY KEY (game_id, round)
);

-- ===== The kill feed =====
-- One row per death event. killer_id is NULL for suicides / world deaths where
-- the feed carries no attacker. Raw handles are kept alongside the resolved ids
-- so the IW reconciliation residual can be diagnosed by handle, not just count.
CREATE TABLE kill_events (
  id            bigserial PRIMARY KEY,
  game_id       int      NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  round         smallint NOT NULL CHECK (round >= 1),
  seq           int      NOT NULL,                      -- order within the game's feed (0-based)
  time_ms       int      CHECK (time_ms >= 0),          -- game clock, normalized ms
  round_time_ms int      CHECK (round_time_ms >= 0),    -- clock within the round, normalized ms

  -- participants (handles resolved through the shared alias table at import)
  victim_id     int      NOT NULL REFERENCES players(id),
  killer_id     int      REFERENCES players(id),         -- NULL: suicide / world
  victim_handle text     NOT NULL,                       -- raw feed handle, for audit
  killer_handle text,                                     -- raw feed handle, NULL when no attacker

  -- life index: per-player spawn counter, the key to exact alive-counts
  victim_life   smallint CHECK (victim_life >= 0),
  killer_life   smallint CHECK (killer_life >= 0),

  -- classification, established during reconciliation:
  --   normal   counts against the box death total
  --   suicide  attacker absent or self
  --   teamkill attacker on the victim's team
  death_kind    text NOT NULL DEFAULT 'normal'
                  CHECK (death_kind IN ('normal', 'suicide', 'teamkill')),

  -- weapon / cause
  weapon         text,
  means_of_death text,
  weapon_class   text,   -- IW only (attacker_weapon_class); NULL for WWII
  kill_distance  real CHECK (kill_distance IS NULL OR kill_distance >= 0), -- IW only

  -- positions: x/y both titles, z (kz/vz) IW only. Feed-space units, kept as-is
  -- for schematic kill-density; no real-world scale is claimed.
  victim_x  real,
  victim_y  real,
  victim_z  real,   -- IW only
  killer_x  real,
  killer_y  real,
  killer_z  real,   -- IW only

  UNIQUE (game_id, seq)
);

CREATE INDEX idx_kill_events_game   ON kill_events (game_id);
CREATE INDEX idx_kill_events_victim ON kill_events (victim_id);
CREATE INDEX idx_kill_events_killer ON kill_events (killer_id);
-- reconciliation and per-player-map metrics group by (game, victim, kind)
CREATE INDEX idx_kill_events_recon  ON kill_events (game_id, victim_id, death_kind);
