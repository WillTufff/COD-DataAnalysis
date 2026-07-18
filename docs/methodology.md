# Methodology

This is the public specification of how cdlhub produces its numbers. The aim is that
anyone can check the work, so it is written to be detailed enough to argue with.

The project's premise is that a raw stat is not very informative on its own. A K/D
means little without knowing the scoring environment it was earned in, how it compares
to the player's peers that season, and how confident we should be in it. So every
figure published here is meant to arrive with its context: percentile, era adjustment,
uncertainty, and trend.

**Status.** Sections are marked shipped or planned. As of now the era adjustment,
the two team rating systems, the open player rating, the series win-probability
model, the backtest harness, and the finding generator are running on real data.
Career modeling and meta analysis are specified but not yet implemented, and the
site covers 2017 to 2019 because CDL-era data needs Liquipedia API access that is
not yet in place.

## Principles

1. **Analysis over reference.** The work worth doing is the era-adjusted comparison,
   the open rating system, the aging curve, the roster-change study. Those don't
   currently exist for competitive Call of Duty.
2. **Methodological transparency.** Every model's spec, code, and backtest is
   published, including calibration curves, so the ratings can be audited rather than
   taken on trust.
3. **Interpretation-first visualization.** Charts are annotated to make a point, every
   stat links to the evidence beneath it, and claims carry their uncertainty.

**On scale and model choice.** The dataset is thousands of series and tens of thousands
of stat lines. At that size the appropriate tools are hierarchical and Bayesian
statistics, regression, gradient boosting, and clustering rather than deep learning.
That has a useful side effect: the models stay explainable, so this page can actually
explain them.

## Data sources

