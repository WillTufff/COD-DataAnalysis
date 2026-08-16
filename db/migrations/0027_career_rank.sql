-- 0027_career_rank: the all-time ranking engine's own output tables.
--
-- Two grains, matching the two the engine itself publishes. `player_season_rank`
-- is the breadth score (award credit already folded in) plus the two team-
-- strength context numbers, one row per player per season. `player_career_rank`
-- is the peak/best-three/total blend over those season scores, one row per
-- player. Both are keyed on `run_id` like every other model output here, so a
-- rerun is a new row set rather than an update in place.
--
-- `total_sd` and `sd` are nullable for the same reason `player_career.total_sd`
-- is: a season with no computable disagreement estimate withdraws the
-- interval rather than the total pretending to a precision it does not have.

CREATE TABLE player_season_rank (
  run_id             integer NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id          integer NOT NULL REFERENCES players(id),
  season_id          integer NOT NULL REFERENCES seasons(id),
  score              double precision NOT NULL,
  sd                 double precision,
  net_of_teammates   double precision,
  opponent_strength  double precision,
  PRIMARY KEY (run_id, player_id, season_id)
);

CREATE INDEX idx_player_season_rank_player ON player_season_rank (player_id);

CREATE TABLE player_career_rank (
  run_id                       integer NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id                    integer NOT NULL REFERENCES players(id),
  qualified                    boolean NOT NULL,
  n_seasons                    integer NOT NULL,
  total                        double precision NOT NULL,
  total_sd                     double precision,
  peak                         double precision NOT NULL,
  peak_season_id               integer NOT NULL REFERENCES seasons(id),
  best_three                   double precision,
  best_three_start_season_id   integer REFERENCES seasons(id),
  PRIMARY KEY (run_id, player_id)
);

CREATE INDEX idx_player_career_rank_total ON player_career_rank (run_id, total DESC)
  WHERE qualified;

COMMENT ON COLUMN player_career_rank.qualified IS
  'Clears the minimum-seasons floor (career_rank.evalpop.MIN_SEASONS). A '
  'below-floor player still gets season rows; the site should not rank them.';

COMMENT ON TABLE player_season_rank IS
  'career_rank engine output: one breadth score per player-season, award '
  'credit already added and capped at 100. See ai/career-rank-preregistration.md.';

COMMENT ON TABLE player_career_rank IS
  'career_rank engine output: peak/best-three/total over player_season_rank. '
  'See ai/career-rank-preregistration.md.';
