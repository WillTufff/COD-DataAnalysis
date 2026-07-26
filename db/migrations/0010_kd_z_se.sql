-- 0010_kd_z_se: the standard error of the published K/D z-score.
--
-- The career arc chart drew a +/-1.96/sqrt(maps) band, which is the correct
-- shape with the wrong scale: it implicitly assumes a player's per-map K/D
-- varies exactly as much as season K/Ds vary between players. Per-map variance
-- is the larger of the two, so the band was too tight — on the one chart whose
-- subject is uncertainty.
--
-- The honest quantity is SE(kd) / cohort_sd, where SE(kd) comes from the delta
-- method on the ratio of summed kills to summed deaths. Storing it per row
-- keeps the arithmetic next to the model that knows the cohort, instead of
-- approximated in the browser. NULL where the sample cannot support it (one
-- map, or no deaths).
ALTER TABLE player_season_adjusted ADD COLUMN kd_z_se real;

COMMENT ON COLUMN player_season_adjusted.kd_z_se IS
  'Standard error of kd_z: delta-method SE of the season K/D, divided by the '
  'cohort SD used to form the z-score. NULL when maps < 2 or deaths = 0.';
