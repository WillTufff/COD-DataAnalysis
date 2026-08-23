-- 0039_career_rank_blend: the career blend, and the sum it replaces.
--
-- `total` was a plain sum of season scores, so the board ranked on
-- performance alone and the finish and award components sat beside it
-- unweighted. It is now the blend: PEAK 20, PRIME 25, LONGEVITY 20, RESUME 25
-- and ACCOLADE 10, each component min-max scaled across the qualified cohort
-- first so the weights are shares of one comparable scale.
--
-- The old sum keeps its own column rather than its old name. `total_sd` and
-- `mean_season` are both defined against the sum and describe nothing about
-- the blend, so they stay attached to `season_total` and nothing already on a
-- page changes meaning under the name it had.
--
-- `career_components` holds the five as the blend saw them, scaled. A
-- component missing from it is one the archive cannot see for that career —
-- an award axis for a player who finished before 2016 named a season honour —
-- and the weights renormalize over what is left. Never winning an award is a
-- zero and is present.

ALTER TABLE player_career_rank
  ADD COLUMN season_total      double precision,
  ADD COLUMN longevity         double precision,
  ADD COLUMN resume_total      double precision,
  ADD COLUMN accolade_total    double precision,
  ADD COLUMN career_components jsonb;

COMMENT ON COLUMN player_career_rank.total IS
  'The career blend: the weighted mean of the scaled components this career''s '
  'coverage reaches, PEAK 20 / PRIME 25 / LONGEVITY 20 / RESUME 25 / '
  'ACCOLADE 10. This is what the board ranks on. On a run made before '
  'migration 0039 it is the plain sum of season scores.';

COMMENT ON COLUMN player_career_rank.season_total IS
  'The plain sum of season scores, which is what `total` was until the blend '
  'existed. `total_sd` and `mean_season` are defined against this column and '
  'not against `total`.';

COMMENT ON COLUMN player_career_rank.longevity IS
  'Sum over every scorable season of the season score above that season''s '
  'replacement level, the minimum among players with at least eight maps, '
  'taken over the whole archive. A season under the map floor contributes '
  'zero rather than a negative.';

COMMENT ON COLUMN player_career_rank.resume_total IS
  'The career''s finish credit: the sum of every season''s share of the credit '
  'its year could be won.';

COMMENT ON COLUMN player_career_rank.accolade_total IS
  'The career''s award credit: the sum of every season''s share of the award '
  'points its year handed out.';

COMMENT ON COLUMN player_career_rank.career_components IS
  'The five blend components scaled 0..100 across the qualified cohort. A '
  'component the archive cannot see for this career is absent and its weight '
  'renormalizes onto the rest; a component seen and not earned is zero.';
