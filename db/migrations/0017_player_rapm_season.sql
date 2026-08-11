-- 0017_player_rapm_season: the plus-minus gains a time axis.
--
-- Until now `player_rapm` held one coefficient per player per run, fitted over
-- every decided map in the archive. That cannot express a peak, a decline, or a
-- player who changed, which is the axis the interesting arguments run on.
--
-- What it gains is not simply "a season column", because the identification
-- pre-flight found the two eras cannot carry the same resolution. Every row of
-- the design is one lineup against another, so the rank can never exceed
-- distinct lineups minus the components of the lineup graph; the CDL era earns
-- a season, and the CWL era — whose median team-season fielded exactly one
-- lineup all year — earns nothing finer than the era. So a row says three
-- things rather than one:
--
--   season_id   which season it is filed under
--   resolution  what the coefficient actually covers: one season, a whole era,
--               or a whole career
--   scope       which coefficient family it belongs to
--
-- An era-resolution coefficient is stored against every season it covers, with
-- `resolution = 'era'` on each. Three rows carrying one number is the honest
-- shape for a quantity estimated once over three seasons: a join stays uniform,
-- and no reader can mistake the rows for three estimates when each says what it
-- is. The alternative — a null season on an era row — buys nothing and makes
-- every downstream join carry the special case.
--
-- The three scopes are not interchangeable and the distinction is load-bearing:
--
--   career    the global fit that already shipped. Retained, not recomputed.
--   smoothed  two-sided random-walk penalty. A 2023 coefficient has been pulled
--             toward 2024, so it has seen 2024's maps. Correct for a
--             retrospective number, disqualifying for a forward test.
--   filtered  one-sided: fitted on maps through that cell and nothing later.
--             The only family a forward test may read.
--
-- The leak in `smoothed` arrives through the penalty rather than through a
-- column, so no leakage test written against a design matrix can see it. The
-- enforcement is a check in code that raises; this column is what makes the
-- check possible at all.
--
-- `penalty_share` is the share of the column's prior variance that survived
-- into the posterior: 0 means the data decided it, 1 means the prior did. It
-- does not reach 1 and it is not read against a flat threshold — a player whose
-- k-strong lineup never changes shares one identified direction with k-1
-- teammates and their team column, so their share sits near k/(k+1), which is
-- 0.80 at 4v4. It is null on career rows, which predate the statistic.
--
-- The primary key goes, because it can no longer say what it needs to: career
-- rows have no season and season rows have several per player. Two partial
-- unique indexes replace it and between them cover every row exactly once.

ALTER TABLE player_rapm
  ADD COLUMN season_id     integer REFERENCES seasons(id),
  ADD COLUMN scope         text NOT NULL DEFAULT 'career',
  ADD COLUMN resolution    text NOT NULL DEFAULT 'career',
  ADD COLUMN penalty_share real;

ALTER TABLE player_rapm
  DROP CONSTRAINT player_rapm_pkey,
  ADD CONSTRAINT player_rapm_scope_known
    CHECK (scope IN ('career', 'smoothed', 'filtered')),
  ADD CONSTRAINT player_rapm_resolution_known
    CHECK (resolution IN ('career', 'era', 'season')),
  -- A career coefficient has no season and a season row is never the career
  -- scope: the two halves of the table cannot blur into each other.
  ADD CONSTRAINT player_rapm_career_has_no_season
    CHECK ((scope = 'career') = (season_id IS NULL)),
  ADD CONSTRAINT player_rapm_career_resolution
    CHECK ((resolution = 'career') = (scope = 'career')),
  ADD CONSTRAINT player_rapm_penalty_share_range
    CHECK (penalty_share IS NULL OR (penalty_share >= 0 AND penalty_share <= 1));

CREATE UNIQUE INDEX player_rapm_career_key
  ON player_rapm (run_id, player_id)
  WHERE season_id IS NULL;

CREATE UNIQUE INDEX player_rapm_season_key
  ON player_rapm (run_id, scope, player_id, season_id)
  WHERE season_id IS NOT NULL;

CREATE INDEX idx_player_rapm_season ON player_rapm (season_id);

COMMENT ON COLUMN player_rapm.season_id IS
  'The season this row is filed under, null only on a career-scope row. Read '
  'with resolution: an era coefficient is filed against each season it covers.';

COMMENT ON COLUMN player_rapm.resolution IS
  'What the coefficient covers: one season, a whole era, or a whole career. An '
  'era row repeats one estimate across its seasons and is not three of them.';

COMMENT ON COLUMN player_rapm.scope IS
  'Coefficient family. smoothed is two-sided and has seen the following season '
  'through the random-walk penalty; only filtered may be read by a forward '
  'test. career is the published global fit, retained unchanged.';

COMMENT ON COLUMN player_rapm.penalty_share IS
  'Share of the column''s prior variance surviving into the posterior: 1 means '
  'the data said nothing and the number is the penalty''s. Read against k/(k+1) '
  '-- 0.80 at 4v4 -- not against a flat threshold, since a never-changing '
  'lineup cannot go below it.';
