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
  what actually wins maps and published as the posterior of a two-level model, so its
  interval is the model's rather than a bootstrap of its point estimate, and a
  win-probability model whose result was a published null.
  Walk-forward backtests over 1,310 decided series put Elo at 0.22281 Brier / 63.4%
  accuracy and Glicko-2 at 0.22874 / 63.4%. Those gaps are paired and carry intervals:
  Elo's edge over Glicko-2 (−0.0059, 95% CI −0.0111 to −0.0010) separates, the
  win-probability model's edge over Glicko-2 does not, and the null it publishes comes
  with a power statement saying what size of effect the archive could have found. Every model write is versioned through `model_runs` and replaced on rerun;
  superseded runs of the same model are pruned so only what a run published survives.
- **Series dynamics.** What a 1-0 lead is actually worth, measured against an exact
  enumeration of a race to three with no memory in it. The map-1 winner takes 75.9% of
  1,272 best-of-fives, and against ratings alone that looks like momentum — too many
  sweeps, too few deciders. Model the sequence with a per-series quality offset the
  ratings did not have and the carryover goes to −0.8 points of map win probability
  (95% CI −5.5 to +3.9), against +10.9 from the same data fitted the ordinary way.
- **Rounds.** The first model built on the kill feed: given the survivor count in a Search
  and Destroy round right now, what is each side worth? Sixteen non-terminal states from
  ~104,000 observations, walk-forward by event, and the regularity worth naming is that
  round odds track the *ratio* of survivors rather than the difference — being up one is
  .716 at 4v3 and .846 at 2v1. Two add-a-feature nulls are published with their intervals:
  neither elapsed time nor a plant indicator improves out-of-sample Brier, the second for a
  structural reason the feed cannot fix. The round is also drawn along its own clock —
  survivors, win probability, the model against the outcome, and how much of the sample is
  still playing, on one shared time axis. Rounds turn out not to be decided early so much
  as leaned on early: half are over by 60 seconds, and at thirty the eventual winner is
  ahead by under two thirds of a player.
- **Player style.** Whether roles are a taxonomy or a continuum, asked properly. With the
  composite rating projected out (it explains 11.7% of the variance, so style and quality
  are nearly orthogonal) and only metrics every season can reach, no partition beats a
  cloud with no clusters in it: the best-separated k scores a silhouette of 0.286 where an
  unclustered Gaussian of the same shape scores 0.251 to 0.305. Bootstrap stability of
  0.961 sounds convincing and is not — the same null reproduces itself just as well. So
  players are published as positions on the four axes Horn's parallel analysis retains,
  not as archetype labels.
- **Metric layer.** 93 derived metrics per player, season and mode, plus team
  style metrics and loadout meta aggregates, all era-scored against their own cohort.
  Which seasons a metric covers is measured from the data rather than declared, so
  columns the archive records but never populated are reported as gaps instead of being
  published as zeros.
- **Site.** An analysis overview (Elo race, era-adjustment strips, raw vs. adjusted
  leaderboard), team and player pages, a report builder over every published metric with
  CSV/XLSX export, a rounds page for the kill-feed tier, a loadout meta page, a findings
  ledger, and the methodology write-up with an auto-generated metric glossary. Player and
  team pages are prerendered; the filterable views render per request.
- **Uncertainty is drawn, not stored and hidden.** Every rating the site publishes
  carries the interval its model computed: the composite rating's posterior SD on the
  player page, the rating board and the player index; the era model's standard error on
  the career arc, the season tables and the home leaderboard; Glicko-2's RD on the team
  pages. Bands are ±1.96 SD on a domain shared across the table or plot, so overlap is
  legible — on the top-twenty rating board, eight of the nineteen chasing seasons reach
  the leader's interval, and the order between them is not a claim the model can make.

Two things are still open. CDL-era data needs Liquipedia LPDB API access, which is not yet in
place, so the site currently covers 2017 to 2019 only. And
the `career_curves` table exists but is empty; aging curves are the next modeling work.

## What comes next

1. CDL-era ingestion once LPDB access lands, followed by a first real rating run across
   both eras.
2. Career modeling: aging curves and peak detection, both of which build on the
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
  The ingestion design assumes the published 60 requests/hour LPDB limit: a nightly
  incremental pull of matches new since the last run, every response persisted to
  Postgres so nothing is ever fetched twice, and one-off backfills throttled well
  under the limit and spread across days rather than run as a burst.
- Code is AGPL-3.0 (see [LICENSE](LICENSE)).
- The project will not build anything betting-related.
