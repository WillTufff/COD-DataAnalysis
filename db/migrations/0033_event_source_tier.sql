-- The wiki's own tier word, kept out of the column the title rule reads.
--
-- `events.tier` is a Liquipedia numeric tier, and `career_rank.titles`
-- selects on it: coalesce(tier, '1') IN ('1', '2'). The 2013-2016 archive
-- publishes Premier, Major, Minor and Qualifier instead of numbers, so the
-- word cannot go in that column without deciding the title set as a side
-- effect of a data load. Premier and Major map onto 1 and 2, which the rule
-- already admits; Minor has no numeric analogue anywhere in the post-2017
-- record, so it leaves `tier` null and lands here alone.

ALTER TABLE events ADD COLUMN source_tier text;

COMMENT ON COLUMN events.source_tier IS
  'Tier word as the source publishes it (Premier, Major, Minor, Qualifier). '
  'Read by nothing that selects events; events.tier carries the numeric tier.';
