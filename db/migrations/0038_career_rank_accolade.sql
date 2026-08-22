-- 0038_career_rank_accolade: award credit leaves the season score.
--
-- Award credit used to be added to `player_season_rank.score` and capped at
-- 100, which made a first-team vote a performance measurement. It is now
-- ACCOLADE, a season component of its own beside RESUME: built per season,
-- published here, and carrying no weight until the career blend fixes one.
--
-- `accolade` is the season's share of every award point its year handed out,
-- and `accolade_credit` is that share's numerator, kept for the same reason
-- `resume_credit` is — the denominator is a property of the year and a reader
-- re-normalising over a different set needs the numerator back. A year that
-- named no season-level honour normalises to nothing rather than dividing by
-- one award; measured on the archive of 2026-08-22 that is 2013, 2014 and
-- 2015, whose whole record is five event MVPs.
--
-- The two table comments 0027 and 0030 wrote are false from this migration
-- forward and are corrected here.

ALTER TABLE player_season_rank
  ADD COLUMN accolade        double precision,
  ADD COLUMN accolade_credit double precision,
  ADD COLUMN awards_present  text[];

COMMENT ON COLUMN player_season_rank.accolade IS
  'Award credit as a share of every award point that year, 0..1. Zero where '
  'the year named no season-level honour and so has nothing to normalise '
  'against. Null on a run made before the column existed.';

COMMENT ON COLUMN player_season_rank.accolade_credit IS
  'The raw tier points behind `accolade`, before the per-year division: 8 for '
  'a top-tier honour, 4 for a second-tier one, 4 for a rookie of the year, '
  'capped per tier and additive across tiers.';

COMMENT ON COLUMN player_season_rank.awards_present IS
  'The awards that season held, as the source names them. An award row the '
  'record cannot attach to a player is unresolved and appears on no season.';

COMMENT ON TABLE player_season_rank IS
  'career_rank engine output: one row per player-season, carrying whichever '
  'components that season has. `score` is the performance component alone — '
  'shrunk breadth blended with VALUE — and carries no award credit; the '
  'finish and award components sit beside it in `resume` and `accolade`, each '
  'on its own scale and unweighted until the career blend.';
