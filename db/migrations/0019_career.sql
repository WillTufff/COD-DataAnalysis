-- 0019_career: career totals over a replacement baseline, and the aging curve
-- gaining the columns that say which fit produced it.
--
-- Two tables, because the phase produces two different objects. `player_career`
-- is one row per player per way of counting their career. `career_curves` has
-- existed and been empty since 0003; it now says what it is a curve *of*.
--
-- **A career total is not one number and the table refuses to pretend it is.**
-- The plan asks for a sum of season value over replacement, and the record
-- offers two axes to sum, which answer different questions:
--
--   composite    `player_season_adjusted.rating` -- what that season was worth
--                on the scoreboard. Ten seasons, 2017-2026, measured precisely.
--   plus_minus   the `filtered` season coefficient -- what the player's presence
--                was worth in score margin, as a deviation from their team.
--
-- Only the second has a team term, and that is where the credit rule bites. A
-- player's season coefficient is a deviation from their team-season, so a career
-- total either credits the deviation alone -- which under-credits the four
-- players who *were* a great team -- or the deviation plus a share of the team
-- term, which re-imports the ambiguity the team effect was added to remove.
-- Both are stored. Neither is the answer. The difference between the two
-- orderings is the finding, and a table that stored one of them would publish a
-- choice as a measurement.
--
-- **`era_scope` exists because the two eras do not carry the same resolution.**
-- The CWL years store one pooled coefficient per player per era; three rows
-- carrying one estimate is the honest shape for it, and summing those three
-- would count one number three times. So a plus-minus career total covers the
-- CDL era, and the CWL contribution is stored as its own row at era scope,
-- readable beside the total and never inside it. The composite axis has no such
-- constraint -- every season is its own estimate -- so it stores one row at
-- `all` scope spanning 2017-2026.
--
-- **Peak, best-three and total are three columns because they are three
-- arguments.** Collapsing them into one hides the disagreement that makes the
-- question interesting: the highest peak and the largest total are usually not
-- the same player, and a reader who is told only the total cannot see that.
--
-- `total_sd` is not decoration on the plus-minus axis. P1 measured the spread
-- between season coefficients as indistinguishable from zero given their own
-- standard errors; summing seven of them narrows the interval without
-- manufacturing separation, so most of this table's intervals overlap. The
-- column is what lets a reader see that rather than being told an ordering.

CREATE TABLE player_career (
  run_id       integer NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  player_id    integer NOT NULL REFERENCES players(id),
  axis         text NOT NULL,
  credit       text NOT NULL,
  era_scope    text NOT NULL,
  seasons      integer NOT NULL,
  maps         integer NOT NULL,
  replacement  double precision NOT NULL,
  total        double precision NOT NULL,
  total_sd     double precision,
  peak         double precision NOT NULL,
  peak_season_id integer REFERENCES seasons(id),
  best_three   double precision,
  best_three_start_season_id integer REFERENCES seasons(id),
  PRIMARY KEY (run_id, player_id, axis, credit, era_scope),
  CONSTRAINT player_career_axis_known
    CHECK (axis IN ('composite', 'plus_minus')),
  CONSTRAINT player_career_credit_known
    CHECK (credit IN ('none', 'deviation', 'deviation_plus_team')),
  CONSTRAINT player_career_era_scope_known
    CHECK (era_scope IN ('all', 'cdl', 'cwl')),
  -- The composite axis has no team term to share and no era it cannot span, so
  -- exactly one shape is legal for it. The plus-minus axis is the mirror: it
  -- always names a credit rule and never spans both eras in one row.
  CONSTRAINT player_career_composite_shape
    CHECK (axis <> 'composite' OR (credit = 'none' AND era_scope = 'all')),
  CONSTRAINT player_career_plus_minus_shape
    CHECK (axis <> 'plus_minus' OR (credit <> 'none' AND era_scope <> 'all')),
  CONSTRAINT player_career_counts_positive
    CHECK (seasons > 0 AND maps > 0),
  -- Best-three is null when a career is shorter than three seasons, and the
  -- season it starts from is present exactly when the value is.
  CONSTRAINT player_career_best_three_paired
    CHECK ((best_three IS NULL) = (best_three_start_season_id IS NULL)),
  CONSTRAINT player_career_total_sd_nonnegative
    CHECK (total_sd IS NULL OR total_sd >= 0)
);

CREATE INDEX idx_player_career_axis ON player_career (run_id, axis, credit, era_scope);

