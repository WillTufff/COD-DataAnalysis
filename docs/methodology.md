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
| Same archive, structured event feeds | 2017-2018 kill feeds (Infinite Warfare, WWII); BO4 games carry no events | BSD 3-Clause (same repository) |
| [Liquipedia](https://liquipedia.net/callofduty) via LPDB API | CDL-era results and metadata (not yet ingested) | CC-BY-SA 3.0 |

The CWL archive was captured live from tournament host consoles, then cleaned and
published by Activision, which makes it about as close to a primary source as this
sport has. The upstream license and README are retained verbatim alongside the data in
`pipeline/snapshots/cwl-archive/`. Both the box scores and the structured event feeds
come from that one repository and fall under its BSD 3-Clause licence.

The upstream repository was later taken down, so both tiers are recovered from Software
Heritage and pinned to snapshot `c5ee2cd04d10971b39685fc55da4747d04a0ba04` and revision
`5b7eb907b63ab4a53ed7fd2987459f3bf28c9c21` of `github.com/Activision/cwl-data`.
`pipeline/scripts/fetch_structured.py` re-fetches the event tarballs and re-hashes the
box-score CSVs against that same revision, so both tiers are reproducibly one version of
the source.

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

## Tier 1b: Metric layer (shipped)

The archive measures far more than kills and deaths. The metric layer turns every
measured column into a published, era-scored metric, so a player's season can be read
across roughly eighty different lenses instead of four.

Metrics are stored in long form, one row per player, season, mode, and metric, each
carrying its own qualification denominator. That denominator is the honest sample size
for that metric: maps for rate-per-map statistics, rounds for Search and Destroy round
rates, kills for kill-denominated shares, shots for accuracy. Qualification thresholds
are 8 maps, 50 rounds, 100 kills, and 1,000 shots. Rows below the threshold are still
written and still scored against the qualified cohort, so a small sample can be shown
and labelled rather than hidden.

Two rules keep the numbers honest. First, numerators and denominators are summed across
a player's maps and divided once, so a season rate is never the average of per-map
rates. A player with one quiet twenty-minute map and one loud five-minute map has one
true rate, not the mean of two. Second, a metric is only published for a title whose
data actually supports it.

That second rule is enforced by measurement, not by a hand-written table. Each metric
declares the source columns it reads, and the pipeline counts how many rows carry a
non-zero value for each column in each title. A column counts as tracked once at least
twenty of its rows are non-zero. The threshold is an absolute floor rather than a
percentage on purpose: genuinely rare events, like four-kill rounds at roughly one
percent of rounds, must stay published, while a column that exists in the file but was
never populated must not. Several columns fall into that second group. Black Ops 4
records fields for time alive and for kills that were not immediately answered, but both
are zero on all 19,120 of its rows; WWII does the same for hill captures and sneak
defuses; Black Ops 4 shots and hits are populated on five rows out of 19,120. Treating
those as data would publish a whole season of zeros as though it were a finding. They
are listed on the methodology page instead, and the metrics that depend on them simply
do not exist for those seasons.

The catalog itself, including each metric's formula, unit, direction, threshold, and
measured season coverage, ships as an artifact of the same run that computes the values.
The stat explorer and the metric glossary both render from it, so a definition and a
number cannot drift apart.

Team metrics use the same machinery with the roster as the subject, and cover map and
round win rates, average Hardpoint margin, and three measures of how a roster spreads
its work: the Gini coefficient of hill time across the four players, the Herfindahl
index of first bloods, and the spread of kill shares. Those describe style, not quality.
A roster that shares hill duty evenly is not thereby better than one that assigns a
specialist.

## Tier 1c: Structured event tier (shipped)

Underneath every box score for 2017 and 2018 sits a full event feed: every kill with its
attacker, victim, weapon, position and game clock, plus round-boundary scores. The 2019
Black Ops 4 season shipped box scores with empty event lists, so this tier is a
2017-2018 story and nothing here is published for BO4.

Two death-event shapes are normalized into one kill feed. Infinite Warfare spreads the
attacker across flat fields with three-dimensional positions and a per-kill distance;
WWII nests the attacker in an object with two-dimensional positions and no distance. The
importer reads both from the compressed tarballs in place and resolves every handle
against that game's own box-score roster, which also supplies the team membership used to
tell a team kill from a real one.

Nothing from the feed is trusted until it reconciles with the box score. For every
(game, player) the feed's normal-death count — suicides and team kills excluded — must
equal the box-score death total. WWII reconciles exactly, at 100.00% of 22,728
player-maps; the same rule holds Infinite Warfare to 94.97% of 2,384, the residual being
feed deaths the box never recorded. Player-maps that fail are excluded from every
kill-feed metric through a single queryable set, never patched. The full summary ships as
an artifact, and the WWII figure is a hard check in CI, so a regression in the importer
or the death classification fails the build.

On the reconciled feed the layer measures what the box score cannot. A death is *traded*
when a teammate kills the attacker within five seconds — the archive's own window — and
the untraded deaths are the ones that actually cost a numbers advantage. A *clutch* is
being the last player alive, scored 1vN by how many opponents remain. *Man-advantage
conversion* is whether the team that draws the round's first blood goes on to win it; its
mirror is the *steal*, winning a round opened a man down. The round-based measures are
Search and Destroy only; trades cover both feed titles, Uplink included. Round winners
come from the feed's own round scores, except the deciding round, which resets its score
— its winner is recovered by matching the box-score map result.

Two limits are stated rather than papered over. The per-kill distance field is Infinite
Warfare only and, despite the box column's metres label, is in engine units: it
correlates with that column at r = 0.97 per player-season but on a fixed ~5.75x scale, so
it is reported as engine space, not metric. And while every Hardpoint game lists its hill
names and rotation count, the events carry no per-hill timing, so kills cannot be
attributed to a specific hill and hill-by-hill analysis is not claimed. Headshots are
likewise left to the box score, whose headshot column the feed's cause-of-death matches
only about 69% of the time.

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

## Tier 4: Meta and environment analysis (partly shipped)

- **Loadout meta (shipped).** Usage share and map win rate for every loadout choice the
  archive records, by season and mode: weapons across all three titles, WWII divisions
  and basic training, Infinite Warfare rigs, payloads and traits, and Black Ops 4
  specialists. Choices under 30 player-maps are suppressed. Win rates sit near 50% for
  every widely used option, which is the expected result when both teams field the same
  meta, and worth stating plainly rather than dressing up as an edge.
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
