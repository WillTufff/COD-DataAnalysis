-- 0003_analytics: versioned model-output layer.
-- Every model output row is keyed to a model_runs row (code version, params,
-- train window). Outputs are replaceable as a whole run, never edited row-wise;
-- ON DELETE CASCADE lets a rerun with identical (model, version, data_through)
-- swap its outputs atomically.

CREATE TABLE model_runs (
  id           serial PRIMARY KEY,
  model        text NOT NULL,          -- 'elo','glicko2','era_adjust_v1',...
  version      text NOT NULL,          -- semver of the model spec
  code_ref     text,                   -- git SHA that produced it
  params       jsonb,                  -- full hyperparameters, reproducibility
  data_through date,                   -- last match date included
  created_at   timestamptz DEFAULT now(),
  UNIQUE (model, version, data_through)
);

CREATE TABLE team_ratings (             -- time series: one row per team per rated series
  run_id      int NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  team_id     int NOT NULL REFERENCES teams(id),
  series_id   int NOT NULL REFERENCES series(id),
  rating_pre  real NOT NULL,
  rating_post real NOT NULL,
  rating_sd   real,                    -- uncertainty (Glicko RD); NULL for Elo
  PRIMARY KEY (run_id, team_id, series_id)
);

CREATE TABLE player_season_adjusted (   -- era-adjusted per player × season × mode
  run_id       int NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id    int NOT NULL REFERENCES players(id),
  season_id    int NOT NULL REFERENCES seasons(id),
  mode_id      smallint REFERENCES game_modes(id),  -- NULL = all modes
  maps_played  int NOT NULL,
  kd_raw       real, kd_z real, kd_pctl real,       -- raw, cohort z-score, percentile
  engagement_z real,                   -- (kills+deaths) pace vs cohort
  obj_z        real,                   -- mode-specific objective z
  rating       real, rating_sd real,   -- open composite rating (future), NULL until modeled
  completeness real NOT NULL           -- share of maps with full box scores
);

-- mode_id is NULL for the all-modes row, so it can't sit in a primary key;
-- a COALESCE unique index enforces the same one-row-per-cohort guarantee.
CREATE UNIQUE INDEX uq_psa_cohort
  ON player_season_adjusted (run_id, player_id, season_id, COALESCE(mode_id, 0));

CREATE TABLE career_curves (            -- fitted aging/trajectory per player
  run_id     int NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id  int NOT NULL REFERENCES players(id),
  age_or_seq real NOT NULL,            -- x-axis: age if known else career-season index
  fitted     real NOT NULL,
  lo95       real, hi95 real,          -- band — always stored, always drawn
  PRIMARY KEY (run_id, player_id, age_or_seq)
);

CREATE TABLE player_archetypes (
  run_id     int NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id  int NOT NULL REFERENCES players(id),
  season_id  int NOT NULL REFERENCES seasons(id),
  archetype  text NOT NULL,            -- cluster label, e.g. 'anchor-AR','entry-SMG'
  loadings   jsonb,                    -- per-feature contribution, drives the radar viz
  PRIMARY KEY (run_id, player_id, season_id)
);

CREATE TABLE insights (                 -- machine-generated interpretation atoms (§5.4)
  id           serial PRIMARY KEY,
  run_id       int REFERENCES model_runs(id) ON DELETE CASCADE,
  subject_type text NOT NULL,          -- 'player','team','season','map','mode','event'
  subject_id   int NOT NULL,
  kind         text NOT NULL,          -- 'trend','outlier','milestone','h2h_edge','era_context'
  headline     text NOT NULL,          -- one sentence, plain English
  detail       jsonb NOT NULL,         -- numbers backing it + link params to evidence
  score        real NOT NULL,          -- surprisingness/importance for ranking
  valid_through date
);

CREATE TABLE backtests (                -- published model report cards
  run_id        int NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE PRIMARY KEY,
  window_from   date NOT NULL,
  window_to     date NOT NULL,
  n_predictions int NOT NULL,
  brier         real, log_loss real, accuracy real,
  calibration   jsonb NOT NULL         -- binned predicted-vs-observed
);

CREATE INDEX idx_team_ratings_team   ON team_ratings (team_id, series_id);
CREATE INDEX idx_psa_player          ON player_season_adjusted (player_id);
CREATE INDEX idx_psa_season          ON player_season_adjusted (season_id);
CREATE INDEX idx_insights_subject    ON insights (subject_type, subject_id);
CREATE INDEX idx_insights_run        ON insights (run_id);
CREATE INDEX idx_model_runs_model    ON model_runs (model, version, created_at DESC);
