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

A percentile and a z-score are held to different cohort depths. A percentile is a rank
statement, and with few peers it is coarse but not wrong. A z-score divides by an
estimated standard deviation and invites the reader to treat the answer as standard
deviations from a distribution, which on a handful of qualified players it is not. So
below 15 qualified members a cohort publishes its percentiles and leaves the z-score
null. Nothing that was shown before is hidden; the claim the sample cannot support is
simply not made. This applies everywhere cohort scoring is used — the era adjustment,
all 93 metrics, and the team metrics — and the threshold is recorded in each run's
parameters.

Objective metrics are mode-specific: hill seconds per 10 minutes of map time for
Hardpoint, first bloods plus plants plus defuses per map for Search and Destroy, zone
captures for Control, flag captures plus returns for Capture the Flag, and uplink
points for Uplink.

Each z-score is stored with its own standard error, so the career arc can draw a real
interval rather than a decorative one. A season K/D is a ratio of summed kills to summed
deaths over the maps played, so its sampling error comes from the delta method on that
ratio, using the second moments of the player's own maps. The covariance term matters:
kills and deaths correlate strongly across maps — a map spent in heavy fighting raises
both — and treating them as independent would overstate the error. Dividing that error by
the same cohort SD that formed the z-score puts it in z units. Seasons of one map, or
with no deaths, get no error and therefore no band. The closed form is checked against a
direct bootstrap of the same maps in the test suite.

This adjustment drives the cross-era leaderboards and the percentile coloring
throughout the site. Player pages show raw and adjusted values side by side so the
adjustment stays visible instead of disappearing inside a number.

Splitting cohorts further by LAN versus online is a planned refinement. It needs the
2019-2022 span to be meaningful, which the current dataset does not cover.

## Tier 1b: Metric layer (shipped)

The archive measures far more than kills and deaths. The metric layer turns every
measured column into a published, era-scored metric, so a player's season can be read
across 93 different lenses instead of four.

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

## Tier 1d: Round win probability (shipped)

The event tier says what happened. This is the first model built on top of it: given the
state of a Search and Destroy round right now, what is the probability each team wins it?

**Why only Search and Destroy.** SnD is the only mode in the archive whose rounds are a
unit of play — one life each, a discrete winner, 9,282 of them. The `game_rounds` table
does carry rows for the other feed modes, but a Hardpoint "round" is the whole map and a
Capture the Flag one is a half, so there is no round-scale contest there to model. BO4 has
no feed at all. What is left reconciles cleanly: 1,023 of 1,024 SnD games resolve a round
winner, every one of them four a side. One round in 9,302 is dropped because its feed
contradicts itself — a player dying while their side is already empty — which is the same
treatment a failing player-map gets in the reconciliation view.

**The state is the survivor count, and that is nearly all of it.** Counting outcomes for
every (own alive, opponent alive) pair over both sides of every instant gives sixteen
non-terminal states from ~104,000 observations. Because each instant is recorded from both
teams' points of view, the table is antisymmetric by construction and every *n*-versus-*n*
state is exactly 0.500 — not a finding, a property of the encoding, stated so nobody reads
it as one.

| | opp 4 | opp 3 | opp 2 | opp 1 |
|---|---|---|---|---|
| **4 alive** | .500 | .716 | .912 | .988 |
| **3 alive** | .285 | .500 | .782 | .959 |
| **2 alive** | .088 | .218 | .500 | .846 |
| **1 alive** | .012 | .041 | .154 | .500 |

Standard errors run from 0.003 to 0.011 and are widened by √2, because each round enters
its cells twice and the naive binomial error would understate them.

The regularity worth naming is that round win odds track the *ratio* of survivors, not the
difference. A ridge logistic on both gives

```
logit P(win) ≈ 2.01 · [log(own) − log(opp)] + 0.42 · [own − opp]
```

with an intercept of zero, again by construction. Being up one is worth 0.716 at 4v3 and
0.846 at 2v1: the same man advantage, nearly twice the swing, because it is a larger share
of what is left.

**What the backtest compares.** Walk-forward by event — the model scoring CWL Anaheim was
fitted on everything through CWL Seattle and nothing after — over 8,479 rounds in the ten
events that have a predecessor. Losses accumulate per round rather than per state row, and
the bootstrap resamples rounds: the twenty-odd rows a round contributes are one round seen
at successive instants from both sides, and treating them as independent draws would shrink
every interval by roughly √20 and manufacture significance out of nothing.

| Model | Brier | vs. the table |
|---|---|---|
| **State table (published)** | **0.16394** | — |
| Survivors: log-ratio + difference | 0.16400 | +0.00006 [+0.00001, +0.00011] |
| Difference only | 0.16477 | +0.00083 [+0.00060, +0.00106] |
| Log-ratio only | 0.16469 | +0.00075 [+0.00063, +0.00087] |
| Coin flip at 0.5 | 0.25000 | +0.08606 [+0.08388, +0.08844] |

Counting wins. With sixteen states and ~100,000 observations there is nothing for a smooth
function to buy, and the parametric fit is beaten — narrowly, but by an interval that
excludes zero. The logistic is still reported because it is interpretable and the table is
not; it is not used for anything the table can do.

**Two nulls, both stated with an interval.** Each is an add-a-feature question, so each is
asked against the model the feature was added to, not against the published table.

- *Time elapsed in the round adds nothing.* Interacting elapsed time with both survivor
  terms moves Brier by +0.00004 [−0.00001, +0.00009], *p* = 0.13 — and the sign is the
  wrong way. This archive could have detected 0.00008.
- *Neither does the bomb.* The feed carries no plant or defuse event; `means_of_death` is
  fifteen kinds of gunfire. It is not wholly unrecoverable, since round durations pile up
  at 90 seconds and then tail off, which is the regulation clock expiring — so a round
  still alive at 90 seconds implies a plant. Adding that indicator moves Brier by
  +0.000001 [−0.000015, +0.000016], *p* = 0.92. The reason is structural rather than
  empirical: the feed never says which side planted, and an indicator that cannot be
  attributed to a team is symmetric under swapping the two teams while the target is
  antisymmetric, so it can only enter at zero. It is measured anyway rather than argued
  away, because the argument is the kind that is easy to get wrong.

Recovering which side is attacking would be the single largest improvement available here,
and the archive does not currently support it.

### The round along its own clock

The table above answers what a state is worth. It says nothing about when states arrive, so
a third artifact, `round_timeline`, describes the round on a 5-second grid: survivors, win
probability, the model against the outcome, and how much of the sample is still playing.
It is description rather than a second model — the probabilities are the same table read
*in sample*, on the rounds that fitted it — which is why a model-free series is published
next to them and why the out-of-sample claim stays with the walk-forward above.

Rounds are not decided early so much as leaned on early. Half of them are over by 60
seconds. At thirty seconds the eventual winner has 3.29 players up against 2.65 — a gap of
under two thirds of a player — and the table already reads that as a 64.5% favourite,
climbing to about 0.70 by the seventy-second mark and then *falling back*, because the
rounds still alive that late are the ones nobody is winning.

**A body is worth more late than early, and the table prices both the same.** The third
series is a calibration check over time, on one subset: rounds where the two sides have
different survivor counts. What the table says the side ahead is worth, against how often
that side went on to win, at two single instants — single, because a round appears in
twenty bins and pooling them would treat one round as twenty observations.

| Instant | Rounds | Table says | Actually won | Gap |
|---|---|---|---|---|
| 15 s | 3,409 | 0.773 | 0.785 | +1.2 pt ± 0.7 |
| 60 s | 2,636 | 0.857 | 0.884 | +2.7 pt ± 0.6 |

The late gap clears its error and the early one does not. This does not overturn the "time
adds nothing" null above: that null is about *prediction*, and a small one-directional
calibration drift is entirely compatible with no measurable Brier gain — 0.00008 was the
smallest Brier difference this archive could resolve. Both statements are true, and the
combination is the ordinary situation of an effect that is real and small.

**What the trade window costs.** A death counts as traded when the killer is answered by
the victim's side within 5 seconds, a convention inherited from the archive's own
`kills_stayed_alive` column. Measuring the same latency without the cutoff: 44% of deaths
are ever answered by that side, the median answer takes 7.2 seconds, and only 42% of the
answers that arrive land inside the window. The distribution peaks in its first two
seconds, so the convention is not arbitrary — but it is a cut across a smooth decay rather
than a seam in the data, and it leaves more revenge kills outside than it counts. Trades
are also front-loaded: a quarter of deaths in the opening five seconds are answered in
time, against 16% of deaths at a minute in, when there are fewer teammates left to answer.

Three exclusions, stated rather than absorbed. The figure's axis stops at 105 seconds,
where 1.5% of rounds remain. 166 rounds of 9,282 record a length that ends before their own
last death — by 38 seconds at the median, so not rounding — and are dropped from the three
state panels, which need to know when a round stopped; they are kept for the trade
latency, which does not. And the calibration panel starts at 5 seconds: exactly two rounds
in the archive open uneven, and a rate over two rounds is not a rate.

### Win probability added, and why it is not a rating

Each kill moves the round from one state to the next, and the killer is credited with the
change in their own side's win probability. The credits telescope: summed in one team's
frame across a round they land exactly on that team's final win probability minus its
opening 0.500, so WPA is an accounting of the round rather than a score attached to it.

As a description of what happened, it works. As a measurement of a *player*, it does not,
and both numbers are published so the leaderboard is not read as a rating.

