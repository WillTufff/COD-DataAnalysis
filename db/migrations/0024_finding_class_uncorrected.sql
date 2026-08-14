-- 0024_finding_class_uncorrected: a fourth class, for a claim nothing can test yet.
--
-- 0023 declared three classes and sorted every kind into one of them. Working
-- through the families one at a time broke two of those assignments, and both
-- breaks are about the same thing: what a finding's sentence claims.
--
-- The criterion 0023 was missing, stated here so the next reader has it:
--
--   A finding is TESTABLE when its sentence claims a latent quantity -- an
--   ability, an edge, a tendency -- that the record only estimates. It is
--   DESCRIPTIVE when its sentence is a statement about the record itself.
--
-- **`era_context` and `meta_shift` move to descriptive.** Both are league-wide
-- aggregates over every map a season or event contains: engagement pace across
-- a whole season, weapon usage share across a whole event. A complete
-- population estimates nothing, so there is no latent quantity to be wrong
-- about and no null that is not invented. 0023 had them as testable.
--
-- **`profile_extreme`, `intangible_outlier` and `team_style` become
-- `uncorrected`.** These do claim latent tendencies, so the criterion admits
-- them, and they cannot be tested with what the database holds.
-- `player_metric_season` stores `value`, `denom`, `z` and `pctl` for an
-- arbitrary metric and no standard error, and the project has no per-metric
-- variance model. A threshold test on one of them would need an error bar
-- invented for the occasion, which is the failure this phase exists to prevent.
--
-- So they ship labelled rather than silently uncorrected or quietly dropped.
-- The fix is named and is a phase of its own: a cluster bootstrap over a
-- player's own maps gives a uniform error for any metric without a per-metric
-- variance model, and `resample.py` already carries the ordering and seeding
-- policy such a bootstrap has to follow.

ALTER TABLE insights DROP CONSTRAINT insights_finding_class_known;

ALTER TABLE insights
  ADD CONSTRAINT insights_finding_class_known
    CHECK (finding_class IN ('testable', 'uncorrected', 'descriptive', 'self_tested'));

UPDATE insights SET finding_class = CASE
  WHEN kind IN ('milestone', 'rating_top', 'what_wins', 'era_context', 'meta_shift')
    THEN 'descriptive'
  WHEN kind IN ('model_null', 'mode_null', 'series_dynamics')
    THEN 'self_tested'
  WHEN kind IN ('profile_extreme', 'intangible_outlier', 'team_style')
    THEN 'uncorrected'
  ELSE 'testable'
END;

-- `uncorrected` carries no numbers either, so the rule that only tests carry
-- test statistics still holds and is restated against the wider vocabulary.
ALTER TABLE insights DROP CONSTRAINT insights_only_tests_carry_stats;

ALTER TABLE insights
  ADD CONSTRAINT insights_only_tests_carry_stats
    CHECK (
      finding_class = 'testable'
      OR (p_value IS NULL AND q_bh IS NULL AND q_by IS NULL)
    );

COMMENT ON COLUMN insights.finding_class IS
  'The declared partition. testable claims a latent quantity and carries p and '
  'q; uncorrected claims one the database has no error model for and says so; '
  'descriptive is a statement about the record with no latent quantity behind '
  'it; self_tested already publishes its own interval.';
