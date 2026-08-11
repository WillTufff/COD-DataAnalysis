-- 0016_game_segments: the within-map time axis for the CDL era.
--
-- The stored Cito player-stats responses carry, per team per map, the score at
-- every hill rotation and the result of every Control and Search & Destroy
-- round. The transform discarded all of it. This is the mode-level analogue of
-- what roundwp.py does for 2017-2018 Search & Destroy, for an era the project
-- believes has no time axis at all, and it costs zero API calls: the bytes are
-- already on disk.
--
-- One row per (game, team, kind, ordinal):
--
--   hill           cumulative team score after that hill rotation
--   control_round  which team took the round, and how
--   snd_round      which team took the round, and how
--
-- Resolution is the team, never the player. Cito reports player box scores per
-- map and segments per team, so no player action can be located inside a
-- segment; anything fitted here is a team quantity or a map-level weight.
--
-- `win_type` is stored as the source reports it rather than folded into a
-- smaller vocabulary, because the distinctions are the finding: a Control round
-- won on ticks is a different event from one won on kills, and an SnD round won
-- on a defuse is a different event from one won pre-plant.

CREATE TABLE game_segments (
  game_id     integer  NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  team_id     integer  NOT NULL REFERENCES teams(id),
  kind        text     NOT NULL CHECK (kind IN ('hill', 'control_round', 'snd_round')),
  ordinal     smallint NOT NULL CHECK (ordinal >= 1),
  score       integer  CHECK (score >= 0),
  won         boolean,
  win_type    text,
  data_source text     NOT NULL CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb')),
  PRIMARY KEY (game_id, team_id, kind, ordinal)
);

COMMENT ON TABLE game_segments IS
  'Within-map segments: hill rotations and Control/SnD rounds, per team. '
  'Team-resolved only — the record locates no player inside a segment.';
COMMENT ON COLUMN game_segments.score IS
  'Hill rotations only: the team''s cumulative map score after this hill, so '
  'the per-hill gain is the first difference. NULL on round kinds.';
COMMENT ON COLUMN game_segments.win_type IS
  'How the round was decided, verbatim from the source. Control: ticks, time, '
  'kills. SnD: kills, pre_plant_kills, post_plant_kills, bomb_defuse, '
  'bomb_explosion, time. NULL where the source did not say.';

CREATE INDEX game_segments_kind_idx ON game_segments (kind, game_id);
