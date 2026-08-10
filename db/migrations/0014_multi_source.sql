-- 0014_multi_source: schema deltas for the multi-provider ingest
-- (CWL archive + Cito/Breaking Point + LPDB).
--
-- data_source makes row provenance explicit on every table whose licensing
-- obligations differ by source: attribution renders from the tag and the
-- "derived analytics only" guard for Cito rows becomes a WHERE clause instead
-- of season-year inference. Rows predating this migration are all from the
-- CWL archive import, so the backfill is a constant.

-- ===== Provenance =====
ALTER TABLE series ADD COLUMN data_source text
  CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb'));
ALTER TABLE games ADD COLUMN data_source text
  CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb'));
ALTER TABLE game_player_stats ADD COLUMN data_source text
  CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb'));
ALTER TABLE event_placements ADD COLUMN data_source text
  CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb'));

UPDATE series            SET data_source = 'cwl_archive' WHERE data_source IS NULL;
UPDATE games             SET data_source = 'cwl_archive' WHERE data_source IS NULL;
UPDATE game_player_stats SET data_source = 'cwl_archive' WHERE data_source IS NULL;
UPDATE event_placements  SET data_source = 'cwl_archive' WHERE data_source IS NULL;

ALTER TABLE series            ALTER COLUMN data_source SET NOT NULL;
ALTER TABLE games             ALTER COLUMN data_source SET NOT NULL;
ALTER TABLE game_player_stats ALTER COLUMN data_source SET NOT NULL;
ALTER TABLE event_placements  ALTER COLUMN data_source SET NOT NULL;

-- ===== Series source key =====
-- source_uid is the provider-native series id (Cito matchId, or the archive's
-- synthetic key) and the idempotent-upsert conflict target for non-LPDB
-- sources. liquipedia_match_id is reserved for real LPDB match2 ids from here
-- on; the archive importer's synthetic keys move over.
ALTER TABLE series ADD COLUMN source_uid text;
ALTER TABLE series ADD CONSTRAINT series_source_uid_key UNIQUE (source_uid);
UPDATE series SET source_uid = liquipedia_match_id, liquipedia_match_id = NULL
  WHERE liquipedia_match_id LIKE 'cwl-archive:%';

-- ===== Cito stat set on game_player_stats =====
-- The Cito payload is richer than the archive's typed columns: clutch detail,
-- contested hill time, non-traded kills, SnD round counts, Control round/zone
-- detail. These are stable, queryable inputs to the metric layer, so they get
-- typed columns; provider-proprietary extras (e.g. Cito's own rating) stay in
-- the existing jsonb extras column.
ALTER TABLE game_player_stats ADD COLUMN contested_hill_time smallint CHECK (contested_hill_time >= 0);
ALTER TABLE game_player_stats ADD COLUMN first_deaths        smallint CHECK (first_deaths >= 0);
ALTER TABLE game_player_stats ADD COLUMN captures            smallint CHECK (captures >= 0);
ALTER TABLE game_player_stats ADD COLUMN highest_streak      smallint CHECK (highest_streak >= 0);
ALTER TABLE game_player_stats ADD COLUMN non_traded_kills    smallint CHECK (non_traded_kills >= 0);
ALTER TABLE game_player_stats ADD COLUMN snd_rounds          smallint CHECK (snd_rounds >= 0);
ALTER TABLE game_player_stats ADD COLUMN clutch_1v1          smallint CHECK (clutch_1v1 >= 0);
ALTER TABLE game_player_stats ADD COLUMN clutch_1v2          smallint CHECK (clutch_1v2 >= 0);
ALTER TABLE game_player_stats ADD COLUMN clutch_1v3          smallint CHECK (clutch_1v3 >= 0);
ALTER TABLE game_player_stats ADD COLUMN clutch_1v4          smallint CHECK (clutch_1v4 >= 0);
ALTER TABLE game_player_stats ADD COLUMN ctl_attack_rounds   smallint CHECK (ctl_attack_rounds >= 0);
ALTER TABLE game_player_stats ADD COLUMN ctl_defense_rounds  smallint CHECK (ctl_defense_rounds >= 0);
ALTER TABLE game_player_stats ADD COLUMN ctl_zone_captures   smallint CHECK (ctl_zone_captures >= 0);

-- ===== LPDB-fed extensions (populated by the P2/P4 pulls) =====
ALTER TABLE teams ADD COLUMN earnings     numeric;
ALTER TABLE teams ADD COLUMN create_date  date;
ALTER TABLE teams ADD COLUMN disband_date date;

ALTER TABLE players ADD COLUMN earnings         numeric;
ALTER TABLE players ADD COLUMN earnings_by_year jsonb;

ALTER TABLE events ADD COLUMN tier_type      text;
ALTER TABLE events ADD COLUMN publisher_tier text;
ALTER TABLE events ADD COLUMN format         text;

ALTER TABLE event_placements ADD COLUMN individual_prize numeric;

-- Dated roster-change log from LPDB /transfer.
CREATE TABLE transfers (
  id            serial PRIMARY KEY,
  player_id     int  NOT NULL REFERENCES players(id),
  transfer_date date NOT NULL,
  from_team_id  int  REFERENCES teams(id),
  to_team_id    int  REFERENCES teams(id),
  role          text,
  platform      text,
  reference     text,                   -- provider-native id / page
  data_source   text NOT NULL CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb'))
);
CREATE INDEX idx_transfers_player ON transfers (player_id, transfer_date);
CREATE INDEX idx_transfers_date   ON transfers (transfer_date);
