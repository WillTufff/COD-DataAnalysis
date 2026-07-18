-- 0002_gps_extras: additions for the CWL 2017-2019 archive import
-- (Activision cwl-data, BSD-3). The archive carries per-map stats the core
-- columns don't cover (ekia, damage, accuracy, time alive, CTF/Uplink/Control
-- objective detail, streaks). Rather than widen game_player_stats with dozens
-- of era-specific columns, bonus stats land in one jsonb column; the mapped
-- basics stay in typed columns.
--
-- games gains duration + end time: the archive records both per map, and pace
-- metrics (kills per 10 min, hill-time share) need duration to be honest.

ALTER TABLE game_player_stats ADD COLUMN extras jsonb;

ALTER TABLE games ADD COLUMN duration_s int CHECK (duration_s > 0);
ALTER TABLE games ADD COLUMN ended_at timestamptz;

CREATE INDEX idx_gps_extras ON game_player_stats USING gin (extras);
