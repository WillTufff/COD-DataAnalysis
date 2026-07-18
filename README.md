# cdlhub

cdlhub is a free, open-source analytics project for competitive Call of Duty. The goal
is to model and interpret the stats rather than just list them: era-adjusted comparisons
across titles, rating systems whose backtests are published alongside them, career-arc
modeling, and generated findings, covering the CWL era through the current CDL.

Everything about how the numbers are produced is written up in
[docs/methodology.md](docs/methodology.md).

## Where the project is now

The full loop runs end to end on real data, locally:

- **Data.** The Activision `cwl-data` archive is imported and committed: 44,552
  player-map rows across 1,310 decided series, covering 2017 Champs (IW), the 2018 WWII
  season, and the 2019 BO4 season.
- **Models.** Era adjustment (cohort z-scores and percentiles per season and mode,
  minimum 8 maps), Elo (K=32), and Glicko-2 (τ=0.5). Walk-forward backtests put Glicko-2
  at 0.2215 Brier / 65.4% accuracy and Elo at 0.2228 / 63.7%. Every model write is
  versioned through `model_runs` and replaced on rerun.
- **Site.** An analysis overview, a query explorer over player-season cohorts, a ratings
  page (Elo race, Glicko standings with RD, raw vs. adjusted leaderboard), a findings
  ledger, and the methodology write-up.

Two things are still open. CDL-era data needs Liquipedia LPDB API access, which is not yet in
place, so the site currently covers 2017 to 2019 only. And
the `career_curves` and `player_archetypes` tables exist but are empty; aging curves and
archetype clustering are the next modeling work.

## What comes next

1. CDL-era ingestion once LPDB access lands, followed by a first real rating run across
   both eras.
2. Career modeling: aging curves and archetypes, plus a `player_rating_v1` that ships
   with its backtest.
3. Public query API and player comparison.
4. CWL backfill to 2016 and cross-title era recalibration, which also unlocks the
   LAN-vs-online study since that needs the 2019 to 2022 span.

## Layout

| Path | What |
|---|---|
| `pipeline/` | Python 3.12 (uv) ingestion: CWL archive importer and, later, the Liquipedia LPDB client. Validated transforms, Postgres upserts, quality gate. |
| `analytics/` | Python 3.12 (uv) modeling: era adjustment, Elo/Glicko-2, backtests, finding generation. Outputs versioned via `model_runs`. |
| `web/` | Next.js site (App Router, TypeScript, Tailwind, drizzle-orm) |
| `db/migrations/` | Plain SQL migrations, numbered and forward-only |
| `db/seeds/` | Synthetic fixtures for CI |
| `.github/workflows/` | CI, nightly ingest, manual backfill |

## Local development

Requirements: Docker, Node 22+, [uv](https://docs.astral.sh/uv/).

```sh
# 1. Postgres
docker compose up -d db

# 2. Migrations
./db/migrate.sh          # applies db/migrations/*.sql in order

# 3. Import the CWL 2017-2019 archive
cd pipeline && uv sync
uv run python -m cdlhub_pipeline.cwl_archive --reset
uv run python -m cdlhub_pipeline.quality               # quality gate + coverage

# 4. Fit models, write versioned outputs and findings
cd ../analytics && uv sync
uv run python -m cdlhub_analytics.run_all

# 5. Web
cd ../web && npm install && npm run dev   # http://localhost:3000
```

Copy `.env.example` to `.env` and adjust if needed; the defaults match
`docker-compose.yml`.

### A note on the two datasets

The dev dataset is real. It lives in `pipeline/snapshots/cwl-archive/` and is
Activision's official box scores (BSD 3-Clause, notice retained in that directory).

`db/seeds/` is something else: synthetic fixtures with real player names, fictional
events, and generated stat lines, used only by CI's schema checks. Don't mix them. Both
the seed scripts and `--reset` truncate the database first, so whichever you run last is
what you have.

## Data sources, attribution, licensing

- CWL 2017-2019 box scores come from the Activision `cwl-data` archive (BSD 3-Clause,
  © Activision Publishing 2017; license retained in `pipeline/snapshots/cwl-archive/`).
- CDL-era statistics will come from [Liquipedia](https://liquipedia.net/callofduty)
  (CC-BY-SA 3.0) through the LPDB API, within the published rate limits and with an
  identifying User-Agent. No HTML scraping. Derived data is shared under CC-BY-SA 3.0.
- Code is AGPL-3.0 (see [LICENSE](LICENSE)).
- The project will not build anything betting-related.
</content>
</invoke>