| Source | Coverage | License |
|---|---|---|
| [Activision `cwl-data` archive](https://github.com/Activision/cwl-data) | CWL 2017-2019 box scores, 44,552 player-game rows across 18 tournaments | BSD 3-Clause, © Activision Publishing 2017 |
| [Liquipedia](https://liquipedia.net/callofduty) via LPDB API | CDL-era results and metadata (not yet ingested) | CC-BY-SA 3.0 |

The CWL archive was captured live from tournament host consoles, then cleaned and
published by Activision, which makes it about as close to a primary source as this
sport has. The upstream license and README are retained verbatim alongside the data in
`pipeline/snapshots/cwl-archive/`.

Liquipedia data will be accessed only through the LPDB API, never by scraping HTML,
within the published rate limits (1 request per 2 seconds; `parse` and `ask` 1 per 30
seconds), with an identifying User-Agent and caching so unchanged data is not
re-requested. Pages using their data carry visible attribution, and derived data is
shared back under CC-BY-SA 3.0.

Project code is licensed AGPL-3.0.

### Completeness is published, not guessed

Map-level statistics do not exist for much of the pre-2018 record. The schema can
represent "series known, stats unknown" directly, and no value is ever fabricated to
fill a gap. Missing data is stored as NULL, and every aggregate carries a
`completeness` figure: the share of underlying maps that have full box scores. A
per-season coverage report is generated after each ingest and published rather than
buried.

## Tier 1: Era adjustment (shipped)

Everything else stands on this. Raw stats are not comparable across titles, because a
1.05 K/D in 2017 IW Hardpoint and a 1.05 K/D in 2019 BO4 Hardpoint were earned in
different scoring environments.

A cohort is every qualified player in the same (season, mode), where qualified means at
least 8 maps played. Each player-season-mode aggregate gets a z-score and a percentile
within its cohort, which makes those two K/Ds comparable. Rows are written for all
players, not just qualified ones; the z-scores and percentiles are computed relative to
the qualified cohort, and `maps_played` is exposed so consumers can filter.

Objective metrics are mode-specific: hill seconds per 10 minutes of map time for
Hardpoint, first bloods plus plants plus defuses per map for Search and Destroy, zone
captures for Control, flag captures plus returns for Capture the Flag, and uplink
points for Uplink.

This adjustment drives the cross-era leaderboards and the percentile coloring
throughout the site. Player pages show raw and adjusted values side by side so the
adjustment stays visible instead of disappearing inside a number.

Splitting cohorts further by LAN versus online is a planned refinement. It needs the
2019-2022 span to be meaningful, which the current dataset does not cover.

## Tier 2: Rating systems

**Team strength over time (shipped).** Elo (K=32) and Glicko-2 (τ=0.5) are fit over the
full history at series level. Ratings are org-lineage-aware, so a team's curve runs
continuously through rebrands rather than resetting. Glicko-2's rating deviation gives
the uncertainty bands shown on the ratings page. A map-margin-weighted variant is
planned as a sensitivity check.

**Series win probability, `winprob_v1` (shipped).** Glicko-2 is the strongest
baseline in the table below, so rather than another rating system this model asks a
sharper question: given the ratings, does anything else carry information about who
wins a series? Its features, all computed strictly before each series, are the
walk-forward Glicko-2 and Elo win probabilities (as logits), the combined Glicko-2
rating deviation, each team's win rate over its last ten series, and a shrunken
head-to-head record. The model is L2-regularized logistic regression, refit on an
expanding window every 50 series; until 200 series of history exist it passes the
Glicko-2 probability through unchanged, so its backtest covers the same series as the
baselines and any improvement is attributable to the added features.

The answer, over 2017-2019, is no: recent form and head-to-head history do not
improve on team strength (see the table below — the difference is noise). The learned
form coefficient is approximately zero, which is this site's first published test of
the momentum narrative. A null, backtested and reported, is a result.

**Validation (shipped).** Models are evaluated by walk-forward backtest, which is to
say each prediction is made using only data available before that series. Current
results, over the full 2017-2019 record:

| Model | Brier | Accuracy |
|---|---|---|
| Glicko-2 | 0.2215 | 65.4% |
| winprob_v1 | 0.2217 | 64.4% |
| Elo | 0.2228 | 63.7% |

Glicko-2 is ahead on both, though the margin is narrow enough that it should not be
read as settled. Brier score, log loss, accuracy, and calibration curves are published
for every model version.

Model outputs are versioned against the run that produced them, recording code version,
hyperparameters, and training window. A rerun replaces a whole run rather than editing
rows in place, so any published number can be traced back to the exact code and data
window that generated it.

**Open player rating, `player_rating_v1` (shipped).** The composite rating, built in
four steps, each of them auditable:

1. *Learn what wins maps.* For every (season × mode), each map is one observation:
   the difference between the two teams' per-10-minute profiles (kills, deaths,
   assists, mode objective), standardized, regressed against which team won the map.
   The regression is L2 logistic (λ=1 on standardized features), fit by iteratively
   reweighted least squares in ~40 lines of published numpy — no black box. Cohorts
   with fewer than 40 maps are not fit. The learned weights are stored with the run
   and published: they are data-derived answers to "how much was a one-SD edge in
   hill time worth, against the same edge in kills, in this title?" One caveat for
   reading them: in respawn modes a team's kills mirror its opponent's deaths almost
   exactly, so those two coefficients are near-collinear and the ridge penalty splits
   their shared weight — read them jointly as slaying.
2. *Score players with those weights.* Each player-season-mode aggregate is z-scored
   against its qualified cohort (≥ 8 maps, as in the era adjustment) and dotted with
   the mode's weights, then standardized so modes land on a common scale.
3. *Shrink small samples.* Scores are pulled toward the league mean by
   m / (m + 15), where m is maps played — empirical-Bayes partial pooling, so a hot
   12-map season cannot outrank a great 200-map one.
4. *Normalize.* The season rating blends mode scores weighted by maps played, scaled
   so the qualified cohort averages 1.00 with a league SD near 0.15. The published
   uncertainty is a map-resampling bootstrap (200 draws, fixed seed).

Its validation is walk-forward within each (season × mode): every event's maps are
predicted using weights trained only on earlier events (4,204 predictions, Brier
0.054, accuracy 92.5%). Read that number for what it is: this is a *value* model
scored on same-map box scores, not a forecast — the backtest establishes that the
learned weights generalize across events rather than memorizing them, which is the
property the rating stands on.

## Tier 3: Career modeling (planned)

The tables exist; the models are not yet written.

- **Aging curves.** Hierarchical fit of adjusted performance against age, or against
  career-season index where birthdate is unknown, giving each active player a position
  on the curve and the league a peak-age estimate with a credible interval.
- **Peak and breakout detection.** Changepoint analysis on rolling adjusted rating,
  flagging career inflections with their magnitude in standard deviations.
- **Archetype clustering.** k-means or GMM on standardized per-mode stat profiles
  (engagement share, objective share, first-blood rate, hill-time share), producing
  named archetypes per era and letting a career's archetype drift be tracked.

## Tier 4: Meta and environment analysis (planned)

- **Map and mode analysis.** Scoring environments per map, side and streak effects
  where derivable, map-pool comparisons across eras.
- **LAN versus online.** A paired within-player comparison across the 2020-2022 online
  boundary, which is one of the few natural experiments available in esports, reported
  as effect sizes with confidence intervals. Needs data the project does not yet have.
- **Series dynamics.** P(win series | won map 1) by era and series length, comeback and
  sweep rates, and a direct test of momentum claims: is there autocorrelation beyond
  team strength? The answer gets published either way.
- **Roster-change event studies.** Performance k series before and after a move against
  matched controls, reporting the distribution of chemistry effects, including when the
  effect turns out to be null.

## Tier 5: Finding generation (shipped)

A layer of rules and statistics scans model outputs after every run and emits ranked,
plain-English findings in eight kinds: trends, outliers, milestones, era context,
head-to-head edges, and — reading the newer models' outputs directly — what-wins-maps
weight comparisons per (season × mode), the top open-rating seasons, and published
model nulls such as the momentum test. There are currently 151. Each carries the
numbers backing it and a link into the evidence view, so any claim on the site can be
traced to the data that produced it. These are generated from model output by fixed
rules, not written by hand and not written by a language model.

## Publishing rules

A model ships only when all four of these hold:

1. Its spec is written on this page.
2. Its code lives in `analytics/src` with tests.
3. Its backtest or sensitivity analysis is stored and published.
4. Its outputs are written through a versioned model run.

Notebooks are for exploration. Nothing ships from a notebook.

## Non-goals

These are permanent commitments rather than gaps in the roadmap.

- **Nothing betting-related.** No odds, no bookmaker integrations, no wager-framed
  predictions. Model predictions are published as educational model evaluation with
  backtests attached: report cards, not picks.
- **No replication of Liquipedia's UX.** No bracket pages, standings pages, or
  tournament coverage pages. Raw results appear only as thin drill-down views
  supporting an analysis.
- Also out of scope: fantasy, forums, news and editorial, live scores, user accounts.
</content>
</invoke>