Per player per round, WPA correlates **0.912** with kills per round — most of it is a kill
count in a different unit. The question is whether the remaining third carries anything, so
each player's games are split in two (by game, never by rounds within a map, which would
let shared opponent and map context masquerade as a stable trait) and each half is asked to
predict the other, over the 101 players with at least 75 rounds on both sides.

| Rate | Split-half *r* | Spearman-Brown |
|---|---|---|
| Kills per round | 0.43 [0.21, 0.62] | 0.60 |
| WPA per round | 0.30 [0.06, 0.51] | 0.46 |
| **WPA with kill rate removed** | **−0.19** [−0.39, +0.03] | — |

Kill rate repeats. WPA repeats, slightly less well. The part of WPA that kill rate does not
explain does not repeat at all: the point estimate is negative and the interval spans zero.
101 players could have detected a correlation of 0.28, so a large effect is ruled out and a
small one is not — but nothing here supports the claim that *which* kills a player gets is
a repeatable skill this archive can measure.

So round WPA is published as a per-round description and is deliberately **not** promoted
into the player rating. That was the hope this model was built on — player value measured
in outcomes rather than box-score totals — and the reliability test says it is not there.
Reporting it is the point of running the test.

Artifacts `round_win_prob` and `round_wpa` are stored with the `round_wp` run and
recomputed on every rerun.

## Tier 2: Rating systems

**Team strength over time (shipped).** Elo (K=32) and Glicko-2 (τ=0.5) are fit over the
full history at series level. The rating chart on the overview and team pages toggles
between the two, and the Glicko-2 view shades each team's rating deviation, which is the
thing Elo cannot express: a team's first few series carry a deviation near the 350
starting value, and it narrows as the record accumulates. A map-margin-weighted variant
is planned as a sensitivity check.

**Rating periods.** Glicko-2's deviation is only meaningful if it grows while a team is
idle, and that requires periods. One event is one period: a CWL event is a few days of
dense play followed by weeks of nothing, which is the shape the method assumes — the
paper wants ten to fifteen games per period, not one. At the close of each period every
rated team is advanced, those that played by the paper's update and those that sat out by
volatility inflation alone, so a roster returning from a layoff is correctly less certain
than when it left. The deviation is capped at the initial 350, because that value already
means "nothing is known" and a team idle for three years is not less known than one that
has never played.

This is stricter walk-forward than rating each series in turn, not looser. Every series
in a period is predicted from the ratings as they stood when the period opened, so the
model cannot use the first day of an event to sharpen its guess about the third.

An earlier version made each series its own period and advanced only the two teams in it,
which meant the inflation never ran and the deviation tracked games played rather than
time elapsed. That was a bug, and the numbers on this page postdate its fix.

**Hyperparameter sensitivity (shipped).** Elo's K, Glicko-2's τ, and the period length
were asserted constants in a project whose rule is that a model ships with its backtest,
so all three are now swept over the same walk-forward evaluation and the grid is stored
as an artifact of the Glicko-2 run.

The sweep does not choose the published settings, and that is deliberate. Picking the
grid's argmin on the same 1,310 series the score is reported over would be selection on
the test set: the published Brier would then be the best of twenty draws rather than an
estimate of anything. The constants stay declared, and the grid is published as
sensitivity analysis — its job is to show how much the choice matters, not to make it.

Ratings are org-lineage-aware: rating state is keyed on the organisation, not the brand,
so a rebrand continues one curve instead of restarting at 1500. The stored rows still
name the team that actually played, so the site shows the brand of the day on a
continuous line, and a lineage is rated under its founding team.

Two honest notes on how much that currently does. Lineage membership is declared in the
importer's identity file, and it is asserted only where two brands' series windows do
*not* overlap — a same-brand roster playing concurrently is an academy team, not a
rebrand, so `Mindfreak` / `Mindfreak Black`, `EZG` / `EZG Blue` and the three `GGEA`
teams stay on separate curves. Applying that test to this archive leaves exactly one
lineage, `eRa` → `eRa Eternity`, covering 23 of 1,310 series: the era's well-known
rebrands (Splyce to Evil Geniuses, the OpTic and franchise moves) all happen after
2019-08-18, where the data stops. So the machinery is real and tested but currently
near-inert; it matters for the CDL era, not for this one. `Morituri eSports` /
`Regal Morituri` is deliberately left unmerged: the older brand reappears *after* the
newer one, which is not the shape of a rebrand.

**Series win probability, `winprob_v1` (shipped).** Rather than a third rating system,
this model asks a sharper question: given the ratings, does anything else carry
information about who wins a series? Its features, all computed strictly before each series, are the
walk-forward Glicko-2 and Elo win probabilities (as logits), the combined Glicko-2
rating deviation, each team's win rate over its last ten series, and a shrunken
head-to-head record. The model is L2-regularized logistic regression, refit on an
expanding window every 50 series; until 200 series of history exist it passes the
Glicko-2 probability through unchanged, so its backtest covers the same series as the
baselines and any improvement is attributable to the added features.

That last clause is load-bearing, and for a while it was false. The rating systems moved
to whole-roster rating periods while this model went on advancing its own copy of
Glicko-2 one series at a time, so its "pass the Glicko-2 probability through unchanged"
phase passed through a Glicko-2 that appeared nowhere else on the site, and the backtest
table below credited the added features with what was really a difference between two
fits. The settings that define a fit — rating period, lineage map, K, τ — are now passed
in from the same values the published Elo and Glicko-2 runs use, and a test pins the
identity phase against the published Glicko-2 prediction by prediction, at every period
length, so the two cannot drift apart again in silence.

The answer, over 2017-2019 and now measured against the right baseline, is no. Against
the Glicko-2 it is built on, `winprob_v1` moves Brier from 0.22874 to 0.22733 — 0.0014,
about six parts in a thousand — and moves accuracy the *other* way, from 63.4% down to
61.8%. Recent form and head-to-head history do not improve series prediction *by an
amount this archive can measure* — the interval and the power statement below say how
much that qualifier is worth. Both numbers are published rather than the flattering one,
because a null that survives only
on the metric that suits it is not a null.

The learned coefficients ship with the run. At the final refit `form_diff` sits at −0.16
on a feature spanning roughly −1 to +1: small, and signed so that recent winners do
slightly *worse*, which is not a momentum effect and is not a claim this site makes — it
is what a weak feature looks like fitted beside strong and partly collinear ones. Note
also that the ridge splits the two rating logits unevenly (0.85 on Elo against 0.22 on
Glicko-2, which are near-restatements of each other); read them together, as with the
slaying pair in the player rating.

The gap now carries an interval and a power statement, which together change what this
null is allowed to claim. Every model predicts the same 1,310 series, so the comparison
is paired: the per-series difference in squared error is one observation, its mean is
the gap, and a 2,000-draw bootstrap over series gives the interval. Against Glicko-2,
`winprob_v1` improves Brier by 0.0014, 95% CI −0.0029 to +0.0059 — Diebold-Mariano
t = 0.65, p = 0.52. The gap is not distinguishable from zero, which is the result.

The more useful half is what this archive could have found. Suppose momentum is real
and the true probability is Glicko-2's logit plus β × `form_diff`. Then the expected
Brier gain from knowing β and its variance are both available in closed form, so the
smallest detectable β follows directly: at 1,310 series, 80% power and a two-sided 5%
level, **β would have to be 1.65 or larger** — a team arriving on a 10-0 run against
one on 0-10 would have to be 34 percentage points more likely to win than the ratings
alone say. The fit found −0.16.

State the null at that strength and no further. A momentum effect that large is ruled
out. A plausible one — a few points of win probability — is not, and 1,310 series
cannot settle it. "Recent form adds nothing measurable here" is true; "recent form does
not exist" is not something this archive can say, and the site does not say it.

**Validation (shipped).** Models are evaluated by walk-forward backtest, which is to
say each prediction is made using only data available before that series. Current
results, over the full 2017-2019 record of 1,310 decided series:

