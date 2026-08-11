-- 0015_cwl_extras_promotion: the CWL archive's most-read extras become columns.
--
-- The archive lands every unmapped measured stat in the jsonb `extras` bag.
-- That is the right default for a wide, year-varying CSV, and the wrong home
-- for the dozen keys that every fit reads on every run: each one costs a jsonb
-- traversal and a text-to-number cast per player-map, against 44,552 rows.
--
-- `fave_weapon` is the reason this migration exists rather than the accuracy
-- pair. It is a string, so nothing that reads a numeric extras key can see it
-- at all, and it is the only observed role label anywhere in the record — the
-- player's primary weapon on that map, on 44,182 of 44,552 rows, across 27
-- weapons that map onto SMG/AR/sniper without ambiguity.
--
-- Promoted keys are removed from `extras` in the same statement. Two copies of
-- one quantity is how they come to disagree.
--
-- Cito rows keep NULL on all of these: the CDL source reports none of them,
-- and a zero would claim it reported zero.

ALTER TABLE game_player_stats ADD COLUMN headshots     smallint CHECK (headshots >= 0);
ALTER TABLE game_player_stats ADD COLUMN suicides      smallint CHECK (suicides >= 0);
ALTER TABLE game_player_stats ADD COLUMN team_kills    smallint CHECK (team_kills >= 0);
ALTER TABLE game_player_stats ADD COLUMN hits          smallint CHECK (hits >= 0);
ALTER TABLE game_player_stats ADD COLUMN shots         smallint CHECK (shots >= 0);
ALTER TABLE game_player_stats ADD COLUMN hill_captures smallint CHECK (hill_captures >= 0);
ALTER TABLE game_player_stats ADD COLUMN hill_defends  smallint CHECK (hill_defends >= 0);
ALTER TABLE game_player_stats ADD COLUMN bomb_pickups  smallint CHECK (bomb_pickups >= 0);
ALTER TABLE game_player_stats ADD COLUMN multikill_2   smallint CHECK (multikill_2 >= 0);
ALTER TABLE game_player_stats ADD COLUMN multikill_3   smallint CHECK (multikill_3 >= 0);
ALTER TABLE game_player_stats ADD COLUMN multikill_4   smallint CHECK (multikill_4 >= 0);
ALTER TABLE game_player_stats ADD COLUMN fave_weapon   text;

COMMENT ON COLUMN game_player_stats.team_kills IS
  'Friendly-fire kills this player committed on this map. Not the team''s kill '
  'total, which is a windowed sum over the map''s rows.';
COMMENT ON COLUMN game_player_stats.fave_weapon IS
  'The player''s primary weapon on this map, as reported by the CWL archive. '
  'The only observed role label in the record; NULL outside 2017-2019.';
COMMENT ON COLUMN game_player_stats.shots IS
  'Shots fired. With hits, the accuracy pair: the archive publishes a derived '
  'accuracy percentage that is dropped at parse in favour of the two counts.';

UPDATE game_player_stats SET
  headshots     = (extras->>'headshots')::smallint,
  suicides      = (extras->>'suicides')::smallint,
  team_kills    = (extras->>'team_kills')::smallint,
  hits          = (extras->>'hits')::smallint,
  shots         = (extras->>'shots')::smallint,
  hill_captures = (extras->>'hill_captures')::smallint,
  hill_defends  = (extras->>'hill_defends')::smallint,
  bomb_pickups  = (extras->>'bomb_pickups')::smallint,
  multikill_2   = (extras->>'2_piece')::smallint,
  multikill_3   = (extras->>'3_piece')::smallint,
  multikill_4   = (extras->>'4_piece')::smallint,
  fave_weapon   = extras->>'fave_weapon',
  -- snd_rounds already exists as a column for the Cito era and is NULL on
  -- every archive row; the archive has been reporting it in extras all along.
  snd_rounds    = COALESCE(snd_rounds, (extras->>'snd_rounds')::smallint)
WHERE extras IS NOT NULL;

UPDATE game_player_stats
   SET extras = extras - 'headshots' - 'suicides' - 'team_kills' - 'hits' - 'shots'
                       - 'hill_captures' - 'hill_defends' - 'bomb_pickups'
                       - '2_piece' - '3_piece' - '4_piece' - 'fave_weapon' - 'snd_rounds'
 WHERE extras IS NOT NULL;

UPDATE game_player_stats SET extras = NULL WHERE extras = '{}'::jsonb;
