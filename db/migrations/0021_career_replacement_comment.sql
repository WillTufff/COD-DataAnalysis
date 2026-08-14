-- 0021_career_replacement_comment: `player_career.replacement` says what it is.
--
-- 0019 documented the column as "the baseline subtracted per season". That is
-- what a career row subtracts, and it is not what the column stores. A career
-- spans several seasons and each one has its own qualified-cohort minimum, so
-- the row keeps the mean of the baselines it actually used. The stored numbers
-- said so all along -- 72 distinct values across 321 composite careers, where a
-- single per-season figure would repeat -- and the comment did not.
--
-- Corrected here instead of by editing 0019, which is applied. A migration that
-- changes under a database that already ran it is not a migration.

COMMENT ON COLUMN player_career.replacement IS
  'Mean of the per-season qualified-cohort minimums this career subtracted, one '
  'per season played. The per-season figures themselves are not stored: they are '
  'a property of the season''s cohort, not of the player.';
