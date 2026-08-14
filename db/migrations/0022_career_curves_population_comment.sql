-- 0022_career_curves_population_comment: the pair counts the column claims.
--
-- 0019 documented `career_curves.population` as "composite has ten seasons and
-- 573 consecutive pairs; plus_minus has seven and 229". The fitted run reports
-- 530 pairs on the composite overall population and 263 on the plus-minus, and
-- the numbers in the comment matched neither. A count written into a comment
-- drifts from the run that produces it, so this one now names where the count
-- can be read instead of repeating a figure that goes stale.
--
-- Corrected here instead of by editing 0019, which is applied, for the same
-- reason 0021 was written.

COMMENT ON COLUMN career_curves.population IS
  'The season quantity fitted. The two have very different power: the composite '
  'axis spans ten seasons, the plus-minus seven, and the pair count each fit '
  'used is stored on the run''s aging artifact as n_observations.';
