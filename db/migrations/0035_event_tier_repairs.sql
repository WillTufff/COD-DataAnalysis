-- 0035_event_tier_repairs: the three events the tier stamp missed or mis-sized.
--
-- `codwiki.results.load_event_meta` now windows on the event's own start date
-- and reads `TIER_BY_STRUCTURE`, and both changes reach a fresh load. Neither
-- reaches a database that already ran the load once, so the same three repairs
-- are made here.
--
-- The two Australia-New Zealand stage playoffs take tier 1 from the competition
-- they belong to. North America and Europe ran the same stage playoff in the
-- same season and the wiki calls those Premier; Australia-New Zealand is Minor
-- on a smaller pool. Admitting two regions' stage titles and refusing the
-- third's would be a regional cut wearing a tier's clothes.
--
-- `PlayStation Experience Invitational` was never stamped. It ran on
-- 2016-12-03, but seasons are cut by game title, so it sits in an Infinite
-- Warfare season stamped 2017 and the year-windowed stamp could not see it. The
-- wiki calls it Minor and publishes a $20,000 pool. Minor stays out of `tier`,
-- which is what keeps it out of the title set.
--
-- `CWL/2017 Season/Las Vegas Open` is the other event the date window newly
-- reaches. It already carries tier 2 and its pool from Liquipedia and keeps
-- both; only the wiki's word for it was missing.

UPDATE events SET tier = '1'
 WHERE name IN ('CWL/2016 Season/Australia-New Zealand/Stage 1/Playoffs',
                'CWL/2016 Season/Australia-New Zealand/Stage 2/Playoffs')
   AND tier IS NULL;

UPDATE events SET source_tier = 'Minor', prize_pool = 20000
 WHERE name = 'PlayStation Experience Invitational'
   AND source_tier IS NULL;

UPDATE events SET source_tier = 'Premier'
 WHERE name = 'CWL/2017 Season/Las Vegas Open'
   AND source_tier IS NULL;
