-- Who played for the team that placed: the roster of a team at one event.
--
-- `event_placements` says a team finished first. `roster_stints` says a player
-- belonged to a team between two dates. Neither says the thing a resume needs,
-- which is that this player was on that roster at that event. Deriving it from
-- the two costs a date-range join that is wrong whenever a stint is wider than
-- the event, and the pre-2017 sources publish a roster per tournament and no
-- date range at all.
--
-- So the roster is stored as the source publishes it. A row here is an
-- assertion about one event, never about a period, and building a stint out of
-- it would invent the start and end dates the source never gave.
--
-- data_source follows the same precedence as everywhere else: codwiki is tier
-- three and never overwrites a row another source holds.

BEGIN;

CREATE TABLE event_rosters (
  event_id    int NOT NULL REFERENCES events(id),
  team_id     int NOT NULL REFERENCES teams(id),
  player_id   int NOT NULL REFERENCES players(id),
  role        text,
  data_source text NOT NULL CHECK (data_source IN ('cwl_archive', 'cito', 'lpdb', 'codwiki')),
  PRIMARY KEY (event_id, team_id, player_id)
);

CREATE INDEX event_rosters_player_idx ON event_rosters (player_id);

COMMIT;
