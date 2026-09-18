-- 0040_career_rank_net_of_teammates: a career-level display figure.
--
-- `player_season_rank.net_of_teammates` already carries the per-season
-- figure. This adds the career's plain mean over those seasons, for display
-- only: it never enters `total` or any weighted component.

ALTER TABLE player_career_rank
  ADD COLUMN net_of_teammates_mean double precision;

COMMENT ON COLUMN player_career_rank.net_of_teammates_mean IS
  'Plain unweighted mean of the career''s season net_of_teammates values. '
  'Display only: not a component of total and carries no weight.';
