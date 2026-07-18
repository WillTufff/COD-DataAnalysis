-- Reference data: real, factual (titles, modes, BO6 competitive maps).
BEGIN;

-- Dev fixtures are a full reset: seeds own every row in these tables.
TRUNCATE game_player_stats, event_placements, games, series, stages, events,
  seasons, roster_stints, player_aliases, players, teams, orgs, maps,
  game_modes, titles, ingest_runs RESTART IDENTITY CASCADE;

INSERT INTO titles (name, short_name, release_year, era) VALUES
  ('Call of Duty 4: Modern Warfare', 'CoD4', 2007, 'early'),
  ('Modern Warfare 2',               'MW2',  2009, 'early'),
  ('Black Ops',                      'BO1',  2010, 'early'),
  ('Modern Warfare 3',               'MW3',  2011, 'early'),
  ('Black Ops II',                   'BO2',  2012, 'early'),
  ('Ghosts',                         'Ghosts', 2013, 'mlg'),
  ('Advanced Warfare',               'AW',   2014, 'mlg'),
  ('Black Ops III',                  'BO3',  2015, 'cwl'),
  ('Infinite Warfare',               'IW',   2016, 'cwl'),
  ('WWII',                           'WWII', 2017, 'cwl'),
  ('Black Ops 4',                    'BO4',  2018, 'cwl'),
  ('Modern Warfare (2019)',          'MW19', 2019, 'cdl'),
  ('Black Ops Cold War',             'BOCW', 2020, 'cdl'),
  ('Vanguard',                       'VG',   2021, 'cdl'),
  ('Modern Warfare II',              'MWII', 2022, 'cdl'),
  ('Modern Warfare III',             'MWIII', 2023, 'cdl'),
  ('Black Ops 6',                    'BO6',  2024, 'cdl'),
  ('Black Ops 7',                    'BO7',  2025, 'cdl')
ON CONFLICT (name) DO NOTHING;

INSERT INTO game_modes (name, slug) VALUES
  ('Hardpoint',        'hardpoint'),
  ('Search & Destroy', 'search-and-destroy'),
  ('Control',          'control'),
  ('Domination',       'domination'),
  ('Uplink',           'uplink'),
  ('Capture the Flag', 'capture-the-flag'),
  ('Blitz',            'blitz')
ON CONFLICT (slug) DO NOTHING;

-- BO6 competitive map pool (2025 CDL season)
INSERT INTO maps (name, title_id)
SELECT m.name, t.id FROM (VALUES
  ('Hacienda'), ('Protocol'), ('Red Card'), ('Rewind'),
  ('Skyline'), ('Vault'), ('Dealership')
) AS m(name)
JOIN titles t ON t.short_name = 'BO6'
ON CONFLICT (name, title_id) DO NOTHING;

COMMIT;
