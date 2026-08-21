-- Liquipedia's own importance number for a finish, kept where the source puts
-- it: on the placement, not on the event.
--
-- The number is not one value per tournament. It decomposes exactly as
--
--   weight = 8 * max(prizemoney, 1) / placement_min
--
-- over 1,813 of the 1,845 parseable rows in the snapshot, so it already
-- carries a placement curve of its own, and 137 of 171 tournament pages hold
-- more than one value. A column on `events` would have had to pick one row and
-- call it the tournament's, and any credit formula that multiplied it by a
-- placement curve would count placement twice. The tournament's own scale is
-- `events.prize_pool`, which the /tournament load already writes.

ALTER TABLE event_placements ADD COLUMN lpdb_weight numeric;

COMMENT ON COLUMN event_placements.lpdb_weight IS
  'Liquipedia placement weight, as published: 8 * max(prize, 1) / placement_min. '
  'Carries a placement curve already; do not multiply it by another one.';
