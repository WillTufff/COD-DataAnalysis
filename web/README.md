# cdlhub web

The Next.js site (App Router, TypeScript, Tailwind, drizzle-orm). It reads the
Postgres database that the pipeline and analytics layers populate — see the
[root README](../README.md) for full setup, including migrations and data import.

```sh
npm install
npm run dev   # http://localhost:3000
```

`DATABASE_URL` comes from `../.env` (copy `../.env.example`; the defaults match
`docker-compose.yml`). Player and team pages are prerendered at build time, so
`npm run build` needs a populated database; the filterable views (report builder,
rounds, loadout meta) render per request.

Other scripts: `npm test` runs the Vitest suite over `lib/reports/` — how a URL
becomes a report, the export matrix, and the CSV/JSON/XML serializers, none of
which need a database. `npm run db:check` verifies the schema matches what the
site expects; `npm run typecheck` and `npm run lint` do what they say.
