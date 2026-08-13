-- 0018_player_skill: the box-score prior, the plus-minus, and the posterior of
-- the two.
--
-- `player_rapm` says what a player's presence was worth in score margin, and on
-- this record it says it very quietly: the season coefficients' spread is
-- smaller than their own average standard error, so taken at face value the
-- estimator does not establish that these players differ. `player_season_
-- adjusted` says what their box score looked like, which is measured precisely
-- and answers a different question. This table is where the two meet: a box
-- score fitted to predict the plus-minus, then blended with it by inverse
-- variance.
--
-- A row therefore stores three quantities rather than one, and the reason is
-- that only the three together can be argued with:
--
--   prior_mean, prior_sd   what the box score expected of this player-season,
--                          predicted from seasons strictly before it
--   coef, se               the filtered plus-minus it is blended with, copied
--                          from player_rapm so a reader can check the blend
--                          without a join to a different run
--   skill, skill_sd        the posterior, and
--   weight_prior           how much of it the prior supplied
--
-- `weight_prior` is the row that makes the table readable. Near 1 it says the
-- direct estimate contributed nothing and the rating is the box score's opinion;
-- near 0 it says the maps decided. Publishing the rating without it would let a
-- shrinkage artifact read as a measurement of a player.
--
-- **`skill` is a forward quantity and may never be computed from the smoothed
-- coefficient family.** The smoothed fit's random-walk penalty is two-sided, so
-- a season's coefficient there has already been pulled toward the season after
-- it; a rating built on that and then scored on next-season persistence is
-- scored against a target it has already seen. The scope column below records
-- which family each row was blended with, and the check constraint permits
-- exactly one of them. The enforcement that matters lives in code — the reader
-- raises rather than returning smoothed coefficients — and this constraint is
-- the copy of that rule the database can enforce on its own.
--
-- One row per (run, player, season). A player-season with no prior prediction —
-- the earliest season, which has nothing before it to train on — has no row
-- here rather than a row with nulls: absent is what it is.

CREATE TABLE player_skill (
  run_id        integer NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id     integer NOT NULL REFERENCES players(id),
  season_id     integer NOT NULL REFERENCES seasons(id),
  prior_mean    double precision NOT NULL,
  prior_sd      double precision NOT NULL,
  coef          double precision NOT NULL,
  se            double precision NOT NULL,
  skill         double precision NOT NULL,
  skill_sd      double precision NOT NULL,
  weight_prior  double precision NOT NULL,
  scope         text NOT NULL DEFAULT 'filtered',
  model         text NOT NULL,
  PRIMARY KEY (run_id, player_id, season_id),
  CONSTRAINT player_skill_forward_scope_only
    CHECK (scope = 'filtered'),
  CONSTRAINT player_skill_weight_is_a_share
    CHECK (weight_prior >= 0 AND weight_prior <= 1),
  CONSTRAINT player_skill_sds_nonnegative
    CHECK (prior_sd >= 0 AND skill_sd >= 0 AND se > 0)
);

CREATE INDEX idx_player_skill_season ON player_skill (season_id);

COMMENT ON TABLE player_skill IS
  'SKILL: the box-score prior for a player-season, the filtered plus-minus it '
  'was blended with, and the posterior of the two.';

COMMENT ON COLUMN player_skill.prior_mean IS
  'The box score''s expectation for this player-season, predicted from seasons '
  'strictly before it. Averaged over draws of the target from its own posterior.';

COMMENT ON COLUMN player_skill.prior_sd IS
  'Spread of that prediction across the target draws: what the fit does not know '
  'about this player. Not the variance the blend weights by, which is pooled.';

COMMENT ON COLUMN player_skill.weight_prior IS
  'Share of the posterior precision the prior supplied. Near 1 the rating is the '
  'box score''s opinion and the maps added nothing; near 0 the maps decided.';

COMMENT ON COLUMN player_skill.scope IS
  'The coefficient family blended with. Only filtered is permitted: a smoothed '
  'coefficient has already seen the season a forward test predicts.';

COMMENT ON COLUMN player_skill.model IS
  'Which arm of the declared ladder published this rating: ridge, random_forest '
  'or lightgbm. The comparison that chose it is stored in the run''s artifact.';
