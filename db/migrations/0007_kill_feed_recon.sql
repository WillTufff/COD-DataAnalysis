-- 0007_kill_feed_recon: the kill-feed reconciliation set as a queryable view.
--
-- A player-map (game, player) reconciles when its box-score death count equals
-- its NORMAL kill-feed death count. Box deaths exclude suicides and team kills,
-- so only death_kind='normal' is counted; this is the rule that makes WWII land
-- at exactly 100.00% (any misclassification would break that).
--
-- The view is the single source of truth for the exclusion: kill-feed metrics
-- join it and keep only `reconciled` rows, rather than each query re-deriving
-- the test. Failing player-maps are excluded, never patched. Scope is games
-- that actually have a feed (BO4 has box scores but no events, so it never
-- appears here).
CREATE VIEW kill_feed_recon AS
WITH feed_games AS (
  SELECT DISTINCT game_id FROM kill_events
),
normal_deaths AS (
  SELECT game_id, victim_id AS player_id, count(*) AS deaths
  FROM kill_events
  WHERE death_kind = 'normal'
  GROUP BY game_id, victim_id
)
SELECT
  gps.game_id,
  gps.player_id,
  t.short_name                       AS title,
  gm.slug                            AS mode,
  gps.deaths                         AS box_deaths,
  COALESCE(nd.deaths, 0)             AS feed_deaths,
  gps.deaths = COALESCE(nd.deaths, 0) AS reconciled
FROM game_player_stats gps
JOIN feed_games fg ON fg.game_id = gps.game_id
LEFT JOIN normal_deaths nd
  ON nd.game_id = gps.game_id AND nd.player_id = gps.player_id
JOIN games g    ON g.id = gps.game_id
JOIN game_modes gm ON gm.id = g.mode_id
JOIN series s   ON s.id = g.series_id
JOIN events e   ON e.id = s.event_id
JOIN seasons se ON se.id = e.season_id
JOIN titles t   ON t.id = se.title_id;
