-- The Call of Duty Esports Wiki as a fourth data source, covering 2013-2016.
--
-- Source precedence: codwiki never overwrites cwl_archive or cito. Those two
-- carry publisher and operator records; codwiki carries community
-- transcriptions of broadcast scoreboards. The loader inserts a row only where
-- no row exists, and reports every disagreement instead of applying it.
--
-- The load window is 2013-01-01 through 2016-12-31. Rows dated November or
-- December 2016 belong to the Infinite Warfare title and the 2017 season, the
-- same rule the rest of the pipeline follows for a title that ships late in a
-- calendar year.

BEGIN;

ALTER TABLE series DROP CONSTRAINT series_data_source_check;
ALTER TABLE series ADD CONSTRAINT series_data_source_check
  CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb', 'codwiki'));

ALTER TABLE games DROP CONSTRAINT games_data_source_check;
ALTER TABLE games ADD CONSTRAINT games_data_source_check
  CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb', 'codwiki'));

ALTER TABLE game_player_stats DROP CONSTRAINT game_player_stats_data_source_check;
ALTER TABLE game_player_stats ADD CONSTRAINT game_player_stats_data_source_check
  CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb', 'codwiki'));

ALTER TABLE event_placements DROP CONSTRAINT event_placements_data_source_check;
ALTER TABLE event_placements ADD CONSTRAINT event_placements_data_source_check
  CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb', 'codwiki'));

ALTER TABLE player_awards DROP CONSTRAINT player_awards_data_source_check;
ALTER TABLE player_awards ADD CONSTRAINT player_awards_data_source_check
  CHECK (data_source IN ('lpdb', 'codwiki'));

-- Four titles and their seasons. release_year is the year the title shipped,
-- one before the season it is played in, matching the existing rows.
INSERT INTO titles (name, short_name, release_year, era) VALUES
  ('Black Ops 2',       'BO2', 2012, 'mlg'),
  ('Ghosts',            'GHO', 2013, 'mlg'),
  ('Advanced Warfare',  'AW',  2014, 'mlg'),
  ('Black Ops 3',       'BO3', 2015, 'cwl')
ON CONFLICT (name) DO NOTHING;

INSERT INTO seasons (year, title_id, league)
SELECT y.year, t.id, y.league
FROM (VALUES
  (2013, 'Black Ops 2',      'MLG'),
  (2014, 'Ghosts',           'MLG'),
  (2015, 'Advanced Warfare', 'MLG'),
  (2016, 'Black Ops 3',      'CWL')
) AS y(year, title, league)
JOIN titles t ON t.name = y.title
ON CONFLICT (year, title_id, league) DO NOTHING;

-- Blitz is the one mode in the load window with no row. The window also holds
-- 40 rows tagged `Unrecognized Mode`, which get no mode and are quarantined.
INSERT INTO game_modes (name, slug) VALUES ('Blitz', 'blitz')
ON CONFLICT (slug) DO NOTHING;

COMMIT;