| Model | Brier | Log loss | Accuracy |
|---|---|---|---|
| map_elo, blend ([below](#map-elo)) | 0.21821 | 0.6249 | 64.7% |
| map_elo, global | 0.22098 | 0.6333 | 64.7% |
| map_elo, mode | 0.22180 | 0.6335 | 64.3% |
| Elo | 0.22281 | 0.6354 | 63.4% |
| winprob_v1 | 0.22733 | 0.6476 | 61.8% |
| Glicko-2 | 0.22874 | 0.6518 | 63.4% |

All six are fitted the same way: same lineage map, same K and τ, and — where the model
has periods at all — the same event-length rating periods. That was not true until
recently, and the row that changed is `winprob_v1`, which had been carrying a per-series
Glicko-2 of its own. The three `map_elo` rows are map-level ratings rolled up to the
series, which is what makes them belong in this table at all; how that rollup avoids
reading the series it predicts is [below](#map-elo).

The spread across the table is about 0.011 of Brier and 2.9 points of accuracy, on 1,310
series — and because every model predicts the same series, those gaps are paired data
with intervals rather than a leaderboard to be read off:

| Contrast | Brier gap | 95% CI | DM p | Detectable at 80% power |
|---|---|---|---|---|
| map_elo blend − Elo | −0.00460 | −0.00704 to −0.00212 | 0.0002 | 0.00349 |
| map_elo blend − winprob_v1 | −0.00911 | −0.01275 to −0.00562 | <0.0001 | 0.00517 |
| map_elo blend − Glicko-2 | −0.01052 | −0.01596 to −0.00551 | 0.0001 | 0.00760 |
| map_elo global − map_elo blend | +0.00277 | +0.00056 to +0.00524 | 0.021 | 0.00336 |
| map_elo mode − map_elo blend | +0.00359 | +0.00087 to +0.00610 | 0.007 | 0.00369 |
| Elo − Glicko-2 | −0.00593 | −0.01115 to −0.00102 | 0.025 | 0.00741 |
| Elo − winprob_v1 | −0.00452 | −0.00780 to −0.00139 | 0.007 | 0.00469 |
| Glicko-2 − winprob_v1 | +0.00141 | −0.00294 to +0.00588 | 0.517 | 0.00609 |

A negative gap favours the first model. Among the three series-level models, two of the
three contrasts separate: **Elo genuinely outscores both Glicko-2 and `winprob_v1` on
Brier over this archive**, and that is the one ordering among them the data supports. The
simplest model here beats both of the ones built to improve on it, which is worth stating
plainly rather than explaining away.

The last contrast is the momentum null and it does not separate, with an interval four
times the width of the gap. Note also that Elo's Brier edge over Glicko-2 sits *inside*
its own detectability threshold (0.00593 against 0.00741): the bootstrap interval
excludes zero while the 80%-power criterion says a gap this size would usually be
missed, which is what a real but marginal difference looks like. It should be read as
a lead worth one more archive to confirm, not a settled ranking. Accuracy separates
nothing among those three — every one of their accuracy intervals spans zero.

The `map_elo` rows are the exception, and they are the strongest positive result on this
page. Its blend arm beats Elo by 0.00460 with an interval clear of zero *and* clear of
its own 80%-power threshold (0.00349) — the only model gap in this project that passes
both tests. It beats Glicko-2 and `winprob_v1` by more. The two contrasts *within*
`map_elo` do not clear power (+0.00277 against 0.00336, +0.00359 against 0.00369), so the
honest reading is that rating maps beats rating series, while the choice among the three
map-level arms is not settled here. What the extra data buys is set out next.

The whole table is computed by `ratings/significance.py` and stored as a `model_gaps`
artifact with the winprob run, so it is remeasured on every rerun.

One caution about reading Glicko-2's row as a verdict on rating periods: the
hyperparameter sweep finds series-length periods scoring better (Brier 0.22462 at τ=0.2),
and they were *not* adopted for it. The period length is argued from the shape of the
calendar — a CWL event is a few days of dense play then weeks of nothing, which is what
Glicko-2's periods assume — and the sweep is published as sensitivity, never as the
selection rule. Picking hyperparameters on the backtest that then validates them is how a
backtest stops meaning anything.

Brier score, log loss, accuracy, and calibration curves are published for every model
version. The Brier and accuracy *differences* between them carry intervals, as above;
log loss does not, and no statement here rests on a log-loss gap.

Model outputs are versioned against the run that produced them, recording code version,
hyperparameters, and training window. A rerun replaces a whole run rather than editing
rows in place, so any published number can be traced back to the exact code and data
window that generated it.

### Map Elo

The team ratings above rate 1,310 series while the 5,087 decided maps underneath them go
unrated. That is the smaller half of what this section is about. The larger half is that
a series result is a blend of three or four different games — a Hardpoint, a Search and
Destroy, a Control or Capture the Flag — and Call of Duty rosters are not equally good at
all of them. A single number per team cannot say "top three in Hardpoint, mid-table in
Search", and as far as we can tell nothing published anywhere says it.

So `map_elo` fits three arms, all Elo, all on the same 5,087 maps, all strictly
walk-forward:

- **global** — one rating per team, updated once per map. The control: it answers "is the
  extra sample worth anything" without changing the model.
- **mode** — one rating per (team, mode). Full mode specificity, and a fifth of the sample
  behind each number.
- **blend** — the two mixed per team by how much mode history it has, `w = m / (m + 40)`
  maps in that mode. It nests both — `w = 0` is global, `w = 1` is mode — so it needs no
  choice between them, which is why it is the arm whose rollup goes in the table above.

All three share one K (16, half the series-level 32 on the ground that a map carries less
information than a series, and declared rather than tuned). Sharing it is deliberate: the
experiment is about how finely rating state is cut, and giving the mode arm its own K
would confound the two. The sweep re-scores every arm across K anyway, and the answer
below holds at all eight values, which is the point of publishing it.

**Staying comparable.** A map Brier cannot be read against a series Brier, so each arm
also rolls up. At the moment a series starts the ratings are frozen, a win probability is
computed for each of the five maps the title's rotation would play, and those are combined
into P(win the series) as a best-of-five. The rotation is a league rule, known before a
ball is thrown — Hardpoint, Search, then Uplink (IW) or Capture the Flag (WWII) or Control
(BO4), then Hardpoint and Search again — and the archive confirms it: 737 of 740 WWII
opening maps are Hardpoint. The number of maps *actually* played is not known in advance
and is never read, because it is the result: a series that went five was 3-2 by
definition. All 1,310 series roll up, none is missing a rotation, and a test asserts the
rollup is bit-identical whether the series went 3-0 or 3-2.

**The result on maps.** Scored on the maps themselves, against the 0.25000 a coin flip
gets:

| Arm | Brier | Log loss | Accuracy |
|---|---|---|---|
| blend | 0.23579 | 0.66395 | 60.4% |
| global | 0.23645 | 0.66556 | 59.9% |
| mode | 0.24033 | 0.67338 | 57.7% |

**A mode-specific rating does not beat a global one at predicting map winners. It loses.**
Global − mode is −0.00388, 95% CI −0.00613 to −0.00168, DM p = 0.001, against a
detectability threshold of 0.00318 — it clears both tests. Global beats mode at every K in
the sweep, from 4 to 48, so this is about the granularity of the state and not about a
constant chosen for one arm. The blend is indistinguishable from global (+0.00066, CI
−0.00056 to +0.00189, p = 0.30) and beats mode outright.

That is the answer to the question this was built to ask, and it goes the way the smaller
sample predicts: cutting the archive five ways costs more in precision than mode identity
returns in signal.

**Where it goes wrong, per mode.** The overall number hides a real pattern, so the same
contrast is computed within each mode:

| Mode | Maps | global | mode | blend | global − mode | 95% CI |
|---|---|---|---|---|---|---|
| Hardpoint | 2,130 | 0.23027 | 0.23308 | 0.23018 | −0.00281 | −0.00535 to −0.00015 |
| Search and Destroy | 1,656 | 0.24998 | 0.24885 | 0.24642 | +0.00113 | −0.00321 to +0.00576 |
| Capture the Flag | 737 | 0.22710 | 0.23832 | 0.22849 | −0.01122 | −0.01770 to −0.00472 |
| Control | 485 | 0.23223 | 0.24512 | 0.23563 | −0.01289 | −0.02087 to −0.00424 |
| Uplink | 79 | 0.23269 | 0.24669 | 0.23339 | −0.01400 | −0.02289 to −0.00541 |

Search and Destroy is the only mode whose gap does not run against the mode arm — and its
interval spans zero (p = 0.62, detectable at 0.00641). The correct statement is that
Search is the one mode where mode-specific state is *not shown to hurt*, not one where it
helps. It is also the mode where the blend does best, beating global by 0.00356 (CI
+0.00092 to +0.00628), which is suggestive and sits just inside its own power threshold of
0.00373. Search is the mode with the least scoreboard signal and the most distinct skill,
so a residual there is the result worth chasing with a larger archive. The thin modes go
the other way hard: Control and Uplink lose more than a hundredth of Brier to mode-specific
state, which is what a rating with a few hundred maps behind it looks like.

**Is mode specialization real at all?** A spread of per-mode ratings proves nothing on its
own — fit five noisy numbers per team instead of one and they will differ. So the spread
is tested against a permutation null. Mode labels are shuffled *within each event*, which
keeps every team, opponent, result, date and the event's own mode mix and destroys only
the association between a team and which mode it was playing. The statistic is the SD
across qualified (team, mode) cells of that cell's rating minus the team's own global
rating.

Over 98 cells with at least 25 maps each, the observed spread is **54.8 rating points**
against a permuted null of 51.4 (95% range 47.4 to 55.5) over 300 refits: **p = 0.0598,
inside the null.** About 3.4 points of spread survive what noise alone supplies, out of
54.8.

That is the honest verdict, and it is a null: **this archive cannot show that Call of Duty
teams have real per-mode strengths, distinct from being good or bad in general.** The
per-mode table is still stored and shown, because the ordering is the thing readers ask
for and hiding it would not make it less tempting elsewhere — but it is published with
this number attached, and the largest gaps in it (eUnited −173 in Control, 100 Thieves
−162 in Search) are within the range shuffled labels produce. Note also what the null does
*not* rule out: an effect too small for 5,087 maps to separate from noise. "Mode
specialization is not measurable here" is what this says; "mode specialization does not
exist" is not.

**Reading `mode_ratings.delta` off the artifact.** The stored `delta` is a cell's rating
minus the team's global rating, and it is not centred: across the 98 qualified cells it
averages **−34** and is negative in 72 of them. That is a property of the estimator, not
of the league. A mode rating is fit on a fraction of the maps the global rating sees, so
it regresses further toward the initial value, and the size of the pull depends on how
much of the rotation the mode is — control −57 on average, hardpoint −24. Printed raw,
`delta` says almost every team is worse at every mode than they are overall, which cannot
be true of a set of modes that make up the whole. The figures quoted above (eUnited −173
in Control, 100 Thieves −162 in Search) carry that offset and are quoted only to show the
range the null covers.

Team pages therefore subtract the field's mean gap in each mode before drawing anything,
which leaves a gap against the field rather than against the estimator. The chart shades
the null band behind the bars and mutes every bar that falls inside it, and the verdict
above travels with it in the same component, so no page can render the ordering without
the number that says how much of it is real.

**What the extra sample does buy.** The one thing that clearly works is rating maps at
all. Rolled up to series, the blend arm scores 0.21821 against Elo's 0.22281 — a gap of
0.00460 that clears both its interval and its power threshold, the only model comparison
in this project that does. Accuracy goes from 63.4% to 64.7%. Nothing about the model
changed; it saw 3.9× as many results.

Sensitivity is stored as a `map_sweep` artifact and, as everywhere else on this page, does
not choose anything: K is declared at 16 (the grid's best for the global arm is also 16,
for the blend 24, a difference of 0.0003 of Brier) and the blend constant at 40 (the grid
mildly prefers 80, by 0.0003). All of it — `map_backtest`, `series_rollup`,
`mode_specialization`, `mode_ratings`, `map_sweep` — is computed by
`ratings/maplevel.py` and rewritten on every pipeline run.

**Open player rating (shipped).** The composite rating, built in four steps, each of
them auditable:

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
   their shared weight — read them jointly as slaying. Every coefficient also ships
   with a bootstrap interval, because a few hundred maps of collinear features do not
   pin one down as tightly as a single number implies; see
   [how much of the weights is signal](#how-much-of-the-weights-is-signal) below.
2. *Score players with those weights.* Each player-season-mode aggregate is z-scored
   against its qualified cohort (≥ 8 maps, as in the era adjustment) and dotted with
   the mode's weights. That score is the observation; it is not yet a rating.
3. *Estimate what the score means.* Within each cohort, a two-level normal-normal
   model says a player's maps are noisy reads of a true skill and true skills are
   spread across the league. Fitting it gives the posterior for each player —
   partial pooling, so a hot 12-map season cannot outrank a great 200-map one — and
   gives the interval in the same closed form rather than from a bootstrap bolted on
   afterwards. See [the rating is a posterior](#the-rating-is-a-posterior) below,
   and [how many maps a season needs](#how-many-maps-a-season-needs) for the
   pooling strength it implies.
4. *Normalize.* The season rating blends mode posteriors weighted by maps played,
   centred so the qualified cohort averages 1.00 and scaled so one rating point is
   0.15 of the cohort's estimated true-skill spread. The published `rating_sd` is
   the posterior SD, on every row including the per-mode ones.

Its validation is walk-forward within each (season × mode): every event's maps are
predicted using weights trained only on earlier events. That number establishes one
narrow thing — that the learned weights generalize across events rather than memorizing
them — and it is not evidence that the model can forecast anything. Several of the
features *are* the win condition, so the map accuracy is largely a decomposition of the
final score. The size of that effect is measured, not waved at, in
[what the map backtest does not establish](#what-the-map-backtest-does-not-establish)
below; read the two together or not at all.

### The rating is a posterior

This page named hierarchical and Bayesian statistics as the appropriate tools for a
dataset this size, and then, for the player rating, shipped a z-score multiplied by a
shrinkage factor. Both pieces had the right instinct and neither was a model. They also
worked against each other: the z-score divided by the *observed* spread of season
scores, which is inflated by exactly the per-map noise the shrinkage step exists to
discount, so the scale and the pooling were each estimated as though the other were not
happening — and the published interval had to come from a bootstrap bolted on at the
end, because neither step could produce one.

One two-level normal-normal model states all of it at once. Inside a cohort
(season × mode), a player's per-map score is a noisy read of a true skill, and true
skills are spread across the league:

    y_ij = θ_i + ε_ij,   ε_ij ~ N(0, σ²)     what a single map measures
    θ_i  ~ N(μ, τ²)                          how good players actually are

A season is summarized by x_i, the score of the season profile, whose sampling variance
is v_i = σ²/m_i over m_i maps. The posterior for θ_i is then closed form — no sampler,
no probabilistic-programming dependency, about thirty lines of numpy:

    B_i = τ² / (τ² + v_i)        how much of this season is signal
    θ̂_i = μ + B_i (x_i − μ)      posterior mean
    V_i = B_i v_i                posterior variance

Three things follow that the old pipeline could not state.

**The shrinkage was this model all along.** B_i is exactly m_i / (m_i + k) with
k = σ²/τ², so the estimated-k work described below is not discarded by this change; it
is recovered as a consequence of it. What changes is that k no longer has to be
estimated by a separate moment decomposition — the same fit that produces the ratings
produces it.

**The scale is τ, not the observed spread.** A rating point is now 0.15 of the estimated
*true* spread between players in that cohort. Under the old estimator it was 0.15 of the
observed spread, which is √(τ² + mean v) wide — so "one rating SD" quietly meant
something different in every cohort, depending on how many maps its players happened to
play. The ratio of the two is worth publishing on its own: **τ over the observed spread
is how much of the leaderboard's range is real difference between players rather than
noise.** It runs from 0.36 in 2017 IW Search & Destroy — where a 721-map cohort of
five-map seasons leaves most of the visible spread unexplained by skill — to 0.90 in
2018 WWII Hardpoint.

**The interval is the posterior's, and it is not the bootstrap.** Resampling a shrunk
point estimate measures how far that estimate would move on other maps, B√v. The
posterior SD measures what is still unknown about the player after pooling, √(Bv), which
is larger by 1/√B: a median of **2.00×** across this archive, with a quartile range of
1.54 to 2.93 and worse on short seasons. The old ±rating_sd was answering a question
nobody asked of it, and every band drawn from it was too tight. Per-mode rows now carry
an interval too; the bootstrap only ever existed for the all-mode blend.

**One assumption, measured rather than asserted.** v_i = σ²/m_i treats the season
profile as a mean of m maps, but it is a ratio of summed numerators to summed
denominators — close, not identical. Rather than caveat that, each cohort measures it:
every player-season's score is resampled from its own maps, and the ratio of that
variance to σ²/m is averaged over the cohort. The answer is **0.927** overall (0.876 to
0.966 by cohort) — the profile is about 7% steadier than the plain form assumes, and σ²
is scaled by the measured factor before the fit rather than after. It matters more than
it looks: v enters τ² = Var(x) − mean(v) with a minus sign, so an overstated observation
variance does not merely widen intervals, it eats the between-player variance and
reports a cohort as flatter than it is. Left uncalibrated, 2017 IW Search & Destroy fits
at τ² = 0 — the model concluding that no two players in it can be told apart.

**What moved.** The published ratings shift by 0.022 on average and 0.070 at most, on a
scale whose league SD is 0.15; the rank correlation between the two estimators is 0.987
and nine of the top ten qualified seasons are the same players. This is a re-estimation,
not a re-ranking.

**Does it forecast better?** Being better specified is an argument, not evidence, so the
new estimator and the old one are both run through the roster forecast in
[two tests the rating can fail](#two-tests-the-rating-can-fail): identical maps,
identical weights, identical prefixes, differing only in the step being tested. The
posterior wins by −0.00112 of Brier [−0.00220, −0.00010]. The interval excludes zero,
and the gap is smaller than the 0.00148 those 3,760 maps could resolve at 80% power, so
the honest reading is a small improvement that clears its interval but not its power
threshold — and on pick rate the two are indistinguishable (56.4% against 56.7%). The
case for the change rests on the specification and the intervals; the forecast says it
costs nothing, which is what it had to say.

(μ, τ²) are fitted by EM — closed form per step, monotone in the marginal likelihood,
and unable to take τ² negative, which was the old moment estimator's one failure mode.
σ² comes from within-player replication: how much a player's own maps disagree with each
other. Every player in the cohort is fitted, not only the qualified ones, because the
pooling is applied to short seasons and estimating it from long ones alone would
describe a different population. Where 1.00 sits is a presentation choice and is
unchanged: the origin is the qualified cohort's mean posterior. All of it ships as a
`rating_posterior` artifact with every rating run.

### Where the interval is drawn

Storing an interval and printing a point estimate is the same mistake as not having one,
so the posterior travels with the rating everywhere the rating appears. Every band on the
site is ±1.96 SD of the quantity named next to it, on a domain shared by every row of the
table or plot it sits in — a band that is only comparable to itself hides the one thing
it is for.

| Where | Number | What the interval is |
|---|---|---|
| Player page → Rating | composite rating by season | posterior SD, `player_season_adjusted.rating_sd` |
| Player page → Career arc | season K/D vs cohort | era model's SE, `kd_z_se` |
| Player page → Seasons | season K/D vs cohort | era model's SE |
| Players → rating board | composite rating | posterior SD, drawn and printed |
| Players → index | best qualified season | posterior SD, printed |
| Home → season leaderboard | season K/D vs cohort | era model's SE |
| Teams → standings, trajectory | Glicko-2 | rating deviation (RD) |

A season with no stored SD is drawn as a point with no band rather than a zero-width one,
which would read as certainty. Two intervals are called separated only when they do not
touch, which is the conservative direction: on the rating board's top twenty, eight of the
nineteen chasing seasons reach the leader's interval, so most of that ordering is an
ordering of estimates rather than a claim that the seasons differ. Elo carries no interval
because the estimator does not produce one; that is why the team pages publish Glicko-2
alongside it.

### How many maps a season needs

Before the model above existed, the shrinkage was described here as empirical-Bayes
partial pooling while k was fixed at 15 for every cohort — the right functional form
with an invented constant, which is not the same claim. k is now read straight off the
fit as σ²/τ², the ratio of within-player noise to the real spread between players, and
the number it lands on is a fact about the mode.

| Cohort | Players | k (maps to keep half the signal) | vs the old 15 | Signal share |
|---|---|---|---|---|
| 2017 IW Hardpoint | 128 | 9.6 | −5.4 | 0.66 |
| 2017 IW Search & Destroy | 128 | 37.3 | +22.3 | 0.36 |
| 2017 IW Uplink | 128 | 10.4 | −4.6 | 0.57 |
| 2018 WWII Hardpoint | 166 | 13.1 | −1.9 | 0.90 |
| 2018 WWII Search & Destroy | 166 | 35.6 | +20.6 | 0.75 |
| 2018 WWII Capture the Flag | 166 | 21.3 | +6.3 | 0.79 |
| 2019 BO4 Hardpoint | 206 | 12.0 | −3.0 | 0.88 |
| 2019 BO4 Search & Destroy | 206 | 21.5 | +6.5 | 0.76 |
| 2019 BO4 Control | 205 | 10.8 | −4.2 | 0.83 |

The old constant was close for the respawn modes — Hardpoint lands between 9.6 and 13.1
in all three titles, Control and Uplink between 10 and 11 — and far too weak everywhere
else. Search & Destroy wants 21 to 37 maps in every title it appears in: a round-scale
scoreline with four players a side is noisy enough that a season needs roughly two to
three times as many maps before it says as much about a player as a Hardpoint season of
the same length. Capture the Flag sits between the two. That ordering is not something a
fixed constant could express, and it is the substantive result here.

The moment estimator that first produced these numbers is still fitted and still shipped
as the `rating_shrinkage` artifact, next to the model's k in `rating_posterior`. The two
agree closely where the cohort is well sampled — Hardpoint within 0.5 maps in every
title — and diverge exactly where they should, on 2017 IW Search & Destroy, where 5.6
maps per player is thin enough that how you weight players changes the answer (37.3
against 52.9). Keeping both visible is cheaper than arguing about which is right.

### How much of the weights is signal

The headline the learned weights support is a ratio: how much a one-SD team edge in
everything a cohort measured *beyond* kills and deaths was worth against the same edge
in kills and deaths. That ratio is what the findings feed states per (season × mode) and
what the chart on this page draws. Until recently it was published as a point with no
interval, on cohorts running from 79 to 1,179 maps, over features the section above
already admits are collinear. Two different things were being reported identically: a
ratio measured on 931 Search & Destroy maps and one measured on 79 Uplink maps.

The interval is a percentile bootstrap: resample a cohort's maps with replacement, refit
the standardization and the ridge end to end, recompute the ratio, and take the 2.5th and
97.5th percentiles of 200 draws. Refitting rather than conditioning on the original
standardization matters, because the standardization is estimated from the same maps.
The ratio is recomputed per draw rather than propagated from the per-coefficient
intervals, since its numerator and denominator move together.

| Cohort | Maps | Beyond-the-gunfight ratio | 95% interval |
|---|---|---|---|
| 2017 IW Hardpoint | 126 | 0.74× | 0.46 – 1.04 |
| 2017 IW Search & Destroy | 92 | 0.35× | 0.27 – 0.49 |
| 2017 IW Uplink | 79 | 2.61× | 1.29 – 6.05 |
| 2018 WWII Hardpoint | 1,179 | 1.96× | 1.53 – 2.82 |
| 2018 WWII Search & Destroy | 931 | 0.53× | 0.45 – 0.63 |
| 2018 WWII Capture the Flag | 737 | 2.54× | 1.85 – 3.84 |
| 2019 BO4 Hardpoint | 805 | 1.31× | 1.05 – 1.77 |
| 2019 BO4 Search & Destroy | 620 | 0.69× | 0.60 – 0.83 |
| 2019 BO4 Control | 485 | 0.11× | 0.03 – 0.24 |

The intervals are wide, and unequal by a wide margin: Search & Destroy's span roughly
±15% of the point estimate in every title, while Uplink's runs from 1.29× to 6.05× — a
factor of five, on 79 maps. Reporting those two side by side as "0.35×" and "2.61×" was
the problem.

Eight of the nine cohorts still resolve, in the only sense that matters here: their
interval excludes 1.0, so the sign of the claim survives. One does not. 2017 IW Hardpoint
sits at 0.74× with an interval of 0.46 to 1.04, and 126 maps cannot say which half
carried that mode. Its finding is now suppressed rather than published with a hedge, and
the chart fades the bar instead of dropping it, because "we cannot tell" is the reading
for that cohort.

Directions survive the added rigor. Search & Destroy is gunfight-decided in all three
titles and BO4 Control overwhelmingly so; WWII Hardpoint and Capture the Flag are
decided by what happens away from the gunfight, both by a factor near two or better. The
intervals ship in the `mode_weights` artifact with every rating run, per coefficient as
well as per ratio, so this table is remeasured on each rerun rather than transcribed.

### What the rating measures: three feature sets, compared

Steps 2 to 4 above never change. What changed across versions is step 1's answer to
"which numbers describe a team's map", and all three answers are kept runnable so the
choice can be checked rather than asserted.

- **1.0.0** — kills, deaths, assists and one objective column per mode, all per ten
  minutes. The box score, essentially.
- **2.0.0** — per-mode feature sets drawn from the metric layer, with per-mode
  denominators: Search & Destroy is measured per *round*, not per minute, because a
  round is what the mode actually spends. First bloods, first deaths, survival, time
  per life, hill captures and flag carry time enter here.
- **2.1.0** — adds the kill-feed tier to the modes where a trade means something:
  untraded-death rate and trade kills in Hardpoint and Search & Destroy, plus deaths
  that surrendered a man advantage in Search & Destroy. **This is the published
  version.**

No version declares which titles it applies to. Every feature names the source columns
it reads, and a cohort keeps a feature only if its title actually populated those
columns — measured from the data on every run. That is why the feature sets below
differ per season without a hand-maintained matrix anywhere:

| Cohort | Features used |
|---|---|
| 2017 IW Hardpoint | kills, deaths, hill time, hill captures, untraded-death rate, trade kills |
| 2018 WWII Hardpoint | kills, deaths, hill time, **time per life**, untraded-death rate, trade kills |
| 2019 BO4 Hardpoint | kills, deaths, hill time, hill captures |
| 2017 IW Search & Destroy | kills, deaths, first bloods, bomb plays, untraded-death rate, trade kills, thrown deaths |
| 2018 WWII Search & Destroy | the above plus **first deaths and survival** |
| 2019 BO4 Search & Destroy | kills, deaths, first bloods, first deaths, survival, bomb plays |

WWII Hardpoint has no hill-capture column and Infinite Warfare tracked no first deaths,
so those cohorts simply do not use them. Black Ops 4 has no kill feed at all, so its
2.1.0 cohorts fall back to exactly the 2.0.0 set rather than being fed zeros — an
absent column means "not recorded", never "none happened".

**One family is deliberately excluded.** The kill-feed tier can also measure rounds won
while up a man, and clutch wins. Neither is used as a rating feature, because both
contain the round outcome, and round wins are what decide maps — regressing map wins on
them would be close to circular and would flatter the backtest without the model having
learned anything. Thrown deaths qualify because they are counted from alive-counts
alone; the code computes them with an empty round-winner map so that outcome
information cannot reach the feature even by accident.

That exclusion is right, and it is also incomplete, which is worth stating plainly
rather than leaving for a reader to notice. The same test applied to the objective
columns condemns them faster: Capture the Flag is won by scoring the most captures, and
`ctf_caps` is captures. Hardpoint awards a point per second of hill control, and
`hill_time` is that occupancy. A Search and Destroy round ends on a plant or a defuse.
These are not proxies for winning that happen to correlate; on the winning side of the
identity they are the scoreboard itself.

They stay in the model anyway, for a reason that does not apply to clutch wins. What
step 1 is *for* is decomposing the scoreboard: "a one-SD edge in hill time was worth
this much, against the same edge in kills" is a coherent question whose answer requires
the score components as features. What changes is what may be claimed from its backtest,
which is the subject of the section below.

### Does it actually predict better?

All three versions are fitted and backtested on every run, and scored **on the same
maps**. This matters: feature sets have different data requirements, so each version's
walk-forward naturally covers a slightly different set of maps, and comparing raw
totals would let a version look better simply by predicting an easier subset. Only the
4,171 maps every version predicted enter the table.

| Version | Brier | Log loss | Accuracy |
|---|---|---|---|
| 1.0.0 (box score) | 0.0541 | 0.1787 | 92.6% |
| 2.0.0 (intangibles) | 0.0420 | 0.1441 | 94.5% |
| **2.1.0 (+ kill feed)** | **0.0416** | **0.1422** | 94.4% |

Brier falls 22% against the box-score baseline for 2.0.0, and 23% for the published
2.1.0. The kill-feed layer on top is a much smaller gain, and an honest reading is
that it is close to a wash overall — it is published as the default because it wins on
both proper scoring rules, not because the margin is decisive.

What that table compares is how well each feature set *describes* a map, and the section
after next explains why it cannot be read as a comparison of forecasting ability. The
per-cohort breakdown is more informative than the total, and less flattering:

| Cohort | Maps | 1.0.0 | 2.0.0 | 2.1.0 |
|---|---|---|---|---|
| 2018 WWII Hardpoint | 1,068 | 0.0451 | **0.0433** | 0.0442 |
| 2018 WWII Search & Destroy | 843 | 0.0605 | 0.0441 | **0.0406** |
| 2018 WWII Capture the Flag | 667 | 0.0485 | **0.0182** | **0.0182** |
| 2019 BO4 Hardpoint | 674 | 0.0474 | **0.0451** | **0.0451** |
| 2019 BO4 Search & Destroy | 518 | 0.0712 | **0.0468** | **0.0468** |
| 2019 BO4 Control | 401 | **0.0627** | 0.0628 | 0.0628 |

Three things are worth saying plainly. Capture the Flag improves enormously, and this is
the row that should be read most sceptically: per-map captures is the CTF score, where
captures *per ten minutes* was that score divided by map length. So 2.0.0 did not
discover anything about Capture the Flag — it stopped dividing the win condition by a
nuisance variable. That is a units fix on a leaked column, and because CTF is 667 of the
4,171 maps it carries a visible share of the 22% headline above. The kill feed helps in
exactly one place — WWII Search & Destroy, where trades decide rounds — and slightly
*hurts* WWII Hardpoint. And Control is the one cohort where the box-score model is not
beaten at all: with only first-blood net and captures available, 2.0.0 has nothing to add
there. A version that wins overall while losing a cohort is the normal shape of this kind
of result, and reporting it is cheaper than defending an average.

The 2017 cohorts appear in the feature table but not the comparison: Infinite Warfare
in this archive is a single event, and a walk-forward backtest needs an earlier event
to train on. Their ratings are published; their predictive validation is not available,
and no substitute is invented for it.

### What the map backtest does not establish

A 94% map accuracy invites a reading it does not support. To measure how much of it is
the scoreboard predicting itself, every feature is put through the crudest baseline
available: take the sign of the team-A-minus-team-B differential on that column, call
that the winner, and score it. No weights, no fitting, nothing learned. It is run on
exactly the maps the fitted model predicted, so the two numbers are comparable.

| Cohort | Maps | Model | Best single column, by sign alone | Model gain |
|---|---|---|---|---|
| 2018 WWII Hardpoint | 1,068 | 93.8% | **94.5%** hill time per 10 min | −0.6 pt |
| 2018 WWII Search & Destroy | 843 | 94.4% | 92.8% kills per round | +1.6 pt |
| 2018 WWII Capture the Flag | 667 | 97.8% | **100.0%** captures per map | −2.3 pt |
| 2019 BO4 Hardpoint | 674 | 93.6% | 92.0% hill time per 10 min | +1.6 pt |
| 2019 BO4 Search & Destroy | 518 | 94.0% | 91.9% plants + defuses per round | +2.1 pt |
| 2019 BO4 Control | 401 | 92.5% | 91.7% kills per 10 min | +0.9 pt |

The Capture the Flag row is the cleanest statement of the problem: on every map where the
two teams did not tie on the column, the sign of the capture differential was **never
once wrong**, because outscoring the opponent in captures is the definition of winning
that mode. The fitted model, with five features and a ridge penalty splitting weight
between collinear ones, does *worse* than the identity buried inside it. WWII Hardpoint
is the same story one step weaker — hill occupancy is the Hardpoint score, up to
teammates standing on the hill at once — and it also beats the model outright.

Everywhere else the whole model adds between 0.9 and 2.1 points over its single best
column, and even a plain kill differential picks the winner on 86–93% of maps. Nothing
here starts anywhere near a coin flip. Two of the rows tie: in WWII Search & Destroy
kills, deaths and survivals per round all score 92.8%, because in a mode with a fixed
four players a side those three columns are near-restatements of each other. The listed
column is the first of the tie, not a claim to be the only one.

The correct summary is that the map backtest confirms the fitted weights transfer to
unseen events, and says nothing whatever about whether this rating can predict a result
before it happens.

The table is generated from a `feature_sign_baseline` artifact stored with each published
rating run, so it is measured on every rerun rather than transcribed once. Its consequence
is accepted rather than patched over: the version comparison above is not evidence of
predictive quality. The two tests that *are* out-of-sample follow, and the rating does not
come out of them well.

### Two tests the rating can fail

Both predict something that has not happened yet, so no feature can contain its answer.

**Does the rating persist?** For every player with two consecutive seasons and at least
eight maps on each side, season *N* predicts season *N+1*. Two predictors — the composite
rating and the era-adjusted K/D z — against two targets, the same pair one season later.
The 2×2 is deliberate: predicting next season's rating flatters the rating, predicting
next season's K/D flatters K/D, so the off-diagonal is where the question actually lives.
216 transitions (96 across IW → WWII, 120 across WWII → BO4), Pearson *r* with a 2,000-draw
bootstrap over players.

| Predictor (season *N*) | → next rating | → next K/D z |
|---|---|---|
| Composite rating | 0.29 [0.17, 0.41] | 0.28 [0.15, 0.40] |
| **Era-adjusted K/D z** | **0.37** [0.26, 0.48] | **0.54** [0.44, 0.62] |

Raw K/D z is the better predictor in both columns, including the one that is the rating's
own output. The contrasts are paired — the same resampled players scoring both predictors,
because comparing two intervals that happen to overlap answers nothing. Predicting next
season's K/D, Δ*r* = −0.26 [−0.37, −0.14], which excludes zero: **K/D z predicts a
player's future better than the composite rating built on top of it, decisively.**
Predicting next season's rating, Δ*r* = −0.08 [−0.20, +0.03] and that interval spans zero,
so the rating is not measurably worse there, merely not better. Moving to the posterior
estimator lifted both rating cells by about 0.01 and left this verdict where it was.

**Does a roster predict future map wins?** Walk-forward by event within each season: at
every event the whole rating pipeline is refit on maps from earlier events only, each
team's players are averaged into a roster strength for that map's mode, and the
differential becomes a win probability through a logistic also fit on those earlier maps.
Nothing from the event being scored enters. 3,760 maps survive on which every predictor
has an opinion; the first event of each season has no history and is skipped.

| Predictor | Brier | Log loss | Accuracy | vs. coin flip |
|---|---|---|---|---|
| **RAPM** | **0.24467** | 0.6976 | 59.3% [57.8, 60.9] | −0.0053 [−0.0115, +0.0009] |
| RAPM, rating-centered | 0.24601 | 0.7047 | 59.7% [58.1, 61.2] | −0.0040 [−0.0104, +0.0026] |
| Roster composite rating | 0.24807 | 0.6931 | 56.4% [54.8, 57.9] | −0.0019 [−0.0066, +0.0031] |
| Same rating, z-and-shrink | 0.24919 | 0.6958 | 56.7% [55.1, 58.2] | −0.0008 [−0.0056, +0.0044] |
| Glicko-2 team rating | 0.25069 | 0.7140 | **60.5%** [58.9, 61.9] | +0.0007 [−0.0061, +0.0078] |
| Roster K/D | 0.25147 | 0.7047 | 57.2% [55.5, 58.7] | +0.0015 [−0.0034, +0.0069] |
| Coin flip at 0.5 | 0.25000 | 0.6931 | — | — |

The rating's Brier is 0.24807, a hair under the coin flip's 0.25000, and a paired bootstrap
over maps puts that at **−0.0019 [−0.0066, +0.0031] against always guessing 0.5** —
indistinguishable from no model at all, and the sign of the point estimate is not worth
reading. Against Glicko-2, −0.0026 [−0.0096, +0.0042]; against the roster K/D, −0.0034
[−0.0072, +0.0003]. Every interval spans zero.

The fourth row is the same rating estimated the old way, and it is here because
[the rating is a posterior](#the-rating-is-a-posterior) needed a test rather than an
argument. Paired on identical maps the posterior wins by −0.00112 [−0.00220, −0.00010]:
its interval excludes zero, its size sits under the 0.00148 this sample could resolve at
80% power, and the pick rates are a coin-flip apart. Read as "the better-specified
estimator does not cost anything out of sample", which is the most this test could have
established either way.

RAPM is the best row in the table and still does not clear the bar. Its −0.0053 gap is the
largest any predictor here manages and its interval very nearly excludes zero, but 3,760
maps could only have resolved a gap of 0.0089, so the honest reading is "closest, not
established". Against the composite rating directly it is +0.0034 [−0.0027, +0.0094] —
better on the point estimate, unresolved on the interval.

Brier and accuracy disagree here, and reporting either alone would mislead, so both are
published. Every predictor picks the winner more often than chance — the rating's 56.4%
interval clears 50% comfortably — so roster strength does carry directional signal. What it
does not carry is a usable probability: the fitted logistic finds so little to work with
that its output barely leaves 0.5, which is exactly what a Brier at the floor with an
above-chance pick rate means. Glicko-2 is the most accurate and has the *worst* log loss,
the signature of a confident model that is over-confident.

### Plus-minus: value in wins, without the box score

Every other player number here starts from the scoreboard, which is why the leakage section
exists and why the persistence test bites. RAPM asks the question from the other end: forget
what a player did, and look only at whether their side won and who else was on the server.
One row per map, one column per player, +1 for one side and −1 for the other, ridge-
regressed on the map result. A coefficient is a player's estimated contribution to the
log-odds of winning a map, holding the other seven constant. No box-score column enters at
any point, which is what makes it an independent check rather than another view of the same
data. 5,087 decided maps, 196 players with at least 20 of them.

Two things have to be reported rather than assumed away, and together they decide how much
the leaderboard means.

**Collinearity, and it is severe.** Four players who never appear apart are one column
wearing four names; ridge responds by splitting the credit evenly, which is correct and is
also indistinguishable from a finding. So every coefficient is published beside that
player's *teammate concentration* — the share of their maps spent alongside their most
frequent teammate. The median is **0.81**, and **86 of 196 players sit at 0.9 or above**.
Four of the top five coefficients belong to players at concentration 1.00, meaning their
number is their duo's, not theirs.

**Shrinkage.** Standard errors come from the penalized Hessian and are published with every
coefficient. The median is 0.53 against a coefficient spread of 0.42, and **only 7 of 196
coefficients exceed 1.96 standard errors.** The ridge path says the same thing from another
angle: as the penalty rises from 0.25 to 64 the spread of coefficients collapses from 0.60
to 0.09 and the ordering's correlation with the lightest fit falls to 0.53. The penalty is
doing much of the work, and nothing here tunes it against the held-out maps — that would
turn the forecast above into a selection statistic rather than a test.

**The blend.** The task this was built from called for combining RAPM with the box-score
rating as an informative prior, which is a one-line change to what the penalty shrinks
toward: instead of zero, each player's coefficient is pulled toward their composite rating
converted into map-win logits, at an exchange rate estimated on the training maps rather
than assumed. The blended coefficients correlate 0.980 with plain RAPM, and in the forecast
the blend is *worse* on Brier (0.24601 vs 0.24467) and slightly better on accuracy (59.7%
vs 59.3%). It is reported because it was asked for and because a mixed result is a result;
it is not adopted, because nothing in these numbers says it should be.

**What RAPM is actually measuring.** At a median teammate concentration of 0.81, a player's
coefficient is substantially their lineup's. That is the honest explanation for the shape of
the table above: RAPM's accuracy (59.3%) lands much closer to Glicko-2's team rating (60.5%)
than to the box-score rating's (56.7%), and it does so while never being told which team is
playing. It behaves like a team rating expressed one player at a time. That makes it the
best available answer to "does player-level information forecast map wins" and simultaneously
a warning against reading its leaderboard as a ranking of individuals.

The `rapm` artifact is stored with the published rating run, and both variants are scored by
the same walk-forward harness as the rating so the comparison happens on identical maps.

**What this means for the rating.** A single map in this league is close to a coin flip,
and knowing which four players are on the server does not measurably change that — nor
does knowing which team it is. The composite rating remains a defensible *descriptive*
measure: it summarizes what a player did, weighted by what actually correlated with
winning maps in that season and mode, and the leakage section above says plainly why its
map backtest scores as high as it does. It is not a forecasting tool, the site does not
present it as one, and the numbers above are the reason.

Two directions were named here as ways to change that verdict rather than restate it, and
both have now been tried. The round-level model in Tier 1d works as a model of a round, but
the player value derived from it — win probability added per kill — turns out to be kill
rate in another unit, and the part that is not kill rate does not reproduce across a
player's own games. Plus-minus does better: RAPM posts the best Brier and a clearly
above-chance pick rate without touching the box score, but its gap over the coin flip still
does not clear what 3,760 maps can resolve, and its coefficients are so entangled with
lineups that most of them belong to a duo rather than a player.

So the verdict stands, with one amendment. Player-level information does appear to forecast
map wins slightly better than the composite rating does — just not by a margin this archive
can establish, and not from the box score. Neither result promotes anything into the
published rating.

The artifacts (`rating_persistence`, `roster_forecast`, `rapm`) are stored with the
published rating run and recomputed on every rerun.

The `rapm` artifact publishes the forty highest and forty lowest coefficients, which is
the right shape for reading the distribution and the wrong shape for reading a player: it
names 80 of the 196 players the model fits, so the other 116 could not be looked up at
all. `player_rapm` (migration 0013) stores the whole fit, one row per player per rating
run, and the player page reads that.

Publishing a per-player coefficient raises the obvious hazard, so the table is explicit
about it. Seven of the 196 coefficients exceed 1.96 SE; the median standard error is 0.53
against a coefficient spread of 0.423, so for almost everyone the ridge penalty is larger
than the signal. Six of those seven players have a teammate concentration of 1.0 — every
map beside the same teammate, one column wearing two names, with ridge splitting the
credit evenly between them. **Exactly one player in this archive has a RAPM coefficient
that both clears zero and is separable from his teammates'**, which is the single most
important thing to know about the number and is not visible from a leaderboard at all.

So the player page draws the interval as the chart and prints the coefficient as a label
on it, states in words whether the interval covers zero, and adds a second notice when
concentration is at or above 0.9. Nothing renders the coefficient without its standard
error.

## Tier 2b: Series dynamics (shipped)

A Call of Duty series is a race to three maps, and much of what gets said about one is a
claim about the race itself: a 1-0 lead is worth more than arithmetic, teams ride momentum
through a series, a reverse sweep is a collapse rather than a coin landing the same way
twice. This section measures all three, over the 1,272 best-of-five series whose maps
reconstruct their scoreline exactly. Thirty-eight of the archive's 1,310 decided series are
excluded and counted: 34 are best-of-one or best-of-three shapes, almost all forfeits, and
4 carry a map with no archived winner.

**The null is conditional independence, enumerated rather than simulated.** Each series'
two teams have a map-level Elo — the blend arm from the section above — frozen *before its
first map*. The league's mode rotation says which five maps they would play. That gives
five independent per-map win probabilities and an exact enumeration of the race: every
scoreline it could have reached, with its probability. No memory of any kind is in that
calculation, so the difference between it and what happened is where a series dynamic would
have to live.

**Why the raw number is not the finding.** The map-1 winner takes 75.9% of these series,
and most of that is not a dynamic at all. Between two identical teams a 1-0 lead in a race
to three is already worth 68.8%, by arithmetic. Between *these* teams at their frozen
ratings it is worth 70.7%. And the ratings themselves are modest: their map-1 calibration
slope is 1.31, meaning true strength gaps are wider than the ratings say — a check run on
map 1 because every series plays it, so unlike maps 4 and 5 that sample is not conditioned
on a result.

That last point is the whole difficulty. A team that wins map 1 is, on the evidence of
having won it, better than its rating said, so it wins map 2 more often than a rating-based
calculation predicts, with nothing carrying over between the maps. Every quantity here has
that problem. So each rate is stated against a second benchmark: the same enumeration at
the strength gap that best explains these results with *no* carryover, fitted below.

| | Observed | Coin flip | At the ratings | Allowing for quality |
|---|---|---|---|---|
| **Map-1 winner takes the series** | **75.9%** | 68.8% | 70.7% — **+5.3** [+3.0, +7.5] | 74.1% — +1.8 [−0.4, +4.1] |
| **Sweep (3-0)** | **36.1%** | 25.0% | 28.1% — **+8.0** [+5.4, +10.6] | 35.6% — +0.5 [−2.1, +3.1] |
| **Goes the distance (3-2)** | **27.4%** | 37.5% | 34.3% — **−6.9** [−9.3, −4.5] | 28.4% — −1.0 [−3.3, +1.4] |
| **Reverse sweep (0-2 down, won)** | **5.3%** | 6.3% | 5.7% — −0.4 [−1.6, +0.8] | 4.7% — +0.6 [−0.6, +1.8] |

Gaps in percentage points with 95% intervals, resampled over series; the pairing matters,
since both columns are computed on the same series. Bolded gaps exclude zero. Against the
ratings alone, every headline signature of momentum is there: too many sweeps, too few
deciders, a 1-0 lead worth five points more than it should be. Against a strength gap wide
enough to explain the same archive with no memory at all, none of them survive. The pattern
holds in each era separately — the map-1 winner takes 76.9% in 2017 IW, 76.9% in 2018 WWII
and 74.3% in 2019 BO4, within a point or two of that era's own quality benchmark each time.

**The direct test separates the two by shape, not by size.** Quality a rating missed is a
property of the *series*: it is shared by every map equally, in any order. Carryover is a
property of *adjacency*. So the whole sequence of map results is modelled at once,

```
P(team 1 wins map m) = sigmoid(a · strength_m + u_series + gamma · prev_m)
u_series ~ Normal(0, sigma²)
```

with `prev` coded ±1 for who won the previous map, `u` integrated out by Gauss-Hermite
quadrature, and the likelihood written over the whole sequence *including map 1*. That last
detail is not cosmetic: fitting a lag model to maps 2 onward while conditioning on map 1
feeds the latent quality straight into the lag term, which is the initial-conditions trap.
The stopping rule needs no such care — a series ending at three wins is a deterministic
function of results already in the likelihood, so the truncation is ignorable.

The fit, over 4,978 maps in 1,272 series: **sigma = 0.74 logits** of team quality the
ratings did not have, and **gamma = −0.02 [−0.11, +0.08]** — in points of map win
probability between a team that just won a map and one that just lost, **−0.8 pt
[−5.5, +3.9]**, likelihood-ratio *p* = 0.74. Fitted on maps 1-3 only, the one panel in the
archive with no stopping rule at all because every best-of-five plays all three, it is −5.1
pt [−12.8, +2.5]: consistent, wider, and pointing the wrong way for a momentum story.

For contrast, the same data regressed the ordinary way — map 2 on the frozen strength logit
and the map-1 result, with no series offset — puts winning map 1 at **+10.9 pt**, *p* =
0.0002. That regression is reported in the artifact next to the null it produces, because
the gap between +10.9 and −0.8 is the finding: the effect is entirely the two teams being
further apart than the rating knew.

**What this archive could have found.** 1,272 series could resolve a carryover effect worth
6.7 points of map win probability at 80% power. So the claim is that momentum inside a
series is not worth as much as a moderate effect would be — not that it is exactly zero.
The rest of the site's momentum null, at series level across an event, is in
[Does it actually predict better?](#does-it-actually-predict-better).

The model is `series_dynamics` v1.0.0; artifacts `series_dynamics` and `series_momentum`.

## Tier 2c: Player style (shipped)

Every roster in this sport is described in nouns — anchor, entry, flex, objective player —
and a signing is explained by the role it fills. Those nouns may well be true of how teams
play. This section asks the narrower question the archive can answer: do the box scores
fall into groups, or into a cloud?

**The null is that there are no groups.** k-means returns k clusters for any k, on any
data, including data with no structure in it at all, so a partition is never by itself
evidence of one. What is published here is the comparison between the partition this
archive gives up and the partition *the same cloud with no groups in it* gives up.

**Quality is removed before the question is asked.** Cluster raw box scores and the
leading axis is "more kills, better ratio, larger share" with every metric loading the
same way — that is a rating, and the site already publishes one, so the "archetypes" would
come out as tiers. Every feature is therefore residualised against the published composite
rating, and what is clustered is the remainder: how a player played at their level, not
what level that was. The rating explains 11.7% of the variance in these features, so the
two are very nearly orthogonal and almost nothing is lost by insisting on the distinction.

**The era is removed too, and this costs more than it sounds.** Metric coverage is not flat
across the archive: the kill feed exists for two titles of three, Hardpoint qualification
runs from 50% of Infinite Warfare's players to 86% of WWII's, and Search and Destroy's
per-10-minute metrics are unattainable in titles whose rounds are too short to earn a deep
streak. Take every metric present in all three seasons and demand a complete row and *no
player-season in the archive qualifies* — the richest-looking feature set describes nobody.
The rows surviving a looser cut are not a random sample either; they skew to the
better-covered seasons and the higher-volume players, so a cluster fitted on them can be an
era wearing a costume. A column is admitted only if it is attainable in every season, and
the published fit runs on the basis that keeps the league — 21 box-score metrics over 484
of 487 qualified player-seasons, every season retained above 99% — rather than the one with
the most columns.

**There is no taxonomy.** On the published basis the gap statistic prefers a single cluster
to every partition from two to seven. The best silhouette any k reaches is 0.286, at k=2,
and a single Gaussian with the same covariance and sample size scores 0.251 to 0.305 on the
same test: the separation observed is what no separation looks like. Bootstrap cluster
stability at k=2 is high — Jaccard 0.961 — and on its own means nothing, which is the trap
this section exists to avoid: bisecting an elongated cloud along its long axis is
enormously reproducible, and the Gaussian null reproduces itself just as well, at 0.876 to
0.974. Every k from three up fails every test.

The extended basis — 52 columns including Hardpoint and Search and Destroy objective
metrics, at the cost of falling to 336 player-seasons and only 41% of Infinite Warfare —
agrees. Its gap statistic does prefer k=2, but that k=2 scores a silhouette of 0.203
against a null band of 0.174 to 0.216 and a stability of 0.920 against 0.876 to 0.967. Both
sit inside what no clusters look like, so the preference is not evidence and nothing is
published from it.

**What is real is the axes.** Horn's parallel analysis — each eigenvalue against the 95th
percentile of the same matrix with every column independently permuted, which destroys
correlation while preserving each metric's own distribution — retains four components,
together 59.9% of the residual variance. Read in raw metric terms:

| Axis | Share | Loads on |
|---|---|---|
| volume | 31.6% | kills, blitz index, kill share, K/D, multikills and pace, all the same way |
| survival | 14.0% | fewer deaths and fewer engagements, with longer streaks and a better plus/minus |
| streak depth | 7.4% | deep, six- and seven-kill streaks and assists, against K/D, headshot rate and four-streaks |
| risk | 7.0% | eight-plus streaks and assists arriving with more team kills, suicides and deaths |

A player is published as a position on those four axes rather than a label. Scores are
signed so that each axis's largest loading is positive, so a rerun cannot silently flip a
career's direction, and stored per player-season because the position moves — which is the
part a label could never have shown.

**Power.** Every verdict is stated against the null it was measured on, with that null's
own spread, so "no taxonomy" always means "no taxonomy separated by more than an
unclustered cloud of this size and shape would show". A well-separated three-group
structure at this n and dimension is recovered easily; the test suite plants one and
requires the code to find it, and requires the same code to refuse an elongated cloud whose
bisection is 0.99-stable. What the archive rules out is groups of that kind. It does not
rule out roles too subtle for 21 box-score columns to see, and no such claim is made.

## Tier 3: Career and player-shape modeling (planned)

The tables exist but the models are not yet written.

- **Aging curves.** Hierarchical fit of adjusted performance against age, or against
  career-season index where birthdate is unknown, giving each active player a position
  on the curve and the league a peak-age estimate with a credible interval.
- **Peak and breakout detection.** Changepoint analysis on rolling adjusted rating,
  flagging career inflections with their magnitude in standard deviations.

Player archetypes are not on this list. They were attempted and the answer was that this
archive has none: see [Tier 2c](#tier-2c-player-style-shipped) for the tests and
[`db/migrations/0012_player_style.sql`](../db/migrations/0012_player_style.sql) for why the
table stores a position rather than a label.

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
- **Series dynamics (shipped).** P(win series | won map 1), sweep, decider and reverse-sweep
  rates against an enumerated no-memory race, and a direct test of momentum claims. See
  [Tier 2b](#tier-2b-series-dynamics-shipped).
- **Roster-change event studies.** Performance k series before and after a move against
  matched controls, reporting the distribution of chemistry effects, including when the
  effect turns out to be null.

## Tier 5: Finding generation (shipped)

A layer of rules and statistics scans model outputs after every run and emits ranked,
plain-English findings in fifteen kinds. Nine read the ratings, the era adjustment and
the series-dynamics run: trends, outliers, milestones, era context, head-to-head edges,
what-wins-maps weight comparisons per (season × mode), the top open-rating seasons, what
winning map 1 is worth against a race with no memory, and published model nulls — the
series-level momentum test, and the carryover null from [Tier
2b](#tier-2b-series-dynamics-shipped).

The what-wins-maps comparison is stated as the gunfight against everything else, not
as slaying against objective play. The model defines exactly one boundary — which of a
cohort's features are the kills/deaths pair — and what remains on the other side varies
by cohort, mixing objective columns with survival and trade economy. Naming the ratio
after the split the model actually makes keeps the claim true across feature-set
versions; calling it "objective play" would not. A cohort whose bootstrap interval
covers 1.0 emits no finding at all: every reading of that ratio is a claim about which
side of even the truth falls on, and where the interval does not answer that there is
nothing to publish.

Six more read the metric layer, which is where the claims a box score cannot make live:

- **intangible outlier** — a season elite at an intangible while ordinary at K/D, or
  the reverse. This is the argument for having a metric layer, stated one player at a
  time.
- **profile extreme** — the league-best qualified season value of a gold metric.
- **clutch milestone** — 1vN records reconstructed from the kill feed.
- **trade asymmetry** — slaying and trade economy pointing opposite ways: the heavy
  slayer who dies alone, the light slayer whose deaths always get answered.
- **meta shift** — a weapon's usage share swinging 20 points or more between
  consecutive events of a season.
- **team style** — rosters at the extremes of how they divided hill duty, opening duty
  and kills.

There are currently 163. Each carries the numbers backing it and a link into the
evidence view, so any claim on the site can be traced to the data that produced it.
These are generated from model output by fixed rules, not written by hand and not
written by a language model.

Two passes at the end keep that count honest, because the raw rules overcount badly.
Several kinds read a table that carries an all-modes row *plus* one row per mode, so a
player with one strong season produced an all-modes K/D outlier and one more per mode
played — the same finding sliced five ways. One player once held nine of thirty
outliers. So each season collapses to its single most extreme slice, and no subject may
contribute more than two findings of any one kind; league-wide rankings and per-cohort
model summaries are exempt, being one fact each already. Career volume is reported as a
rank among the deepest 25 careers rather than as a threshold, because "past 250 maps"
was true of 75 of 273 players and described the threshold rather than the player.

Two more details are worth stating, because both were bugs first. Roughly
half the intangibles are lower-is-better — untraded deaths, first deaths, zero-kill
rounds — so every comparison re-reads a percentile through the catalog's own direction
before calling it good or bad; without that step the generator reported players who were
excellent at *both* K/D and an intangible as contradictions, with a headline claiming
the opposite of the truth. And a "nobody in the league matched this" claim requires
twice the qualifying sample, because clearing a leaderboard minimum is a much weaker
thing than being unmatched.

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
