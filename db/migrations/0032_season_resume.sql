-- 0032_season_resume: what a player's finishes were worth, beside how he played.
--
-- `player_season_rank.score` measures performance. `resume` measures what the
-- team finished: the placement curve in `career_rank/resume.py` times the
-- square root of the event's prize pool, summed over the title events the
-- player was on the roster for, divided by the credit a team winning every
-- title event that year would have earned. `resume_credit` is that sum before
-- the division, kept because the denominator is a property of the year and a
-- reader re-normalising over a different set needs the numerator back.
--
-- Chips and rings sit on the career row as plain integers, the way readers
-- count them, with `rings_covered_from`: the earliest year every title win
-- reaches a roster. Without that year a zero cannot be told apart from an
-- unloaded season.

ALTER TABLE player_season_rank
  ADD COLUMN resume        double precision,
  ADD COLUMN resume_credit double precision;

ALTER TABLE player_career_rank
  ADD COLUMN chips              integer,
  ADD COLUMN rings              integer,
  ADD COLUMN rings_covered_from integer;

COMMENT ON COLUMN player_season_rank.resume IS
  'Season finish credit as a share of the year''s winnable credit, 0..1. '
  'Placement curve x sqrt(prize pool) over title events, per career_rank.resume.';

COMMENT ON COLUMN player_season_rank.resume_credit IS
  'The same sum before the per-year division, in weighted curve units.';

COMMENT ON COLUMN player_career_rank.chips IS
  'Title wins over the career: any title event, from a CWL open to a Major.';

COMMENT ON COLUMN player_career_rank.rings IS
  'World championships only: Call of Duty, CWL and CDL Championships.';

COMMENT ON COLUMN player_career_rank.rings_covered_from IS
  'Earliest year every title win reaches an event roster. A chip or ring count '
  'is a career total only from this year forward; null means no year is covered.';
