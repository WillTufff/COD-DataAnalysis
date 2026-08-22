-- 0036_career_rank_mean_season: the per-season average, beside the sum.
--
-- `total` is a sum over the seasons the board can score, so a long career
-- outranks a better short one on attendance alone. That is a property of the
-- blend and not a defect in it, but nothing published let a reader see it. The
-- mean is the same number divided by `seasons_covered`, and the two read
-- together say how much of a total is rate and how much is length.
--
-- It is published and nothing ranks on it. The career blend is R7's work.

ALTER TABLE player_career_rank ADD COLUMN mean_season double precision;

COMMENT ON COLUMN player_career_rank.mean_season IS
  'total divided by the seasons that carry a score. Published beside total so '
  'the attendance in a total is visible; nothing ranks on it.';
