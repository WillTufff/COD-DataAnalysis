-- 0025_player_role_season: where a player stood at the opening engagement.
--
-- The role run publishes a league-level artifact -- the entry cost, the weapon
-- class recovery rate -- and until now nothing carried the per-player half of
-- it, so a player page could not show its own subject's position. This table
-- is that half.
--
-- **A position, not a label.** `contact_rate` is how often a player is in the
-- opening engagement of a Search and Destroy round, and `contact_win_rate` is
-- whether they win it. Neither is thresholded into "entry" or "anchor": the
-- style phase found no taxonomy to threshold against, so what is stored is the
-- number and its percentile among the season's qualified players.
--
-- **The three K/D columns ship together or not at all.** `kd_raw` is the
-- within-season standardised K/D, `kd_adjustment` is what the contact rate
-- accounts for, and `kd_adjusted` is what is left. An adjusted number whose
-- adjustment nobody can see the size of is worse than no adjustment, so the
-- check below refuses a row carrying one of the three without the others.
--
-- Modern era only. `first_deaths` is zero on every CWL title, so a contact
-- rate before 2020 would be a first-blood rate wearing another name.

CREATE TABLE player_role_season (
  run_id           integer NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id        integer NOT NULL REFERENCES players(id),
  season_id        integer NOT NULL REFERENCES seasons(id),
  maps             integer NOT NULL,
  contact_rate     real NOT NULL,
  contact_win_rate real NOT NULL,
  contact_pctl     real NOT NULL,
  kd_raw           real,
  kd_adjustment    real,
  kd_adjusted      real,
  PRIMARY KEY (run_id, player_id, season_id),
  CONSTRAINT player_role_season_rates_are_rates
    CHECK (contact_win_rate BETWEEN 0 AND 1 AND contact_pctl BETWEEN 0 AND 1),
  CONSTRAINT player_role_season_adjustment_is_auditable
    CHECK (
      (kd_raw IS NULL AND kd_adjustment IS NULL AND kd_adjusted IS NULL)
      OR (kd_raw IS NOT NULL AND kd_adjustment IS NOT NULL AND kd_adjusted IS NOT NULL)
    )
);

CREATE INDEX idx_player_role_season_player ON player_role_season (run_id, player_id);

COMMENT ON COLUMN player_role_season.contact_rate IS
  'Opening engagements per Search and Destroy map: (first_bloods + '
  'first_deaths) / maps. A position on a continuum, not a role label.';

COMMENT ON COLUMN player_role_season.contact_win_rate IS
  'first_bloods / (first_bloods + first_deaths). Whether the opening '
  'engagement was won, given it was taken.';

COMMENT ON COLUMN player_role_season.kd_adjustment IS
  'The part of the standardised K/D that the season''s contact rate accounts '
  'for. Published beside the raw and the adjusted number so a reader can see '
  'what the role model gave back.';
