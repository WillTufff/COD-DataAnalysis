# cdlhub

cdlhub is a free, open-source analytics project for competitive Call of Duty. The goal
is to model and interpret the stats rather than just list them: era-adjusted comparisons
across titles, rating systems whose backtests are published alongside them, career-arc
modeling, and generated findings, covering the CWL era through the current CDL.

Everything about how the numbers are produced is written up in
[docs/methodology.md](docs/methodology.md).

## Where the project is now

The full loop runs end to end on real data, locally:

- **Data.** Three sources, 2017 through 2026: 98,384 player-map rows across 3,027
  decided series and 99 events. The Activision `cwl-data` archive supplies 44,552 of
  those rows and is committed to this repository (2017 Champs on IW, the 2018 WWII
  season, the 2019 BO4 season); the CDL seasons 2020-2026 come from the Cito API,
  which carries Breaking Point match data, and are held locally rather than
  redistributed. Liquipedia's LPDB supplies the structure around them: 99 enriched
  events, 1,068 placements with prize money, 3,486 roster stints, 4,898 transfers and
  player bios.
- **Models.** Era adjustment (cohort z-scores and percentiles per season and mode,
  minimum 8 maps), Elo (K=32), Glicko-2 (τ=0.5), an open composite player rating fit on
  what actually wins maps and published as the posterior of a two-level model, so its
  interval is the model's rather than a bootstrap of its point estimate, and a
  win-probability model whose result was a published null.
  Walk-forward backtests over 3,027 decided series put Elo at 0.2218 Brier / 64.4%
  accuracy and Glicko-2 at 0.2272 / 63.6%. Those gaps are paired and carry intervals:
  Elo's edge over Glicko-2 (−0.0054, 95% CI −0.0086 to −0.0026) separates, the
  win-probability model's edge over Glicko-2 does not, and the null it publishes comes
  with a power statement saying what size of effect the archive could have found. Every model write is versioned through `model_runs` and replaced on rerun;
  superseded runs of the same model are pruned so only what a run published survives.
- **Series dynamics.** What a 1-0 lead is worth, measured against an exact enumeration
  of a race to three with no memory in it. The map-1 winner takes 75.9% of 1,272
  best-of-fives, which against ratings alone looks like momentum: too many sweeps, too
  few deciders. Modeling the sequence with a per-series quality offset the ratings did
  not have puts the carryover at −0.8 points of map win probability (95% CI −5.5 to
  +3.9), against +10.9 from the same data fitted the ordinary way.
- **Rounds.** The first model built on the kill feed: given the survivor count in a Search
  and Destroy round right now, what is each side worth? Sixteen non-terminal states from
  ~104,000 observations, walk-forward by event. Round odds track the *ratio* of survivors
  rather than the difference: being up one is .716 at 4v3 and .846 at 2v1. Two
  add-a-feature nulls are published with their intervals: neither elapsed time nor a plant
  indicator improves out-of-sample Brier, the second for a structural reason the feed
  cannot fix. The round is also drawn along its own clock (survivors, win probability, the
  model against the outcome, and how much of the sample is still playing, on one shared
  time axis). Half of all rounds are over by 60 seconds, and at thirty the eventual winner
  is ahead by under two thirds of a player.
- **Player style.** Are roles a taxonomy or a continuum? With the composite rating
  projected out (style and quality are nearly orthogonal) and only metrics every season
  can reach, no partition beats a cloud with no clusters in it, in either era: over
  2017-2019 the best-separated k scores a silhouette of 0.286 where an unclustered
  Gaussian of the same shape scores 0.251 to 0.305. Bootstrap stability of 0.961 looks
  convincing until the same null reproduces itself just as well. So players are published
  as positions on continuous axes rather than as archetype labels. The fit runs once per
  era, because the column sets do not overlap enough to share a basis, and the axes are
  never compared across that seam.
- **Metric layer.** 104 derived metrics per player, season and mode, plus team
  style metrics and loadout meta aggregates, all era-scored against their own cohort.
  Which seasons a metric covers is measured from the data rather than declared, so
  columns a source records but never populated are reported as gaps instead of being
  published as zeros. The two box-score archives do not carry the same columns, and
  the sharpest case is map duration: the CDL-era source has no map clock, so every
  per-10-minute metric stops at 2019 and a per-map counterpart takes over from 2020.
  A cohort is never given both forms of one quantity.
- **Site.** An analysis overview (Elo race, era-adjustment strips, raw vs. adjusted
  leaderboard), team and player pages, a report builder over every published metric with
  CSV/XLSX export, a rounds page for the kill-feed tier, a maps page tracking the map
  pool season by season, a loadout meta page, a findings
  ledger, and the methodology write-up with an auto-generated metric glossary. Player and
  team pages are prerendered; the filterable views render per request.
