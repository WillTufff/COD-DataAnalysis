-- 0012_player_style: continuous style axes per player-season.
--
-- 0008 dropped player_archetypes. That table had been declared in 0003 on the
-- assumption that clustering the metric layer would produce a role taxonomy;
-- no clustering was ever written, so an empty table sat there implying a model
-- that did not exist, and publishing rule 2 said it had to go.
--
-- The model now exists, and it says there is no taxonomy. On the 21 box-score
-- metrics every season in this archive can reach, across 484 of 487 qualified
-- player-seasons, the gap statistic prefers a single cluster over every
-- partition from two to seven; the best-separated partition (k=2) scores a
-- silhouette of 0.286 where a single Gaussian of the same shape and size scores
-- 0.251 to 0.305. Bootstrap stability is high at k=2 and is not evidence: an
-- unclustered cloud bisects just as reproducibly. See /methodology#player-style.
--
-- So player_archetypes is deliberately NOT recreated. What replaces it is this
-- table: the four components Horn's parallel analysis retains from the
-- quality-residualised metric vector, stored as a position rather than a label.
-- `axis` is 1-based and its meaning is fixed by the run's player_style artifact,
-- which carries the loadings; scores are signed so that the largest loading on
-- each axis is positive, and rerunning cannot silently flip a career's sign.
--
-- The row is per (player, season) because the metric layer is: a player's
-- position moves between seasons, and that movement is the point.
CREATE TABLE player_style_season (
  run_id     int  NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id  int  NOT NULL REFERENCES players(id),
  season_id  int  NOT NULL REFERENCES seasons(id),
  axis       smallint NOT NULL,   -- 1-based; named in the run's artifact
  score      real NOT NULL,       -- component score, quality already removed
  pctl       real NOT NULL,       -- 0..1 within the fitted cohort
  PRIMARY KEY (run_id, player_id, season_id, axis),
  CONSTRAINT player_style_axis_positive CHECK (axis >= 1),
  CONSTRAINT player_style_score_finite
    CHECK (score <> 'NaN'::real AND score <> 'Infinity'::real AND score <> '-Infinity'::real),
  CONSTRAINT player_style_pctl_range CHECK (pctl >= 0 AND pctl <= 1)
);

CREATE INDEX idx_pss_player ON player_style_season (player_id);
CREATE INDEX idx_pss_axis ON player_style_season (run_id, axis);

COMMENT ON TABLE player_style_season IS
  'Per player-season position on the retained style axes. Not a cluster label: '
  'no archetype partition in this archive beats a cloud with no clusters in it. '
  'See /methodology#player-style.';

COMMENT ON COLUMN player_style_season.score IS
  'Component score on the quality-residualised metric vector, so it describes '
  'how a player played at their own level rather than what level that was. The '
  'composite rating explains 11.7% of the variance these axes are fitted to.';
