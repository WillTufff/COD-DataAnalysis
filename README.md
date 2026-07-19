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
  minimum 8 maps), Elo (K=32), Glicko-2 (τ=0.5), an open composite player rating fit on
  what actually wins maps, and a win-probability model whose result was a published null.
  Walk-forward backtests put Glicko-2 at 0.2215 Brier / 65.4% accuracy and Elo at 0.2228
  / 63.7%. Every model write is versioned through `model_runs` and replaced on rerun.
- **Metric layer.** Around eighty derived metrics per player, season and mode, plus team
  style metrics and loadout meta aggregates, all era-scored against their own cohort.
  Which seasons a metric covers is measured from the data rather than declared, so
  columns the archive records but never populated are reported as gaps instead of being
  published as zeros.
- **Site.** An analysis overview, a query explorer over player-season cohorts, a stat
  explorer across every published metric, a loadout meta page, a ratings page (Elo race,
  Glicko standings with RD, raw vs. adjusted leaderboard), a findings ledger, and the
  methodology write-up with an auto-generated metric glossary.

Two things are still open. CDL-era data needs Liquipedia LPDB API access, which is not yet in
place, so the site currently covers 2017 to 2019 only. And
the `career_curves` and `player_archetypes` tables exist but are empty; aging curves and
archetype clustering are the next modeling work.

## What comes next

1. CDL-era ingestion once LPDB access lands, followed by a first real rating run across
   both eras.
2. Career modeling: aging curves and archetype clustering, both of which build on the
   metric layer.
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
- The 2017-2018 structured event feeds (the kill feed behind the trade, clutch, and
  man-advantage metrics) come from the same repository under the same BSD 3-Clause
  licence, in `pipeline/snapshots/cwl-structured/`. BO4 2019 ships box scores with no
  events, so that tier is 2017-2018 only. The upstream repo was taken down, so both
  tiers are pinned to Software Heritage snapshot `c5ee2cd04d10971b39685fc55da4747d04a0ba04`
  and revision `5b7eb907b63ab4a53ed7fd2987459f3bf28c9c21`; `pipeline/scripts/fetch_structured.py`
  re-fetches and re-verifies against those ids.
- CDL-era statistics will come from [Liquipedia](https://liquipedia.net/callofduty)
  (CC-BY-SA 3.0) through the LPDB API, within the published rate limits and with an
  identifying User-Agent. No HTML scraping. Derived data is shared under CC-BY-SA 3.0.
- Code is AGPL-3.0 (see [LICENSE](LICENSE)).
- The project will not build anything betting-related.
</content>
</invoke>