- **Uncertainty.** Every rating the site publishes carries the interval its model
  computed: the composite rating's posterior SD on the player page, the rating board and
  the player index; the era model's standard error on the career arc, the season tables
  and the home leaderboard; Glicko-2's RD on the team pages. Bands are ±1.96 SD on a
  domain shared across the table or plot, so overlap is legible. On the top-twenty rating
  board, eight of the nineteen chasing seasons reach the leader's interval, and the order
  between them is not a claim the model can make.

The site covers 2017 to 2026. Career modeling (aging curves, peak detection) is
specified in the methodology but not yet implemented.

## Layout

| Path | What |
|---|---|
| `pipeline/` | Python 3.12 (uv) ingestion: CWL archive importer, Cito client (scope/backfill/load), Liquipedia LPDB client (probe/pull/load), and the weekly `refresh` top-up. Validated transforms, Postgres upserts, quality gate. |
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

# 3. Import the CWL 2017-2019 archive (committed to this repo)
cd pipeline && uv sync
uv run python -m cdlhub_pipeline.cwl_archive --reset

# 3b. CDL 2020-2026. Needs CITO_API_KEY and LPDB_API_KEY; the snapshots these
#     read are not committed, so this step fetches before it loads.
uv run python -m cdlhub_pipeline.cito scope
uv run python -m cdlhub_pipeline.cito backfill
uv run python -m cdlhub_pipeline.cito load
uv run python -m cdlhub_pipeline.lpdb pull
uv run python -m cdlhub_pipeline.lpdb load    # always after the cito load

uv run python -m cdlhub_pipeline.quality               # quality gate + coverage

# 4. Fit models, write versioned outputs and findings
cd ../analytics && uv sync
uv run python -m cdlhub_analytics.run_all

# 5. Web
cd ../web && npm install && npm run dev   # http://localhost:3000
```

Copy `.env.example` to `.env` and adjust if needed; the defaults match
`docker-compose.yml`.

### Keeping a running season current

During a season, one command re-scopes, snapshots whatever is new and reloads both
sources in the order they have to run in:

```sh
cd pipeline
uv run python -m cdlhub_pipeline.refresh          # --dry-run to fetch and stop before loading
cd ../analytics && uv run python -m cdlhub_analytics.run_all
```

Every step is idempotent, so it is safe on a timer and safe to re-run after a failure.
The order is not arbitrary: reloading the CDL box scores re-nulls the map results that
the Liquipedia pass repaired, so `lpdb load` always runs after `cito load`, and
`refresh` enforces that.

It also watches one thing that would otherwise fail silently. Some current-season
fixtures arrive with another match's player-stats payload attached; the transform
quarantines those automatically, which thins stats coverage for the running season
without breaking anything. `refresh` compares the quarantine counts against the previous
run and exits non-zero if a class of them grew.

### Tests and checks

Each half runs its own checks. `pipeline/` and `analytics/` use `uv run ruff
check .`, `uv run ruff format --check .`, `uv run mypy` and `uv run pytest`;
`web/` uses `npm run lint`, `npm run typecheck` and `npm test` (Vitest, over the
report builder's URL resolution, export matrix and serializers). CI runs all of
them, plus a build of the site and a full ingest of the committed snapshots that
re-checks the kill-feed reconciliation figures against real data.

The fast ones can run before every push:

```sh
git config core.hooksPath scripts/git-hooks
```

That takes about ten seconds and skips the slow gates, which stay in CI. Use
`git push --no-verify` to bypass it once.

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
- CDL 2020-2026 box scores come from the [Cito API](https://citoapi.com), which
  carries Breaking Point match data, used with attribution under its terms. That
  licence is not CC-BY-SA and the data is not redistributable, so the stored responses
  are **not** committed here (`pipeline/snapshots/cito/` is ignored) and only derived
  analysis — ratings, era-adjusted metrics, model outputs — is published. Requests are
  paced under the tier's limits, every response is snapshotted to disk, and a match is
  never fetched twice.
- Tournaments, placements, prize money, rosters, transfers and player bios come from
  [Liquipedia](https://liquipedia.net/callofduty) (CC-BY-SA 3.0) through the LPDB API,
  within the published rate limits (1 request / 5 s) and with an identifying
  User-Agent. No HTML scraping. Derived data is shared under CC-BY-SA 3.0. Responses
  are snapshotted to `pipeline/snapshots/lpdb/`, which is also not committed.
- Every row in the database carries the source it came from, so the per-source
  licensing rules above are enforced by a column rather than inferred from the season.
- Code is AGPL-3.0 (see [LICENSE](LICENSE)).
- The project will not build anything betting-related.
