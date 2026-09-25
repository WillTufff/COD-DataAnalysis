-- Where in a competition a series was played: league play, an event's group
-- stage, its bracket, or its grand final.
--
-- `series.round_label` is kept as each source wrote it, in three vocabularies,
-- and a label alone does not say whether a series was league play. The 2020 CDL
-- home series labelled every weekend as a small tournament, and the 2017 Global
-- Pro League is labelled "Group A". The stage is derived from the round label
-- and a curated list of league events (`cdlhub_pipeline/leagues.json`), and the
-- LPDB load writes it for every series. NULL where the label says nothing.

ALTER TABLE series ADD COLUMN stage text
  CHECK (stage IN ('league', 'group', 'bracket', 'final'));

COMMENT ON COLUMN series.stage IS
  'league, group, bracket or final, from round_label and the curated league '
  'event list (stage.py). NULL where the round label decides nothing.';
