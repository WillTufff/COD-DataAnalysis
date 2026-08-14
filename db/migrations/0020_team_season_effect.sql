-- 0020_team_season_effect: the column a player coefficient is a deviation from.
--
-- `player_rapm` publishes a season coefficient as a deviation from the player's
-- team-season, and until now the quantity being deviated from was fitted, used
-- to identify the player columns, and then discarded when the solve returned.
-- That is fine while the only question is "who deviated most". It stops being
-- fine the moment a career total has to be built, because the total either
-- credits the deviation alone -- which under-credits the four players who *were*
-- a great team -- or the deviation plus a share of the team term, and the second
-- of those cannot be computed at all without this column.
--
-- Storing it is also the honest thing on its own terms. It is a fitted model
-- output with a standard interpretation: what this roster was worth in
-- rank-transformed score margin, over and above the four players in it. Leaving
-- it in a local variable made it un-auditable and made every downstream user of
-- it a refit.
--
-- **The rows are one-sided, like the player family they pair with.** A season's
-- team effect is taken from the earliest filtered solve that covers that season,
-- so it has seen maps through that season and nothing after it. An era-resolution
-- cell covers three seasons and writes all three from one solve, exactly as
-- `player_rapm` does; `resolution` says which case a row is, so nobody reads
-- three copies of one estimate as three estimates.
--
-- There is no standard error column. The team block is not published as a team
-- rating and no interval is claimed for it: it exists so a career total can name
-- what it credited. `team_ratings` remains the place a team is rated.

CREATE TABLE team_season_effect (
  run_id     integer NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  team_id    integer NOT NULL REFERENCES teams(id),
  season_id  integer NOT NULL REFERENCES seasons(id),
  scope      text NOT NULL DEFAULT 'filtered',
  resolution text NOT NULL,
  coef       double precision NOT NULL,
  PRIMARY KEY (run_id, team_id, season_id, scope),
  CONSTRAINT team_season_effect_forward_scope_only
    CHECK (scope = 'filtered'),
  CONSTRAINT team_season_effect_resolution_known
    CHECK (resolution IN ('era', 'season'))
);

CREATE INDEX idx_team_season_effect_season ON team_season_effect (season_id);

COMMENT ON TABLE team_season_effect IS
  'The fitted team-season column of the season plus-minus: what the roster was '
  'worth beyond the four players in it. Stored so a career total can say what '
  'it credited, not published as a team rating.';

COMMENT ON COLUMN team_season_effect.resolution IS
  'season is one estimate for one season; era repeats one estimate across the '
  'seasons its cell covers, as player_rapm does.';

COMMENT ON COLUMN team_season_effect.scope IS
  'Always filtered. The smoothed family has seen the season after this one '
  'through its two-sided penalty, and nothing that feeds a career total may.';
