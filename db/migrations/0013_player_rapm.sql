-- 0013_player_rapm: RAPM as a joinable per-player quantity.
--
-- The model has been fit since the ratings rebuild, but the only thing stored
-- was its artifact, and that artifact publishes `leaders` and `trailers` --
-- the top and bottom forty coefficients. That is a leaderboard, and a
-- leaderboard cannot answer "what is this player's RAPM", which is the
-- question a player page asks. The model fits 196 players and the artifact
-- names 80 of them, so 116 could not be looked up at all. The one number on
-- this site that does not come from the box score was the one number no player
-- page could show.
--
-- What the table stores is mostly not significant, and that is the point of
-- storing it rather than a top-forty. On the current archive 7 of the 196
-- coefficients exceed 1.96 SE; the median SE is 0.53 against a coefficient SD
-- of 0.423, so the penalty is larger than the signal for nearly everyone, and
-- 86 players sit at a teammate concentration of 0.9 or above. A reader who
-- sees only the extremes of that distribution is being shown the tail of a
-- ridge prior and invited to read it as a ranking. Anything rendering this
-- column has to carry se with it.
--
-- The row is per (run, player), not per season. rapm.fit() estimates one
-- coefficient per player over every decided map in the archive; the per-season
-- variant in rapm.prefix() exists to feed the roster forecast and drops
-- min_maps to 1 for that purpose, which is deliberately below anything
-- publishable. Storing the seasonal one here would be storing the model's
-- working, not its opinion.
--
-- Nothing is centred and nothing is rescaled: a coefficient is already in
-- map-win log-odds, holding the other seven players on the server constant.
CREATE TABLE player_rapm (
  run_id     int  NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id  int  NOT NULL REFERENCES players(id),
  maps       int  NOT NULL,
  coef       real NOT NULL,   -- map-win log-odds contribution
  se         real NOT NULL,   -- from the penalized Hessian, not a bootstrap
  -- Share of this player's maps played alongside their most frequent teammate.
  -- Not a footnote: four players who never play apart are one column wearing
  -- four names, and ridge splits the credit evenly between them. At 1.0 the
  -- coefficient is the team's.
  teammate_concentration real NOT NULL,
  PRIMARY KEY (run_id, player_id),
  CONSTRAINT player_rapm_maps_positive CHECK (maps > 0),
  CONSTRAINT player_rapm_se_positive CHECK (se > 0),
  CONSTRAINT player_rapm_coef_finite
    CHECK (coef <> 'NaN'::real AND coef <> 'Infinity'::real AND coef <> '-Infinity'::real),
  CONSTRAINT player_rapm_concentration_range
    CHECK (teammate_concentration >= 0 AND teammate_concentration <= 1)
);

CREATE INDEX idx_player_rapm_player ON player_rapm (player_id);

COMMENT ON TABLE player_rapm IS
  'Regularized adjusted plus-minus, one row per player per rating run, fit over '
  'every decided map in the archive. Contains only players clearing '
  'rapm.MIN_MAPS. See /methodology#rapm.';

COMMENT ON COLUMN player_rapm.coef IS
  'Estimated contribution to the log-odds of winning a map, holding the other '
  'seven players constant. Nothing from the box score enters the fit. Most '
  'coefficients are dominated by the ridge penalty -- read against se, never '
  'alone.';

COMMENT ON COLUMN player_rapm.teammate_concentration IS
  'Share of the player''s maps spent alongside their most frequent teammate. '
  'Above ~0.9 the coefficient cannot be separated from that teammate''s and is '
  'substantially the team''s number, not the player''s.';
