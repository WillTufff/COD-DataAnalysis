-- 0009_org_name_unique: make orgs.name a natural key.
--
-- The archive names organisations only through their team brands, so the
-- importer resolves an org by name on every run (there is no upstream id to
-- key on). Without a unique constraint an insert-if-missing lookup races
-- itself across reruns and duplicates the org, which would silently split a
-- lineage back into two rating curves. 0001 made liquipedia_page unique, but
-- that column is NULL for every org derived from this archive.
ALTER TABLE orgs ADD CONSTRAINT orgs_name_key UNIQUE (name);
