-- 0011_rating_posterior: rating_sd stops being a bootstrap and becomes a posterior.
--
-- No column changes. The composite rating is now the posterior mean of a
-- two-level normal-normal model (players within season x mode) rather than a
-- z-score multiplied by m/(m+k), and rating_sd is that posterior's standard
-- deviation instead of the spread of 200 resampled point estimates.
--
-- Those are different quantities and the difference is not cosmetic. Resampling
-- a shrunk estimate measures how far it would move on other maps, B*sqrt(v); the
-- posterior SD measures what is still unknown about the player after pooling,
-- sqrt(B*v), which is larger by 1/sqrt(B) -- about a factor of two across this
-- archive, and more on short seasons. Anything drawing a band from this column
-- was drawing one too tight, and a stored number whose meaning changed silently
-- would be worse than one that never existed, hence this migration.
--
-- Two consequences for readers of the table:
--   * per-mode rows (mode_id IS NOT NULL) now carry rating_sd as well. Under the
--     old estimator the bootstrap only existed for the all-mode blend, so those
--     rows were always NULL.
--   * rating is still centred so the qualified cohort averages 1.00 and a rating
--     point is still 0.15, but the unit underneath is now tau, the estimated
--     spread of true player skill, not the observed spread of season scores.
COMMENT ON COLUMN player_season_adjusted.rating IS
  'Composite player rating: posterior mean of the two-level normal-normal model, '
  'centred so the qualified cohort averages 1.00, scaled so one point is 0.15 of '
  'the cohort''s estimated true-skill SD. See /methodology#player-rating.';

COMMENT ON COLUMN player_season_adjusted.rating_sd IS
  'Posterior SD of rating, in the same units: sqrt(B*v) where B is the shrinkage '
  'the model applied and v the season score''s sampling variance. Not a bootstrap '
  'of the point estimate, which is smaller by sqrt(B). Populated for per-mode rows '
  'as well as the all-mode blend.';
