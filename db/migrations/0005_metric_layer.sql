-- 0005_metric_layer: derived per-entity metrics in long form.
-- One row per (subject, season, mode, metric). mode_id NULL means the metric
-- covers all modes. Each row carries its own qualification denominator so
-- consumers can show sample size and filter without re-joining source tables.

CREATE TABLE player_metric_season (
  run_id     int  NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id  int  NOT NULL REFERENCES players(id),
  season_id  int  NOT NULL REFERENCES seasons(id),
  mode_id    smallint REFERENCES game_modes(id),
  metric     text NOT NULL,
  value      real NOT NULL,
  denom      real NOT NULL,   -- maps, rounds, kills, shots, ... per metric
  z          real,            -- vs qualified cohort; NULL if cohort degenerate
  pctl       real,            -- 0..1 within qualified cohort
  qualified  boolean NOT NULL,
  CONSTRAINT player_metric_season_value_finite
    CHECK (value <> 'NaN'::real AND value <> 'Infinity'::real AND value <> '-Infinity'::real),
  CONSTRAINT player_metric_season_denom_nonneg CHECK (denom >= 0),
  CONSTRAINT player_metric_season_pctl_range CHECK (pctl IS NULL OR (pctl >= 0 AND pctl <= 1))
);
CREATE UNIQUE INDEX uq_pms
  ON player_metric_season (run_id, player_id, season_id, COALESCE(mode_id, 0), metric);
CREATE INDEX idx_pms_metric ON player_metric_season (run_id, metric, season_id);
CREATE INDEX idx_pms_player ON player_metric_season (player_id);

CREATE TABLE team_metric_season (
  run_id     int  NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  team_id    int  NOT NULL REFERENCES teams(id),
  season_id  int  NOT NULL REFERENCES seasons(id),
  mode_id    smallint REFERENCES game_modes(id),
  metric     text NOT NULL,
  value      real NOT NULL,
  denom      real NOT NULL,
  z          real,
  pctl       real,
  qualified  boolean NOT NULL,
  CONSTRAINT team_metric_season_value_finite
    CHECK (value <> 'NaN'::real AND value <> 'Infinity'::real AND value <> '-Infinity'::real),
  CONSTRAINT team_metric_season_denom_nonneg CHECK (denom >= 0),
  CONSTRAINT team_metric_season_pctl_range CHECK (pctl IS NULL OR (pctl >= 0 AND pctl <= 1))
);
CREATE UNIQUE INDEX uq_tms
  ON team_metric_season (run_id, team_id, season_id, COALESCE(mode_id, 0), metric);
CREATE INDEX idx_tms_metric ON team_metric_season (run_id, metric, season_id);
CREATE INDEX idx_tms_team ON team_metric_season (team_id);
