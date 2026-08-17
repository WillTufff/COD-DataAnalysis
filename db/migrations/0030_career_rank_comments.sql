-- Re-states the two career_rank table comments so a database migrated before
-- this point carries the same text as one built from 0027 today.

COMMENT ON TABLE player_season_rank IS
  'career_rank engine output: one breadth score per player-season, award '
  'credit already added and capped at 100.';

COMMENT ON TABLE player_career_rank IS
  'career_rank engine output: peak/best-three/total over player_season_rank. '
  'One row per player, over the frozen evaluation population.';
