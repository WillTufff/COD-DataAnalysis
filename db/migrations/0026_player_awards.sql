-- 0026_player_awards: the individual awards, as external referents.
--
-- Liquipedia does not carry awards in a table of their own. Event MVP, Rookie
-- of the Year and the All-Star selections are `placement` rows with
-- `opponenttype` solo and `mode` award_individual, which is why the pull has
-- been fetching all 158 of them for some time and none of them have landed:
-- `event_placements` is keyed on a team, and the loader drops every non-team
-- row at the door rather than mint a team named after a player.
--
-- **Keyed on the season, not the event.** The selections that matter as
-- referents live on `Call_of_Duty_League/Season_N/Regular_Season` pages, and
-- six of those seven pages have no local event -- the regular season is a
-- hundred-odd fixtures here, not one row. Keying on the event would have
-- dropped six seasons of the thing this table exists for. `event_id` is
-- filled when the page does resolve, which is the case for the per-event MVPs.
--
-- **`player_id` is nullable on purpose.** The table records the award, not our
-- success at matching it. Sixteen of the sixty-eight awarded names do not
-- resolve against `players`, most of them from amateur events outside the
-- rated record, and a row that stores the handle and no id is what lets a
-- reader see the shortfall instead of guessing at it.
--
-- **`award` is normalized and `raw_award` is kept.** The source string is not
-- stable across seasons: 2022 says `CDL First All-Star Team` and 2023 says
-- `CDL 2023 Team of The Year` for the same selection. Both columns ship so the
-- normalization can be audited against its input.

CREATE TABLE player_awards (
  pagename    text NOT NULL,
  raw_award   text NOT NULL,
  handle      text NOT NULL,
  award       text NOT NULL,
  season_id   integer NOT NULL REFERENCES seasons(id),
  player_id   integer REFERENCES players(id),
  event_id    integer REFERENCES events(id),
  awarded_on  date,
  data_source text NOT NULL,
  PRIMARY KEY (pagename, raw_award, handle),
  CONSTRAINT player_awards_data_source_check CHECK (data_source = 'lpdb')
);

CREATE INDEX idx_player_awards_player ON player_awards (player_id);
CREATE INDEX idx_player_awards_season ON player_awards (season_id, award);

COMMENT ON COLUMN player_awards.award IS
  'Normalized kind: first_team, second_team, roty, rs_mvp, event_mvp, fmvp, '
  'captains_mvp, mode_best, or unmapped. An unrecognised source string is '
  'stored as unmapped and reported by the loader, never bucketed as other.';

COMMENT ON COLUMN player_awards.player_id IS
  'NULL when the awarded handle does not resolve against players. The row is '
  'kept so the unresolved set can be published by name.';

COMMENT ON COLUMN player_awards.event_id IS
  'NULL when the awarding page has no local event, which is the case for six '
  'of the seven CDL regular seasons. The season is always known.';
