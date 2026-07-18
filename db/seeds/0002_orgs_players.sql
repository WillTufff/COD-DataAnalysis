-- DEV FIXTURES. Real org/team/player names for realistic search & UI work,
-- but roster groupings are approximate and NOT authoritative for any specific
-- season. Replaced entirely by the ingest pipeline in production.
BEGIN;

INSERT INTO orgs (name, liquipedia_page) VALUES
  ('OpTic',         NULL),
  ('FaZe',          NULL),
  ('Toronto Ultra', NULL),
  ('100 Thieves',   NULL)
ON CONFLICT DO NOTHING;

INSERT INTO teams (org_id, name, region, active_from)
SELECT o.id, t.name, t.region, t.active_from::date FROM (VALUES
  ('OpTic',         'OpTic Texas',   'NA', '2021-11-01'),
  ('FaZe',          'Atlanta FaZe',  'NA', '2019-10-01'),
  ('Toronto Ultra', 'Toronto Ultra', 'NA', '2019-10-01'),
  ('100 Thieves',   'LA Thieves',    'NA', '2019-10-01')
) AS t(org, name, region, active_from)
JOIN orgs o ON o.name = t.org;

INSERT INTO players (handle, country, role) VALUES
  ('Shotzzy', 'US', 'SMG'), ('Dashy', 'CA', 'AR'), ('Pred', 'AU', 'SMG'), ('Kenny', 'US', 'AR'),
  ('Simp', 'US', 'Flex'), ('aBeZy', 'US', 'SMG'), ('Cellium', 'US', 'AR'), ('Drazah', 'US', 'Flex'),
  ('CleanX', 'GB', 'SMG'), ('Insight', 'GB', 'AR'), ('Scrap', 'US', 'SMG'), ('Envoy', 'US', 'AR'),
  ('Ghosty', 'US', 'AR'), ('Kremp', 'US', 'SMG'), ('Gwinn', 'US', 'SMG'), ('JoeDeceives', 'US', 'AR');

-- a couple of alias rows so trigram search over aliases is exercised
INSERT INTO player_aliases (player_id, alias)
SELECT id, a.alias FROM players p
JOIN (VALUES ('Shotzzy','Shottzy'), ('Kenny','KennyS'), ('aBeZy','Tiny Terror')) AS a(handle, alias)
  ON a.handle = p.handle
ON CONFLICT DO NOTHING;

INSERT INTO roster_stints (player_id, team_id, role, start_date, source)
SELECT p.id, t.id, p.role, '2024-11-01'::date, 'dev-fixture'
FROM players p
JOIN teams t ON t.name = CASE
  WHEN p.handle IN ('Shotzzy','Dashy','Pred','Kenny')            THEN 'OpTic Texas'
  WHEN p.handle IN ('Simp','aBeZy','Cellium','Drazah')           THEN 'Atlanta FaZe'
  WHEN p.handle IN ('CleanX','Insight','Scrap','Envoy')          THEN 'Toronto Ultra'
  WHEN p.handle IN ('Ghosty','Kremp','Gwinn','JoeDeceives')      THEN 'LA Thieves'
END;

COMMIT;
