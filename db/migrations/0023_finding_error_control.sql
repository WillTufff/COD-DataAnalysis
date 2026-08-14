-- 0023_finding_error_control: what a finding's number is worth against chance.
--
-- The site publishes 227 findings across sixteen kinds, many of them selected
-- extremes, and until now nothing anywhere computed a false discovery rate over
-- them. Scanning sixteen families across thousands of player-seasons and
-- reporting the extremes produces confident-looking claims from noise at a rate
-- nobody had quantified. These columns quantify it.
--
-- **A third of the ledger is not a test, and the schema says so rather than
-- assigning it a q-value of 1.** `finding_class` is the declared partition:
--
--   testable      a null exists and the finding is a screened extreme of it.
--                 Ten kinds, and the only ones that carry p and q.
--   descriptive   a true statement about the record with no null behind it --
--                 a career-map count, a ranking of published ratings, a weight
--                 that already ships its own interval. A q-value on one of
--                 these is a category error, not a conservative choice.
--   self_tested   a declared test that already publishes its own interval, so
--                 it was never drawn from a family and correcting it against
--                 one would misdescribe it.
--
-- **The correction is computed over the candidate population, not over these
-- rows.** Every testable kind screens before it emits: an outlier needs 2 SD, a
-- head-to-head edge needs eight series and 70%, and two further passes collapse
-- and cap what survives. Correcting the survivors would correct for 227 tests
-- when several thousand were run. So `p_value` is the candidate's own p, and
-- `q_bh` and `q_by` are its rank in the whole candidate family. The candidate
-- counts live on the run's artifact, because they are a property of the family
-- rather than of any row here.
--
-- **Both procedures ship because the dependence is real.** Benjamini-Hochberg
-- controls FDR under independence or positive dependence. The same
-- player-season recurs across families and the families overlap by
-- construction, so BH is the optimistic bound. Benjamini-Yekutieli is valid
-- under arbitrary dependence and costs power. Publishing one without the other
-- would trade an unstated error rate for an unstated loss of power.
--
-- **`retracted` is a published state, not a delete.** A finding that fails the
-- threshold stays in the table and stays readable, with the q-value that
-- retracted it. The alternative is a claim that silently disappears, which is
-- the failure this phase exists to prevent.

ALTER TABLE insights
  ADD COLUMN finding_class text,
  ADD COLUMN p_value   double precision,
  ADD COLUMN q_bh      double precision,
  ADD COLUMN q_by      double precision,
  ADD COLUMN retracted boolean NOT NULL DEFAULT false;

-- The partition, applied to the rows already in the table. It carries no
-- default afterwards: a writer has to say which of the three a finding is,
-- because the whole point of the column is that the answer was decided in
-- advance rather than inferred later.
UPDATE insights SET finding_class = CASE
  WHEN kind IN ('milestone', 'rating_top', 'what_wins') THEN 'descriptive'
  WHEN kind IN ('model_null', 'mode_null', 'series_dynamics') THEN 'self_tested'
  ELSE 'testable'
END;

ALTER TABLE insights ALTER COLUMN finding_class SET NOT NULL;

ALTER TABLE insights
  ADD CONSTRAINT insights_finding_class_known
    CHECK (finding_class IN ('testable', 'descriptive', 'self_tested')),
  -- Nothing that is not a test ever carries a test's numbers. The mirror of
  -- this rule -- that every testable finding *does* carry all three -- is a
  -- property of one run rather than of the table, so it lives in the release
  -- gate instead. Rows written before this migration are classified below and
  -- carry no p-value, and a schema constraint would have to either reject them
  -- or be written loosely enough to let a real gap through.
  ADD CONSTRAINT insights_only_tests_carry_stats
    CHECK (
      finding_class = 'testable'
      OR (p_value IS NULL AND q_bh IS NULL AND q_by IS NULL)
    ),
  ADD CONSTRAINT insights_p_value_range
    CHECK (p_value IS NULL OR (p_value >= 0 AND p_value <= 1)),
  ADD CONSTRAINT insights_q_bh_range
    CHECK (q_bh IS NULL OR (q_bh >= 0 AND q_bh <= 1)),
  ADD CONSTRAINT insights_q_by_range
    CHECK (q_by IS NULL OR (q_by >= 0 AND q_by <= 1)),
  -- Both step-up procedures return a q at least as large as the p they adjust,
  -- and BY's penalty is a multiple of BH's, so BY is never the smaller of the
  -- two. A row that breaks either ordering is an implementation error.
  ADD CONSTRAINT insights_q_adjusts_upward
    CHECK (q_bh IS NULL OR q_bh >= p_value - 1e-9),
  ADD CONSTRAINT insights_by_not_below_bh
    CHECK (q_by IS NULL OR q_by >= q_bh - 1e-9),
  -- Only a testable finding can be retracted, because retraction is a verdict
  -- on a q-value and nothing else has one.
  ADD CONSTRAINT insights_retraction_needs_a_test
    CHECK (NOT retracted OR finding_class = 'testable');

CREATE INDEX idx_insights_retracted ON insights (run_id, retracted);

COMMENT ON COLUMN insights.finding_class IS
  'The declared partition: testable carries a null and a statistic, descriptive '
  'is a true statement about the record with no null, self_tested already '
  'publishes its own interval and was never drawn from a family.';

COMMENT ON COLUMN insights.p_value IS
  'The candidate''s own p under its family''s null. Null on everything that is '
  'not a test.';

COMMENT ON COLUMN insights.q_bh IS
  'Benjamini-Hochberg q over the whole candidate family, not over the published '
  'rows. The optimistic bound: BH assumes independence or positive dependence '
  'and these families overlap.';

COMMENT ON COLUMN insights.q_by IS
  'Benjamini-Yekutieli q over the same candidates. Valid under arbitrary '
  'dependence and costs power. Published beside q_bh so a reader can see where '
  'the two disagree.';

COMMENT ON COLUMN insights.retracted IS
  'True when the finding failed the declared threshold. The row stays readable '
  'with the q-value that retracted it, because a claim that disappears is the '
  'failure this column exists to prevent.';
