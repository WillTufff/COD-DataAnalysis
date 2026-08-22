-- 0034_career_rank_coverage: every published row says what the archive could
-- see of it.
--
-- A career that starts before the seasons the board scores is not a short
-- career, and until now nothing on the row said so. Two grains again.
--
-- `player_season_rank.components_present` lists the components the season
-- actually had. A pre-2017 season has a finish record and no box score, and
-- the blend has to renormalize over what is there instead of scoring the
-- missing part as zero. Null means the run predates this column, which is not
-- the same claim as an empty list.
--
-- `score` loses its NOT NULL for the same reason. A season with placement
-- credit and no qualifying breadth row is a real season with a real credit,
-- and it used to be dropped on the floor rather than written with the
-- performance half left open.
--
-- On the career row, `seasons_covered` is the seasons the board could score
-- and `n_seasons` is the seasons the career has at all. They are equal for a
-- league-era career and they are not equal for a career that started in 2013,
-- which is the whole point of publishing both. `coverage_from_year` is the
-- earliest year a box score reaches the player; null means no season of his
-- carries one, and the row is a resume-only entry.

ALTER TABLE player_season_rank
  ALTER COLUMN score DROP NOT NULL,
  ADD COLUMN components_present text[];

ALTER TABLE player_career_rank
  ADD COLUMN seasons_covered    integer,
  ADD COLUMN coverage_from_year integer,
  ADD COLUMN components_present text[];

COMMENT ON COLUMN player_season_rank.score IS
  'Breadth score for the season, null when the season has no qualifying '
  'breadth row. A null score is a season the box-score archive does not '
  'reach, never a zero performance.';

COMMENT ON COLUMN player_season_rank.components_present IS
  'The components this season carried, from PERFORMANCE and RESUME. Null on '
  'a run made before the column existed.';

COMMENT ON COLUMN player_career_rank.seasons_covered IS
  'Seasons carrying a PERFORMANCE component, which is what the board ranks '
  'on. Read beside n_seasons, the seasons the career has at all.';

COMMENT ON COLUMN player_career_rank.coverage_from_year IS
  'Earliest year a box score reaches this player. Null means none does and '
  'the career is a resume-only entry.';

COMMENT ON COLUMN player_career_rank.components_present IS
  'Union of the season components across the career.';
