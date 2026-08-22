-- 0037_career_rank_families_and_value: what a season score was built from.
--
-- Two things the season row could not say before, and both of them are
-- claims about coverage rather than about a player.
--
-- `families_present` lists the metric families the season was actually scored
-- on. The score is now the unweighted mean over the live families rather than
-- over every surviving metric, so what a season is worth no longer depends on
-- how many metrics happen to point at the same thing. That repair is worth
-- publishing only beside the fact that family coverage is era-partitioned: no
-- era carries all six, a 2013-2016 season is scored on three and a league one
-- on four or five. Null means the run predates this column, which is not the
-- same claim as an empty list.
--
-- `breadth` and `value_scaled` are the two halves of the score. VALUE enters
-- at a weight fixed before the run and is mapped onto the breadth score's own
-- location and scale within the same season, so the blend can never move a
-- season against another era. `value_scaled` is null where the season was
-- scored on breadth alone: no VALUE row, or a field too small or too flat to
-- map against. A null there is a half the archive does not reach, never a
-- zero.

ALTER TABLE player_season_rank
  ADD COLUMN families_present text[],
  ADD COLUMN breadth          double precision,
  ADD COLUMN value_scaled     double precision;

COMMENT ON COLUMN player_season_rank.families_present IS
  'Metric families this season was scored on, from volume, efficiency, '
  'objective, discipline, opening and streaks. No era carries all six. Null '
  'on a run made before the column existed.';

COMMENT ON COLUMN player_season_rank.breadth IS
  'The breadth half of the season score, after shrinkage and before the '
  'VALUE blend and any award credit.';

COMMENT ON COLUMN player_season_rank.value_scaled IS
  'The VALUE half, mapped onto the breadth scale within the season. Null '
  'where the season was scored on breadth alone.';