COMMENT ON TABLE player_career IS
  'Career value over a replacement baseline: one row per player per way of '
  'counting, because the axis and the credit rule are choices rather than '
  'facts and the orderings they produce differ.';

COMMENT ON COLUMN player_career.axis IS
  'Which season quantity was summed: composite is the published season rating, '
  'plus_minus is the filtered season coefficient.';

COMMENT ON COLUMN player_career.credit IS
  'How the team-season term was treated. deviation credits the player column '
  'alone; deviation_plus_team adds their share of the team term. none is the '
  'composite axis, which has no team term.';

COMMENT ON COLUMN player_career.era_scope IS
  'Which era the total covers. A CWL row is pooled at era resolution and is a '
  'single estimate, not a sum: it is read beside a cdl total, never added.';

COMMENT ON COLUMN player_career.replacement IS
  'The baseline subtracted per season: the qualified-cohort minimum for that '
  'season, not a chosen percentile.';

COMMENT ON COLUMN player_career.total_sd IS
  'Uncertainty on the total. On the plus-minus axis most of these overlap '
  'across the table, which is the phase''s result rather than a defect in it.';

-- career_curves: fitted since 0003, empty since 0003.
--
-- The reason it stayed empty is the reason it now needs four more key columns.
-- A single aging curve per player is not publishable here, because a curve
-- fitted on observed player-seasons is biased upward at the tail: players who
-- decline leave the league and stop contributing seasons, so the survivors at
-- 28 are the ones who did not decline. The defence is not a better single fit.
-- It is three fits whose disagreement is the measurement:
--
--   naive      every observed player-season, level on level. The biased one,
--              published so the size of the bias is visible.
--   delta      paired consecutive seasons of the same player, so each
--              observation is a within-player change. Carries its own version
--              of the bias -- pairing conditions on having a next season --
--              which is why it is not published alone either.
--   retention  the same population weighted by a fitted hazard of leaving.
--
-- `population` records which season quantity was fitted, because the two have
-- very different power: the box score offers 573 consecutive-season pairs
-- across ten seasons, the plus-minus offers 229 across seven.
--
-- `component` carries the two-component hypothesis: slaying and objective
-- metrics fitted separately, with the prediction that their peaks differ. It
-- is a prediction, and it is reported whichever way it lands.
--
-- `x_is_age` disambiguates the axis that 0003 left ambiguous. `age_or_seq` was
-- documented as "age if known else career-season index", which makes a curve
-- unreadable without knowing which one each row used -- and 439 of 815 players
-- carry a birthdate, so both kinds are real here.

ALTER TABLE career_curves
  ADD COLUMN population text NOT NULL DEFAULT 'composite',
  ADD COLUMN fit        text NOT NULL DEFAULT 'naive',
  ADD COLUMN component  text NOT NULL DEFAULT 'overall',
  ADD COLUMN x_is_age   boolean NOT NULL DEFAULT true;

ALTER TABLE career_curves
  DROP CONSTRAINT career_curves_pkey,
  ADD PRIMARY KEY (run_id, player_id, population, fit, component, x_is_age, age_or_seq),
  ADD CONSTRAINT career_curves_population_known
    CHECK (population IN ('composite', 'plus_minus')),
  ADD CONSTRAINT career_curves_fit_known
    CHECK (fit IN ('naive', 'delta', 'retention')),
  ADD CONSTRAINT career_curves_component_known
    CHECK (component IN ('overall', 'slaying', 'objective')),
  ADD CONSTRAINT career_curves_band_ordered
    CHECK (lo95 IS NULL OR hi95 IS NULL OR lo95 <= hi95);

COMMENT ON COLUMN career_curves.fit IS
  'Which of the three fits produced this curve. No one of them is the answer: '
  'the peak age publishes as an interval spanning all three, and the spread '
  'between them is the size of the survivorship problem.';

COMMENT ON COLUMN career_curves.population IS
  'The season quantity fitted. composite has ten seasons and 573 consecutive '
  'pairs; plus_minus has seven and 229.';

COMMENT ON COLUMN career_curves.component IS
  'overall, or one half of the two-component test: slaying and objective '
  'metrics fitted separately, predicted to peak at different ages.';

COMMENT ON COLUMN career_curves.x_is_age IS
  'True when age_or_seq is an age in years, false when it is a career-season '
  'index. 0003 allowed either in one column without recording which.';
