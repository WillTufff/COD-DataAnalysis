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
Career modeling is specified but not yet implemented. The site covers 2017 to
2026: the CWL archive through 2019, and the CDL seasons from three sources joined
on a per-row provenance tag.

**Not every section below covers both halves of that record, and each says which.** The
two box-score archives measure different things, so some models span 2017-2026 and some
stop at 2019 — the team ratings, the series win-probability model, map-level Elo, the
metric layer, plus-minus and the momentum test all run on the full record; the kill-feed
tier and everything built on it are 2017-2018 by construction; and two surfaces are
narrower than their data because of how they are declared rather than what was recorded,
which is stated where it happens rather than left to be inferred. Where a result changed
when the record grew, this page says what it used to say.

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
| [Cito API](https://citoapi.com) (carries Breaking Point match data) | CDL 2020-2026 box scores, 53,832 player-map rows across 1,713 series | Proprietary; attribution required, redistribution not permitted |
| [Liquipedia](https://liquipedia.net/callofduty) via LPDB API | Full-history structure: tournaments, placements and prize money, rosters, transfers, player bios, map-level results | CC-BY-SA 3.0 |

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

Liquipedia data is accessed only through the LPDB API, never by scraping HTML, at
1 request per 5 seconds with an identifying User-Agent, and every response is
snapshotted so unchanged data is not re-requested. Pages using their data carry
visible attribution, and derived data is shared back under CC-BY-SA 3.0.

The CDL-era box scores are the one source with a licence that constrains what may
leave this project. They are not CC-BY-SA and not redistributable, so the stored
responses are not committed to the repository, they are not served through the
site's export endpoint, and what is published from them is derived analysis —
ratings, era-adjusted metrics, model outputs — with attribution. Every row in the
database carries the source it came from, so that rule is a `WHERE` clause rather
than an inference from the season year.

**The two box-score archives are not the same measurement.** The CWL archive carries
a kill feed, per-shot accuracy, streak and multi-kill counts, and a map clock. The
CDL-era source carries damage, contested hill time, non-traded kills and
source-counted clutches, and no map duration at all. Two consequences run through
everything below. Every per-10-minute metric stops at 2019, and a per-map
counterpart takes over from 2020; a cohort is never given both forms of one
quantity, and nothing is averaged across the seam. And the kill-feed tier — trades,
clutch reconstruction, man-advantage, engagement distance — is 2017-2018 only, and
always was.

**A trade is not the same thing as the kill feed, and this page used to conflate them.**
The feed is what allows a trade to be *reconstructed*: two death timestamps, two teams, a
window between them. That is 2017-2018 and nothing has changed about it. But trade economy
as a measured quantity is not only reconstructible — the CDL-era source counts it directly,
in a `non_traded_kills` column that is populated from 2022 onward. So the record carries
trade economy at both ends and not in the middle: reconstructed for 2017-2018, counted for
2022-2026, and genuinely absent for 2019-2021. The two are different measurements of the
same idea and are never mixed inside a cohort — the reconstructed form is a share of a
player's *deaths* that nobody answered, the counted form a share of their *kills* that
nobody answered, and no title has both.

Project code is licensed AGPL-3.0.

### Completeness is published

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
all 104 metrics, and the team metrics — and the threshold is recorded in each run's
parameters.

That threshold also decides what the CDL era can be asked. A CWL season fields well
over a hundred qualified players; a franchised CDL season fields twelve teams, so its
team cohorts sit below fifteen and publish percentiles with a null z. That is the
policy working as intended rather than a gap: a twelve-team league is too small to
call a roster three standard deviations from its own mean.

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

Splitting cohorts further by LAN versus online is a planned refinement, and the data for
it now exists: every event carries a LAN flag, and the 2020-2022 seasons are genuinely
mixed — 10 online events against 4 on LAN in 2020, 9 against 3 in 2021, and 5 against 6 in
2022 as the league returned. The CWL years are LAN throughout, so the contrast lives
entirely in the CDL era. It is not implemented, and the constraint on it is now work
rather than coverage.

## Tier 1b: Metric layer (shipped)

The archive measures far more than kills and deaths. The metric layer turns every
measured column into a published, era-scored metric, so a player's season can be read
across 104 different lenses instead of four.

Those 104 are not all available in all ten titles, and the split is the seam between
the two box-score archives rather than a curation choice. Fourteen metrics are
published for every title from 2017 to 2026; 81 exist only where the CWL archive's
columns do, and 4 only where the CDL-era source's do. Anything denominated in map time
is in the first group by construction, which is why the per-map counterpart of each
per-10-minute rate exists at all.

Metrics are stored in long form, one row per player, season, mode, and metric, each
carrying its own qualification denominator. That denominator is the real sample size
for that metric: maps for rate-per-map statistics, rounds for Search and Destroy round
rates, kills for kill-denominated shares, shots for accuracy. Qualification thresholds
are 8 maps, 50 rounds, 100 kills and 1,000 shots for those four, with smaller floors
where the denominator is itself a rare event — 25 rounds where a side held or conceded
a man advantage, 20 first deaths, and 5 or 15 clutch attempts depending on the metric.
Every threshold ships in the catalog next to the metric it governs. Rows below the
threshold are still written and still scored against the qualified cohort, so a small
sample can be shown and labelled rather than hidden.

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
never populated must not. Twenty-five columns fall into that second group, in both
archives and in every era.

In the CWL years: Black Ops 4 records fields for time alive and for kills that were not
immediately answered, but both are zero on all 19,120 of its rows; WWII does the same
for hill captures and sneak defuses across 23,048; Black Ops 4 shots and hits are
populated on five rows out of 19,120. In the CDL years the same test catches more.
Contested hill time is declared but empty for 2020 (3,150 rows), 2021 (2,854) and 2026
(3,472), though populated in between. Non-traded kills are empty for 2020 and 2021.
Black Ops Cold War records no assists at all — zero on all 6,892 rows — and none of its
Control round counts, attack, defence or total, carry a value on any of 1,742. And
1v4 clutches sit under the floor in every CDL title, at one to five non-zero rows out of
two to three thousand, which is the shape of a genuinely rare event that this source
cannot separate from a data-entry artefact.

Treating any of those as data would publish a season of zeros as though it were a
finding. They are listed on the methodology page instead, and the metrics that depend on
them simply do not exist for those seasons. The rule is worth restating as a reading
instruction: title coverage on this site is derived from the data on every run, so a
metric absent for a season means the column was not populated, never that the events did
not happen.

The catalog itself, including each metric's formula, unit, direction, threshold, and
measured season coverage, ships as an artifact of the same run that computes the values.
The stat explorer and the metric glossary both render from it, so a definition and a
number cannot drift apart.

Team metrics use the same machinery with the roster as the subject. Results come in two
shapes. Map-shaped metrics — map win rate, kill differential per map, average Hardpoint
margin, Search and Destroy round win rate — sum over a team's maps exactly as player
metrics sum over a player's. Series-shaped metrics are built from series outcomes
instead: series win rate, and deciding-map win rate, the record on maps where both
teams stood one map from taking the series. A series is one result spanning several
modes, so these exist only for the all-modes cohort — slicing a series by mode would
count it once per mode it touched. The archive does not record a series format, but
every covered format is strictly first-to-N, so the winner's map count is the target;
a series whose maps are not all present is skipped rather than replayed wrongly.

Three more measure how a roster spreads its work: the Gini coefficient of hill time
across the four players, the Herfindahl index of first bloods, and the spread of kill
shares. Those describe style, not quality. A roster that shares hill duty evenly is not
thereby better than one that assigns a specialist.

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

Three exclusions. The figure's axis stops at 105 seconds,
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
starting value, and it narrows as the record accumulates.

**Rating periods.** Glicko-2's deviation is only meaningful if it grows while a team is
idle, and that requires periods. One event is one period: a CWL event is a few days of
dense play followed by weeks of nothing, and a CDL major is the same shape around a
league schedule, which is what the method assumes — the paper wants ten to fifteen games
per period, not one. At the close of each period every
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
grid's argmin on the same 3,027 series the score is reported over would be selection on
the test set: the published Brier would then be the best of twenty draws rather than an
estimate of anything. The constants stay declared, and the grid is published as
sensitivity analysis — its job is to show how much the choice matters, not to make it.

Ratings are org-lineage-aware: rating state is keyed on the organisation, not the brand,
so a rebrand continues one curve instead of restarting at 1500. The stored rows still
name the team that actually played, so the site shows the brand of the day on a
continuous line, and a lineage is rated under its founding team.

Lineage membership is declared in the importer's identity file, and it is asserted only
where two brands' series windows do *not* overlap — a same-brand roster playing
concurrently is an academy team, not a rebrand, so `Mindfreak` / `Mindfreak Black`,
`EZG` / `EZG Blue` and the three `GGEA` teams stay on separate curves. `Morituri
eSports` / `Regal Morituri` is left unmerged for the same kind of reason: the older
brand reappears *after* the newer one, which is not the shape of a rebrand.

Applying that test now yields twelve lineages spanning 27 brands, and they touch 1,731
of the 3,027 decided series — a little over half. Almost all of it is the franchised
era, where relocation and title sponsorship rename a team without changing the
organisation: `Chicago Huntsmen` → `OpTic Chicago` → `OpTic Texas`, `Las Vegas
Legion` → `Vegas Falcons` → `Riyadh Falcons`, `Los Angeles Guerrillas` → `Los Angeles
Guerrillas M8` → `Paris Gentle Mates`, and nine more of the same shape. The CWL years
contribute one, `eRa` → `eRa Eternity`, over 23 series.

That is a change in what this feature is worth, and it is worth saying plainly: an
earlier version of this page described the lineage machinery as real, tested and
near-inert, because on the 2017-2019 archive alone it merged a single pair. On the full
record it is load-bearing. Without it the site would restart more than half its rating
curves at 1500 on a rename.

**Series win probability, `winprob_v1` (shipped).** Rather than a third rating system,
this model asks a sharper question: given the ratings, does anything else carry
information about who wins a series? Its features, all computed strictly before each series, are the
walk-forward Glicko-2 and Elo win probabilities (as logits), the combined Glicko-2
rating deviation, each team's win rate over its last ten series, and a shrunken
head-to-head record. The model is L2-regularized logistic regression, refit on an
expanding window every 50 series; until 200 series of history exist it passes the
Glicko-2 probability through unchanged, so its backtest covers the same series as the
baselines and any improvement is attributable to the added features.

That last clause matters, and for a while it was false. The rating systems moved
to whole-roster rating periods while this model went on advancing its own copy of
Glicko-2 one series at a time, so its "pass the Glicko-2 probability through unchanged"
phase passed through a Glicko-2 that appeared nowhere else on the site, and the backtest
table below credited the added features with what was really a difference between two
fits. The settings that define a fit — rating period, lineage map, K, τ — are now passed
in from the same values the published Elo and Glicko-2 runs use, and a test pins the
identity phase against the published Glicko-2 prediction by prediction, at every period
length, so the two cannot drift apart again in silence.

**The answer changed when the record did, and it changed sign.** Over the 2017-2019
archive alone this section reported a null: the added features moved Brier by 0.0014
with an interval spanning zero, and the honest reading was that recent form and
head-to-head history did not improve series prediction by any amount that archive could
measure. Over the full 2017-2026 record of 3,027 series the same comparison, run the
same way, no longer spans zero. Against the Glicko-2 it is built on, `winprob_v1`
moves Brier from 0.22724 to 0.22363 — an improvement of 0.0036, 95% CI +0.0014 to
+0.0062, Diebold-Mariano *p* = 0.004. That interval excludes zero, so the previous
claim that the added features "do not separate in either direction" is not a
conservative statement of the current result; it is the wrong statement, and it is
retracted here rather than softened.

What did *not* change is accuracy. `winprob_v1` calls 63.66% of series correctly
against Glicko-2's 63.63%, a gap of 0.03 points whose interval runs from −1.3 to +1.1
points and comfortably covers zero. Both numbers are published rather than the
flattering one: the supportable reading is a small but real edge on the probability, and
nothing at all on how often the favourite is named.

The learned coefficients say where it came from. At the final refit, on 3,000 training
series, `form_diff` sits at **+0.51** on a feature spanning roughly −1 to +1 — the
second-largest weight in the model, ahead of Glicko-2's own logit. On the CWL archive
alone the same coefficient fitted at −0.16, small and pointing the wrong way for a
momentum story, which is what a weak feature looks like beside strong collinear ones.
It is now neither small nor wrongly signed. Head-to-head contributes +0.11, the summed
rating deviation −0.09, and the ridge still splits the two rating logits unevenly (0.55
on Elo against 0.20 on Glicko-2, which are near-restatements of each other); read those
two together, as with the slaying pair in the player rating.

The gap carries a power statement as well as an interval, and on the larger record the
two now agree. Every model predicts the same 3,027 series, so the comparison is paired:
the per-series difference in squared error is one observation, its mean is the gap, and
a 2,000-draw bootstrap over series gives the interval. The 0.0036 gap sits just above
the 0.0035 that 3,027 series can resolve at 80% power — clearing both tests, but only
just, which is the correct amount of confidence to have in it.

The same closed form says what a form effect would have to be worth to show up here.
Suppose the true probability is Glicko-2's logit plus β × `form_diff`; the expected
paired Brier gain and its variance both follow directly, so the smallest detectable β
does too. At 3,027 series, 80% power and a two-sided 5% level, **β would have to be
0.95 or larger** — a team arriving on a 10-0 run against one on 0-10 being about 22
percentage points more likely to win than the ratings alone say. The fit found 0.51.

So the null has become a bounded positive rather than a null, and the bound is what
matters. Recent form and head-to-head carry information the ratings do not, worth a few
thousandths of Brier and no measurable accuracy. An effect twice that size would have
been comfortably visible and is not there. "Momentum decides series" remains something
this record does not support; "momentum is worth nothing" is what it no longer supports
either.

**Validation (shipped).** Models are evaluated by walk-forward backtest, which is to
say each prediction is made using only data available before that series. Current
results, over the full 2017-2026 record of 3,027 decided series:

| Model | Brier | Log loss | Accuracy |
|---|---|---|---|
| Elo | 0.22181 | 0.6345 | 64.4% |
| winprob_v1 | 0.22363 | 0.6393 | 63.7% |
| Glicko-2 | 0.22724 | 0.6489 | 63.6% |

All three are fitted the same way: same lineage map, same K and τ, and — where the model
has periods at all — the same event-length rating periods. That was not true until
recently, and the row that changed is `winprob_v1`, which had been carrying a per-series
Glicko-2 of its own.

**`map_elo` is scored in its own section rather than this one, and it is now scored on
the same series.** Its series rollup needs the title's mode rotation to enumerate a
best-of-five, and for two years only the three CWL titles declared one — so 1,633 CDL
series were rolled up for no arm at all, the rollup covered 1,310 series over 2017-2019
against 3,027 for every other model, and the two could not be paired. All ten titles now
declare their rotation, which puts the rollup on 2,869 of these 3,027 series; the
remainder are the races to four or five that a best-of-five enumeration does not
describe. The contrast against the three models above is [in that section](#map-elo),
paired series by series, and it is the comparison this table used to be unable to make.

The spread across the table is about 0.005 of Brier and 0.8 points of accuracy, on 3,027
series — and because every model predicts the same series, those gaps are paired data
with intervals rather than a leaderboard to be read off:

| Contrast | Brier gap | 95% CI | DM p | Detectable at 80% power |
|---|---|---|---|---|
| Elo − Glicko-2 | −0.00544 | −0.00858 to −0.00258 | 0.0005 | 0.00437 |
| Elo − winprob_v1 | −0.00182 | −0.00349 to −0.00027 | 0.029 | 0.00234 |
| Glicko-2 − winprob_v1 | +0.00362 | +0.00140 to +0.00617 | 0.004 | 0.00349 |

A negative gap favours the first model. All three contrasts now exclude zero, and the
ordering they establish is Elo, then `winprob_v1`, then Glicko-2. **The simplest model
on the page still beats both of the ones built to improve on it**, and it does so by a
margin that clears its own power threshold against Glicko-2 (0.00544 against 0.00437).

Two of these should be read with the qualifier attached. Elo's edge over `winprob_v1` is
0.00182 against a detectability threshold of 0.00234: the bootstrap interval excludes
zero while the 80%-power criterion says a gap that small would usually be missed, which
is what a real but marginal difference looks like. `winprob_v1`'s edge over Glicko-2
clears its threshold, but only by three parts in a hundred thousand. Accuracy separates
nothing at all — every accuracy interval in the table spans zero, including the 0.8-point
spread between Elo and Glicko-2.

The whole table is computed by `ratings/significance.py` and stored as a `model_gaps`
artifact with the winprob run, so it is remeasured on every rerun.

One caution about reading Glicko-2's row as a verdict on rating periods: the
hyperparameter sweep finds series-length periods scoring better (Brier 0.22336 at τ=0.2,
against 0.22724 for the published event-length periods), and they were *not* adopted for
it. The period length is argued from the shape of the calendar — an event is a few days
of dense play then weeks of nothing, which is what Glicko-2's periods assume — and the
sweep is published as sensitivity, never as the selection rule. Picking hyperparameters
on the backtest that then validates them is how a backtest stops meaning anything. The
same sweep puts Elo's best K at 32, which is the declared value, and its Brier is flat to
four decimal places between K=28 and K=40.

Brier score, log loss, accuracy, and calibration curves are published for every model
version. The Brier and accuracy *differences* between them carry intervals, as above;
log loss does not, and no statement here rests on a log-loss gap.

Model outputs are versioned against the run that produced them, recording code version,
hyperparameters, and training window. A rerun replaces a whole run rather than editing
rows in place, so any published number can be traced back to the exact code and data
window that generated it.

### Map Elo

The team ratings above rate 3,027 series while the 11,623 decided maps underneath them go
unrated. That is the smaller half of what this section is about. The larger half is that
a series result is a blend of three or four different games — a Hardpoint, a Search and
Destroy, a Control or Capture the Flag — and Call of Duty rosters are not equally good at
all of them. A single number per team cannot say "top three in Hardpoint, mid-table in
Search", and as far as we can tell nothing published anywhere says it.

So `map_elo` fits three arms, all Elo, all on the same 11,623 maps, all strictly
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
computed for each of the five maps the title's rotation would play, and those are
combined into P(win the series) as a best-of-five. The number of maps *actually* played
is not known in advance and is never read, because it is the result: a series that went
five was 3-2 by definition. A test asserts the rollup is bit-identical whether the series
went 3-0 or 3-2.

The rotation is a league rule, known before the series starts. Maps 1, 2, 4 and 5 are
Hardpoint, Search, Hardpoint, Search in every title on record, so only the third map is
declared per title: Uplink (IW), Capture the Flag (WWII), Domination (MW19), Overload
(BO7), and Control everywhere else. It stays declared rather than derived — reading the
rotation off the series being predicted would leak the result — but a declared constant
nobody checks is just an assertion, so a test holds each one to the archive at 95% of the
maps in that slot. All 35 CDL (title, map) cells are unanimous; the CWL titles run 95.3%
to 99.6%, the exceptions being the handful of series that swapped a map.

Ten titles declare a rotation now; for two years three did, and the 1,633 CDL series that
declared none were dropped from every arm's rollup without anything failing. The rollup
below covers 2,869 series over 2017-2026. The 158 it does not cover are the races to four
or five — 83 of them — plus series the archive holds only part of; their `best_of` column
cannot be used to find them, since it records seven on five-map scorelines and five on
seven-map ones, so they are identified by the winner's own map count and counted rather
than scored against a question they did not ask. A release now fails if that count of
undeclared rotations is anything but zero.

**The result on maps.** Scored on all 11,623 maps, against the 0.25000 a coin flip gets:

| Arm | Brier | Log loss | Accuracy |
|---|---|---|---|
| blend | 0.23760 | 0.66785 | 59.5% |
| global | 0.23774 | 0.66844 | 59.2% |
| mode | 0.24091 | 0.67478 | 57.8% |

**A mode-specific rating does not beat a global one at predicting map winners. It loses.**
Global − mode is −0.00317, 95% CI −0.00473 to −0.00166, DM p < 0.0001, against a
detectability threshold of 0.00217 — it clears both tests, and it clears them by more than
it did on the CWL archive alone. Global beats mode at every K in the sweep, from 4 to 48,
so this is about the granularity of the state and not about a constant chosen for one arm.
The blend is indistinguishable from global (+0.00014, CI −0.00092 to +0.00113, p = 0.78)
and beats mode outright by 0.00331 (CI +0.00261 to +0.00400).

That is the answer to the question this was built to ask, and doubling the sample did not
change it: cutting the record by mode costs more in precision than mode identity returns
in signal. It is worth naming what that does *not* say. The mode arm's problem is that
each rating sees a fraction of a team's maps, so the result is about sample, and a larger
record makes it *more* visible rather than less — because global and blend sharpen with
the extra data while mode stays thin.

**Where it goes wrong, per mode.** The overall number hides a real pattern, so the same
contrast is computed within each mode:

| Mode | Maps | global | mode | blend | global − mode | 95% CI |
|---|---|---|---|---|---|---|
| Hardpoint | 4,855 | 0.23410 | 0.23634 | 0.23436 | −0.00224 | −0.00402 to −0.00039 |
| Search and Destroy | 3,810 | 0.24868 | 0.24800 | 0.24619 | +0.00068 | −0.00239 to +0.00369 |
| Control | 1,681 | 0.23020 | 0.23916 | 0.23315 | −0.00896 | −0.01327 to −0.00467 |
| Capture the Flag | 737 | 0.22710 | 0.23832 | 0.22849 | −0.01122 | −0.01770 to −0.00472 |
| Overload | 282 | 0.22902 | 0.23886 | 0.23002 | −0.00984 | −0.02014 to +0.00111 |
| Domination | 179 | 0.23429 | 0.24192 | 0.23569 | −0.00764 | −0.01865 to +0.00323 |
| Uplink | 79 | 0.23269 | 0.24669 | 0.23339 | −0.01400 | −0.02289 to −0.00541 |

Search and Destroy is still the only mode whose gap does not run against the mode arm —
and its interval still spans zero (p = 0.66, detectable at 0.00434). The correct statement
remains that Search is the one mode where mode-specific state is *not shown to hurt*, not
one where it helps. It is also the mode where the blend does best, beating global by
0.00249 (CI +0.00035 to +0.00467), which is suggestive and sits just inside its own power
threshold of 0.00302. Search is the mode with the least scoreboard signal and the most
distinct skill, so a residual there is the result worth chasing with more data — and note
that going from 1,656 Search maps to 3,810 narrowed that interval without resolving it.
Control has crossed the other way: on 485 BO4 maps its gap was the second-widest in the
table, and on 1,681 maps across both eras it is a firm loss for the mode arm. The three
thinnest modes go the other way hard: Uplink, Overload and Domination each lose the better
part of a hundredth of Brier to mode-specific state, and only Uplink's interval clears
zero — which is what a rating with one to three hundred maps behind it looks like from
both sides at once.

**Is mode specialization real at all?** A spread of per-mode ratings proves nothing on its
own — fit five noisy numbers per team instead of one and they will differ. So the spread
is tested against a permutation null. Mode labels are shuffled *within each event*, which
keeps every team, opponent, result, date and the event's own mode mix and destroys only
the association between a team and which mode it was playing. The statistic is the SD
across qualified (team, mode) cells of that cell's rating minus the team's own global
rating.

Over 162 cells with at least 25 maps each, the observed spread is **63.4 rating points**
against a permuted null of 62.0 (95% range 58.4 to 65.7) over 300 refits: **p = 0.27,
well inside the null.** About 1.4 points of spread survive what noise alone supplies, out
of 63.4.

This is the same verdict the CWL archive gave, and the extra data moved it the wrong way
for a mode-specialization story: with 98 cells the observed spread cleared the null's
midpoint by 3.4 points at p = 0.06, close enough to be worth another look; with 162 cells
it clears by 1.4 at p = 0.27. **This record cannot show that Call of Duty teams have real
per-mode strengths, distinct from being good or bad in general**, and it now says so with
more sample rather than less. The per-mode table is still stored and shown, because the
ordering is the thing readers ask for and hiding it would not make it less tempting
elsewhere — but it is published with this number attached, and the largest gaps in it
(Chicago Huntsmen −188 in Domination, eUnited −173 in Control, 100 Thieves −162 in Search)
are within the range shuffled labels produce. Note what the null does *not* rule out: an
effect too small for 11,623 maps to separate from noise. "Mode specialization is not
measurable here" is what this says; "mode specialization does not exist" is not.

**Reading `mode_ratings.delta` off the artifact.** The stored `delta` is a cell's rating
minus the team's global rating, and it is not centred: across the 162 qualified cells it
averages **−23** and is negative in 110 of them. That is a property of the estimator, not
of the league. A mode rating is fit on a fraction of the maps the global rating sees, so
it regresses further toward the initial value, and the size of the pull depends on how
much of the rotation the mode is — control −34 on average, capture the flag −30, search
−24, hardpoint −18, and the two thinnest modes least of all because their cells barely
clear the 25-map floor. Printed raw, `delta` says almost every team is worse at every mode
than they are overall, which cannot be true of a set of modes that make up the whole. The
figures quoted above carry that offset and are quoted only to show the range the null
covers.

Team pages therefore subtract the field's mean gap in each mode before drawing anything,
which leaves a gap against the field rather than against the estimator. The chart shades
the null band behind the bars and mutes every bar that falls inside it, and the verdict
above travels with it in the same component, so no page can render the ordering without
the number that says how much of it is real.

**What the extra sample does buy.** The one thing that clearly works is rating maps at
all. Rolled up to series and paired against the series-level models on the same 2,869
series they both cover:

| Model | Brier | Accuracy |
|---|---|---|
| map_elo, blend | 0.21924 | 65.0% |
| map_elo, mode | 0.22120 | 64.6% |
| Elo | 0.22132 | 64.5% |
| map_elo, global | 0.22264 | 64.5% |
| winprob_v1 | 0.22305 | 63.8% |
| Glicko-2 | 0.22627 | 63.8% |

| Contrast | Brier gap | 95% CI | DM p | Detectable at 80% power |
|---|---|---|---|---|
| blend − Elo | −0.00209 | −0.00374 to −0.00049 | 0.012 | 0.00233 |
| blend − winprob_v1 | −0.00381 | −0.00596 to −0.00172 | 0.0005 | 0.00306 |
| blend − Glicko-2 | −0.00704 | −0.01040 to −0.00375 | <0.0001 | 0.00473 |

**Rating maps and rolling them up beats rating series directly, and it now survives the
test it had never been given.** For two years this held only over the 1,310 CWL-era
series a declared rotation reached, against an Elo row covering 3,027 — the numbers were
not paired and the honest statement was that the result had not been tested since 2019.
Paired over both eras it holds against all three series-level models. Nothing about the
model changed; it sees 3.9× as many results.

One caveat on the closest of those three. Against Elo the gap is 0.00209 while the size
this many series can find at 80% power is 0.00233, so the archive was underpowered for the
effect it reports and found it anyway — the interval excludes zero, but an effect that
lands under its own detectability threshold is one a rerun on comparable data would miss
about as often as not. Against `winprob_v1` and Glicko-2 the gaps clear their thresholds
outright. The claim this record supports firmly is that map ratings beat the two
series-level models with the most machinery in them; against plain Elo it is ahead on a
margin at the edge of what 2,869 series can resolve.

The rollup also separates the arms in a way the map-level scores could not. On maps the
blend was indistinguishable from global (p = 0.78); on series it beats global by 0.00341
(CI +0.00140 to +0.00538, p = 0.001) and mode by 0.00196 (CI +0.00043 to +0.00346,
p = 0.010). Nothing about the ratings differs between the two views — the same numbers are
being asked a harder question, and enumerating five maps rewards a rating that is right
about *which* map more than a single map's Brier does. Accuracy moves with Brier in every
one of these contrasts but resolves in only one of them (blend over winprob_v1, +1.2
points), which is the usual gap between a proper score and a threshold count.

Sensitivity is stored as a `map_sweep` artifact and, as everywhere else on this page, does
not choose anything: K is declared at 16 (the grid's best for the global arm is 12, for
the mode arm 16 and for the blend 20, all within 0.0003 of Brier of each other) and the
blend constant at 40 (the grid mildly prefers 160, by 0.0006, and the curve is flat from
40 upward). All of it — `map_backtest`, `series_rollup`, `mode_specialization`,
`mode_ratings`, `map_sweep` — is computed by `ratings/maplevel.py` and rewritten on every
pipeline run.

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

**What the rating covers, and what it costs across the seam.** Step 1 fits 28 cohorts:
nine CWL, one per (title × mode), and nineteen CDL — Hardpoint, Search and Destroy and
Control for every season from 2021, and Hardpoint and Search for 2020 and 2026, whose
third map (Domination, Overload) has no feature set of its own.

It fitted sixteen until recently, and the twelve that were missing are worth naming
because of how they went missing. A per-10-minute rate reads two things, a numerator and
the map clock, and only the numerator was declared as a source. So a CDL Hardpoint cohort
reported itself available on kills, assembled, and then emptied one zero denominator at a
time — the CDL box scores carry no map duration — leaving Search and Destroy, which is
denominated per round, as the only CDL cohort that survived. Every 2020-2026 composite was
a Search and Destroy rating wearing an all-modes label, and nothing in the pipeline said
so. The denominator is now a declared source like any other, and a rate that needs a clock
resolves per title: per ten minutes where the clock exists, per map where it does not.

The seam still costs something, and it is now visible instead of hidden. What each cohort
fits on is recorded per cohort in the `mode_weights` artifact, so this table is generated
rather than transcribed:

| Era | Hardpoint | Search and Destroy | Control |
|---|---|---|---|
| CWL | kills, deaths, hill time, hill captures, time per life — per 10 min | per round, with the kill feed where there is one | kills, deaths, captures, first-blood net |
| CDL | kills, deaths, hill time — per map | per round | kills, deaths — per map |

The CDL rows are thinner because the source is thinner: hill captures and Control captures
are not in the Cito box scores at all, and a coverage measurement rather than a
declaration is what drops them. That leaves CDL Control fitted on kills and deaths alone —
a slaying rating with a Control label, and the one cohort on this page whose
[beyond-the-gunfight ratio](#how-much-of-the-weights-is-signal) cannot be computed because
it has no non-slaying feature to compute it from. A CWL-era rating still blends three
richer cohorts than a CDL-era one does. (This describes the published version. The 2.2.0
feature set, fitted and compared but not published, gives 2022-2025 Control a non-slaying
column and therefore a ratio — see [four feature sets, compared](#what-the-rating-measures-four-feature-sets-compared).)

Its validation is walk-forward within each (season × mode): every event's maps are
predicted using weights trained only on earlier events. That number establishes one
narrow thing — that the learned weights generalize across events rather than memorizing
them — and it is not evidence that the model can forecast anything. Several of the
features *are* the win condition, so the map accuracy is largely a decomposition of the
final score. The size of that effect is measured in
[what the map backtest does not establish](#what-the-map-backtest-does-not-establish)
below; read the two together.

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
noise.** Across all 28 cohorts it runs from 0.36 in 2017 IW Search & Destroy — where a
721-map cohort of five-map seasons leaves most of the visible spread unexplained by
skill — to 0.94 in 2022 Vanguard Hardpoint. The pattern is by mode rather than by era:
Hardpoint sits between 0.87 and 0.94 in the CDL years, Control between 0.77 and 0.82, and
Search and Destroy between 0.54 and 0.79. Search is where a season's visible spread is
least about skill, in both eras and by a wide margin.

**Two cohorts used to fit at exactly zero, and the reason is worth keeping.** In 2021
Black Ops Cold War and 2022 Vanguard the fit landed on τ² = 0, so B_i was zero for
everyone, every posterior mean collapsed onto μ, and all 63 and 61 players in those
seasons carried a published rating of exactly 1.00. Nothing failed and nothing said so.

The cause was not the data. Those cohorts are not thin — 63 and 61 qualified players over
2,296 and 2,262 maps, against 62 and 2,632 for 2025, which fitted normally — and their
per-map spread in kills per round matches their neighbours to two decimal places. The
cause is that τ² = 0 is a **fixed point** of the EM iteration: at τ² = 0 every B_i is
zero, so every θ̂_i is μ, so the M-step returns zero, and the loop exits reporting
convergence after a single step having looked at nothing. The iteration was started at
Var(x) − mean(v) floored at zero, and that moment estimate goes negative whenever σ² is
large — a property of the noise, not of the players — which started those two cohorts
exactly on the point they could not leave. Both showed one iteration where every other
showed 39 to 1,336.

The start is now floored at a strictly positive share of the observed spread instead. EM
is monotone in the marginal likelihood, so a cohort whose optimum really is on the
boundary still descends to it; it just has to get there by iterating rather than by
assuming it. Both cohorts now fit in the interior — 2021 BOCW Search & Destroy at τ = 2.22
over 343 iterations, 2022 VG at τ = 1.51 over 399 — and the whole record fits between 20
and 1,336 iterations with nothing on the boundary.

Two things guard it. "Collapsed" is now a statement about the fit rather than about the
optimizer — τ² negligible beside what one season's maps measure — so a run that never
iterated is caught where a "did not converge" test missed it, and running out of
iterations is flagged separately because it is a different condition. And a collapsed
cohort publishes nothing: the row keeps its map count and carries a null rating rather
than 1.00 for everyone, which is the rule the metric layer already applies below its
minimum cohort size. **1.00 for every player is a claim that they tied, not an abstention
from the claim.** A release fails if any cohort collapses.

**The interval is the posterior's, and it is not the bootstrap.** Resampling a shrunk
point estimate measures how far that estimate would move on other maps, B√v. The
posterior SD measures what is still unknown about the player after pooling, √(Bv), which
is larger by 1/√B: a median of **1.82×** across this record, with a quartile range of
1.55 to 2.34 and worse on short seasons. The old ±rating_sd was answering a question
nobody asked of it, and every band drawn from it was too tight. Per-mode rows now carry
an interval too; the bootstrap only ever existed for the all-mode blend.

**One assumption, measured rather than asserted.** v_i = σ²/m_i treats the season
profile as a mean of m maps, but it is a ratio of summed numerators to summed
denominators — close, not identical. Rather than caveat that, each cohort measures it:
every player-season's score is resampled from its own maps, and the ratio of that
variance to σ²/m is averaged over the cohort. The median is **0.964** (0.877 to 1.024 by
cohort, over 2,711 player-seasons) — the profile is a few percent steadier than the plain
form assumes, and σ² is scaled by the measured factor before the fit rather than after. It
matters more than it looks: v enters τ² = Var(x) − mean(v) with a minus sign, so an
overstated observation variance does not merely widen intervals, it eats the
between-player variance and reports a cohort as flatter than it is. Left uncalibrated,
2017 IW Search & Destroy fits at τ² = 0; calibrated, it fits at 0.36 and is the lowest
value on the page.

**What moved.** The published ratings shift by 0.019 on average and 0.070 at most, on a
scale whose league SD is 0.15; the rank correlation between the two estimators is 0.988
and seven of the top ten qualified seasons are the same players. It remains a
re-estimation rather than a re-ranking.

**Does it forecast better?** Being better specified is an argument, not evidence, so the
new estimator and the old one are both run through the roster forecast in
[two tests the rating can fail](#two-tests-the-rating-can-fail): identical maps,
identical weights, identical prefixes, differing only in the step being tested. The
posterior wins by −0.00041 of Brier [−0.00090, +0.00001] over 9,030 maps, and that
interval now touches zero. On the CWL archive alone the same contrast excluded it, at
−0.00112 [−0.00220, −0.00010]; the honest reading of the larger sample is that the two
estimators are indistinguishable out of sample, not that the posterior wins by a little.
Pick rates are a coin flip apart, 56.1% against 56.2%. The case for the change rests on
the specification and the intervals, and the forecast says it costs nothing — which is
still what it had to say, and is now all it says.

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
touch, which is the conservative direction — and on the full record that test now
separates nothing at the top: **all nineteen chasing seasons on the rating board's top
twenty reach the leader's interval.** On the CWL archive alone it was eight of nineteen.
The change is not that the leaderboard got closer; it is that CDL-era seasons are short,
so their posterior SDs run two to three times the CWL era's and the bands they draw are
correspondingly wide. The ordering of that board is an ordering of estimates, and the page
should be read as saying nothing about which of its top twenty seasons was best. Elo
carries no interval because the estimator does not produce one; that is why the team pages
publish Glicko-2 alongside it.

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
| 2019 BO4 Hardpoint | 206 | 12.0 | −3.0 | 0.87 |
| 2019 BO4 Search & Destroy | 206 | 21.5 | +6.5 | 0.76 |
| 2019 BO4 Control | 205 | 10.8 | −4.2 | 0.83 |
| 2020 MW19 Hardpoint | 76 | 9.4 | −5.6 | 0.90 |
| 2020 MW19 Search & Destroy | 76 | 35.4 | +20.4 | 0.68 |
| 2021 BOCW Hardpoint | 63 | 14.0 | −1.0 | 0.87 |
| 2021 BOCW Search & Destroy | 63 | 78.5 | +63.5 | 0.56 |
| 2021 BOCW Control | 63 | 16.4 | +1.4 | 0.79 |
| 2022 VG Hardpoint | 61 | 5.9 | −9.1 | 0.94 |
| 2022 VG Search & Destroy | 61 | 90.1 | +75.1 | 0.54 |
| 2022 VG Control | 63 | 19.1 | +4.1 | 0.77 |
| 2023 MWII Hardpoint | 63 | 9.3 | −5.7 | 0.92 |
| 2023 MWII Search & Destroy | 63 | 45.8 | +30.8 | 0.69 |
| 2023 MWII Control | 63 | 16.6 | +1.6 | 0.81 |
| 2024 MWIII Hardpoint | 65 | 9.8 | −5.2 | 0.92 |
| 2024 MWIII Search & Destroy | 65 | 34.5 | +19.5 | 0.74 |
| 2024 MWIII Control | 65 | 16.6 | +1.6 | 0.81 |
| 2025 BO6 Hardpoint | 62 | 14.6 | −0.4 | 0.89 |
| 2025 BO6 Search & Destroy | 62 | 26.1 | +11.1 | 0.79 |
| 2025 BO6 Control | 62 | 16.7 | +1.7 | 0.82 |
| 2026 BO7 Hardpoint | 76 | 7.8 | −7.2 | 0.93 |
| 2026 BO7 Search & Destroy | 76 | 34.4 | +19.4 | 0.72 |

The old constant was close for the respawn modes — Hardpoint lands between 5.9 and 14.6 in
all ten titles, Control between 10.8 and 19.1, Uplink at 10.4 — and far too weak
everywhere else. Search & Destroy wants 21 to 90 maps in every title it appears in, in
both eras and under both leagues' formats: a round-scale scoreline with four players a
side is noisy enough that a season needs two to six times as many maps before it says as
much about a player as a Hardpoint season of the same length. Capture the Flag sits
between the two. That ordering is not something a fixed constant could express, it is the
substantive result here, and having both modes in every CDL season rather than one has
sharpened it rather than complicated it.

The two extreme rows are 2021 BOCW and 2022 VG Search & Destroy at 78.5 and 90.1. Those
are the cohorts that used to fit at τ² = 0 and publish 1.00 for everyone; fitted properly
they are not degenerate, they are noisy — the largest σ² of any Search cohort on record,
about 20% above their neighbours, against an ordinary spread of true skill. A season of
those two years says less about a player than any other season in the archive, which is a
real finding about those seasons and was previously reported as the players being
indistinguishable.

The moment estimator that first produced these numbers is still fitted and still shipped
as the `rating_shrinkage` artifact, next to the model's k in `rating_posterior`. Its
median across all 28 cohorts is 16.1 maps against the model's 16.6 — the two agree closely
where a cohort is well sampled, and diverge exactly where they should. On 2017 IW Search &
Destroy, where 5.6 maps per player is thin enough that how you weight players changes the
answer, they differ by 15 maps (37.3 against 52.9). Keeping both visible is cheaper than
arguing about which is right.

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
| 2020 MW19 Hardpoint | 307 | 2.96× | 2.04 – 4.29 |
| 2020 MW19 Search & Destroy | 234 | 0.35× | 0.19 – 0.49 |
| 2021 BOCW Hardpoint | 357 | 4.01× | 2.62 – 6.48 |
| 2021 BOCW Search & Destroy | 287 | 0.21× | 0.15 – 0.30 |
| 2022 VG Hardpoint | 357 | 5.20× | 3.52 – 8.65 |
| 2022 VG Search & Destroy | 284 | 0.49× | 0.35 – 0.70 |
| 2023 MWII Hardpoint | 423 | 4.00× | 2.77 – 5.66 |
| 2023 MWII Search & Destroy | 324 | 0.38× | 0.24 – 0.52 |
| 2024 MWIII Hardpoint | 423 | 3.48× | 2.45 – 4.67 |
| 2024 MWIII Search & Destroy | 335 | 0.36× | 0.28 – 0.51 |
| 2025 BO6 Hardpoint | 413 | 5.51× | 3.65 – 9.51 |
| 2025 BO6 Search & Destroy | 329 | 0.48× | 0.34 – 0.69 |
| 2026 BO7 Hardpoint | 440 | 3.40× | 2.26 – 5.42 |
| 2026 BO7 Search & Destroy | 351 | 0.23× | 0.16 – 0.32 |

The seven CDL Control cohorts have no row. They are fitted on kills and deaths and nothing
else — the Cito box scores carry no Control captures — so there is no "beyond" to put in a
numerator, and a ratio is not published for a cohort that has only one half of it.

The intervals are wide, and unequal by a wide margin: Search & Destroy's span roughly
±15% of the point estimate in the deep CWL cohorts, while Uplink's runs from 1.29× to
6.05× — a factor of five, on 79 maps. Reporting those two side by side as "0.35×" and
"2.61×" was the problem. The CDL cohorts sit in between, at 234 to 440 maps each, and
their intervals are correspondingly looser than the CWL Search & Destroy rows without
being anywhere near Uplink's.

Twenty of the twenty-one cohorts resolve, in the only sense that matters here: their
interval excludes 1.0, so the sign of the claim survives. One does not. 2017 IW Hardpoint
sits at 0.74× with an interval of 0.46 to 1.04, and 126 maps cannot say which half
carried that mode. Its finding is suppressed rather than published with a hedge, and
the chart fades the bar instead of dropping it, because "we cannot tell" is the reading
for that cohort.

Directions survive the added rigor. Search & Destroy is gunfight-decided in all ten
titles, and decisively so after 2019: every CDL season's ratio lands between 0.21× and
0.49×, below all three CWL Search & Destroy figures. BO4 Control is gunfight-decided
overwhelmingly. WWII Hardpoint and Capture the Flag are decided by what happens away from
the gunfight, both by a factor near two or better.

**The Hardpoint result holds after 2019, and it is larger.** This table could not say so
until recently, because the CDL Hardpoint cohorts were being dropped for an undeclared
denominator; with them fitted, every CDL season lands between 2.96× and 5.51× with an
interval clear of 1.0, against WWII's 1.96×. Objective play weighs more against slaying in
modern Hardpoint than it did in 2018, in all seven seasons, and that is the most
substantial finding this fix unlocked.

It is not, however, the same measurement, and the size of the jump should not be read as
though it were. A CWL Hardpoint cohort puts three columns in the numerator — hill time,
hill captures, time per life — while a CDL one has only hill time, because the other two
are not in the source. The CDL ratio is therefore one strong objective column against two
slaying columns, where WWII's is three against two, and a numerator concentrated in its
best column will rate higher than one diluted across three. The comparison that survives
that objection is the sign and the interval, not the multiple: modern Hardpoint is decided
away from the gunfight, decisively, in every season on record. Whether it is *twice* as
decided as WWII's is a question this basket cannot answer.

The intervals ship in the `mode_weights` artifact with every rating run, per coefficient as
well as per ratio, and the artifact now records which denominator each feature resolved
to, so this table and the feature table above are remeasured on each rerun rather than
transcribed.

### What the rating measures: four feature sets, compared

Steps 2 to 4 above never change. What changed across versions is step 1's answer to
"which numbers describe a team's map", and all four answers are kept runnable so the
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
- **2.2.0** — claims the columns both archives already populate and that no earlier
  version had named. Nothing new was fetched: damage, the share of a player's kills
  nobody traded back, contested hill time, shot accuracy, headshots per kill and hill
  defends were all loaded, all coverage-measured, and all unused. Fitted, backtested
  and compared here; **not yet the published version** — promoting one changes what the
  site's leaderboards mean, which is a decision this page records rather than a
  consequence of a feature set existing.

No version declares which titles it applies to. Every feature names the source columns
it reads, and a cohort keeps a feature only if its title actually populated those
columns — measured from the data on every run. That is why the feature sets below
differ per season without a hand-maintained matrix anywhere:

| Cohort | Features used |
|---|---|
| 2017 IW Hardpoint | kills, deaths, hill time, hill captures, untraded-death rate, trade kills |
| 2018 WWII Hardpoint | kills, deaths, hill time, **time per life**, untraded-death rate, trade kills |
| 2019 BO4 Hardpoint | kills, deaths, hill time, hill captures |
| 2019 BO4 Control | kills, deaths, zone captures, first-blood net |
| 2018 WWII Capture the Flag | kills, deaths, captures, returns, flag carry time |
| 2017 IW Uplink | kills, deaths, uplink points |
| 2017 IW Search & Destroy | kills, deaths, first bloods, bomb plays, untraded-death rate, trade kills, thrown deaths |
| 2018 WWII Search & Destroy | the above plus **first deaths and survival** |
| 2019 BO4 Search & Destroy | kills, deaths, first bloods, first deaths, survival, bomb plays |
| 2020–2026 Search & Destroy | kills, deaths, first bloods, first deaths, bomb plays |
| 2020–2026 Hardpoint | kills, deaths, hill time |
| 2021–2025 Control | kills, deaths |

WWII Hardpoint has no hill-capture column and Infinite Warfare tracked no first deaths,
so those cohorts simply do not use them. Black Ops 4 has no kill feed at all, so its
2.1.0 cohorts fall back to exactly the 2.0.0 set rather than being fed zeros — an
absent column means "not recorded", never "none happened". The CDL-era rows are identical
across their seasons and are the shortest sets on the page: no kill feed exists after
2018, the survivals column that BO4 supplied is not carried by the CDL-era source, and
neither are hill captures or Control captures. Control is left with the kills/deaths pair
and nothing else, which is why it is the one cohort with no
[beyond-the-gunfight ratio](#how-much-of-the-weights-is-signal) to report.

**2.2.0 changes that last sentence for most, and not all, of the CDL era.** The columns it
claims land like this, each one still gated on measured availability:

| Cohort | Gains |
|---|---|
| 2017-2018 Hardpoint | hill defends, accuracy, headshots per kill |
| 2017-2018 Search & Destroy, Capture the Flag, Uplink | accuracy, headshots per kill |
| 2019 BO4 Hardpoint / Control / Search & Destroy | damage, headshots per kill (and hill defends in Hardpoint) |
| 2020-2026 Hardpoint | damage |
| 2022-2026 Hardpoint | the above plus non-traded-kill share |
| 2022-2025 Hardpoint | the above plus contested hill time |
| 2020-2026 Search & Destroy | damage per round |
| 2022-2026 Search & Destroy | the above plus non-traded-kill share |
| 2022-2025 Control | damage, non-traded-kill share |
| 2021 BOCW Control | damage, and nothing else |

**Damage counts as part of the gunfight, not beyond it**, and that is a measurement rather
than a convention: the damage differential correlates 0.82 to 0.94 with the kills
differential in every CDL cohort. The entry player who wins a duel's damage without its kill
is a real thing; at the resolution of one number per team per map, this column is a
better-resolved reading of the same gunfight. Counting it as "the rest" would have inflated
the beyond-the-gunfight ratio with a column that is mostly kills. Non-traded-kill share does
not have that problem — it is a share of a player's own kills, and correlates 0.13 to 0.39
with the kills differential in Search & Destroy — so it is the column that gives **2022-2025
Control a beyond-the-gunfight ratio for the first time.**

**2021 Control still has none, and that is a fact about the season rather than a gap.**
Non-traded kills and the Control round counts are declared and empty for Black Ops Cold War,
it records no assists at all, and damage belongs to the slaying pair. There is nothing beyond
the gunfight in that box score to report.

**Contested hill time is the one recovered column that is a new axis rather than a sharper
reading of an old one.** Hardpoint scores a point per second of hill control, so `hill_time`
*is* the scoreboard; occupancy while the hill was contested is strictly less of it. Measured
against each other the two differentials correlate only 0.13 to 0.30, which is much weaker
than the "sharper version of hill time" reading would predict. It is available for 2022-2025
and is declared-but-empty for 2020, 2021 and 2026.

**Two columns are fitted but flagged as not travelling.** Every column is measured on three
things, not one: whether it is available and complete, whether it already knows the winner,
and whether its relationship to the result *holds across titles*. Hill defends and headshots
per kill pass the first two and fail the third. Hill defends points at hill time +0.72 on
Infinite Warfare, +0.44 on WWII and **−0.30 on Black Ops 4**; headshots per kill runs at a
sign-rule accuracy of 0.58 and points one way on WWII and Black Ops 4 and both ways across
Infinite Warfare's three cohorts. A column whose sign against the same quantity flips between
titles is not one quantity measured twice. Both stay in this retrospective decomposition,
where each cohort fits its own weights and a flip between titles costs nothing, and both are
marked as ineligible for anything that has to carry a number across a title seam.

**One column was measured and rejected.** The CDL source records how many Control rounds each
player spent attacking and defending. It is not a player statistic: one team's attack rounds
are the other team's defence rounds on **974 of 974** Control maps, and 343 of those tie
exactly, so the differential records which side each team started on and nothing else. It
already reaches the model where it belongs — as the round denominator derived from it — and
as a feature it would have put the coin toss in the rating.

**What the new columns cost, since a rate needs a denominator on both sides.** Eight of 737
WWII Capture the Flag maps and one of 256 Black Ops 6 Control maps leave the design because
one team recorded no shots, or no kills, so accuracy or a per-kill share cannot be formed for
that side. A half-measured map is not an observation, so it is dropped rather than imputed;
the same nine maps also leave the four-way version comparison, which is why the earlier
versions' comparison numbers move very slightly while their ratings do not move at all.

**And 2.2.0 loses the map backtest, which is worth stating first rather than last.** Over the
9,193 maps all four versions predict, Brier goes 1.0.0 0.05571, 2.0.0 0.04740, 2.1.0 0.04718,
**2.2.0 0.04825** — a small regression, losing in 19 of 25 cohorts. Two things about that,
and neither is "the columns are worthless".

The first is that this is the test the page above spends several paragraphs explaining should
not be read as predictive skill. The map backtest rewards knowing the result, and the columns
that know it best are the ones already in every version: captures pick the winner of a Capture
the Flag map on 100% of maps by their sign alone, hill time on 96%. Almost everything 2.2.0
adds is *less* leaky than what was already there, so a small loss on a leakage-rewarding test
is close to what the page's own
[sign-rule baseline](#what-the-map-backtest-does-not-establish) predicts. The second is mechanical: the ridge strength is a single fixed constant across a
standardized design, and CDL Hardpoint goes from three columns to six on a few hundred maps.
More columns under an unchanged penalty means more shrinkage spread thinner and more
estimation variance, which is the ordinary cost of a wider model on a small cohort and not a
verdict on any column in it.

Where a new column is a genuinely new axis rather than a rival reading of an old one, it wins:
2024 Control improves by 0.0048 and 2023 Control by 0.0029, the two cohorts where non-traded
kill share arrives into a set that previously held nothing but kills and deaths.

This is one reason 2.2.0 is not the published version. The other is that promoting a version
is a decision about what the site's leaderboards mean, and a feature set does not earn that by
existing.

The denominators fork with the era, because map duration is the one column the CDL source
does not carry: Search & Destroy is per round throughout, while Hardpoint and Control are
per 10 minutes in the CWL years and per map in the CDL years. Every feature declares its
denominator as a source, so a rate whose clock a title does not record resolves to its
per-map twin instead of quietly emptying the cohort — which is what it used to do, and is
why the CDL Hardpoint and Control rows are here at all.

**One family is deliberately excluded.** The kill-feed tier can also measure rounds won
while up a man, and clutch wins. Neither is used as a rating feature, because both
contain the round outcome, and round wins are what decide maps — regressing map wins on
them would be close to circular and would flatter the backtest without the model having
learned anything. Thrown deaths qualify because they are counted from alive-counts
alone; the code computes them with an empty round-winner map so that outcome
information cannot reach the feature even by accident.

That exclusion is right, and it is also incomplete. The same test applied to the
objective columns condemns them faster: Capture the Flag is won by scoring the most captures, and
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
9,202 maps every version predicted enter the table.

This used to be a CWL-era comparison, and was described here as one that would stay that
way: 1.0.0's features are per-10-minute by definition, map duration stops in 2019, so the
baseline could not reach the CDL era and dragged the common set back to 2017-2019 with it.
That was the undeclared-denominator defect rather than a fact about 1.0.0 — a rate resolves
to its per-map twin where there is no clock — and with it fixed all three versions predict
the same 9,202 maps across both eras. The version verdict below is measured on the whole
record.

| Version | Brier | Log loss | Accuracy |
|---|---|---|---|
| 1.0.0 (box score) | 0.0557 | 0.1869 | 92.5% |
| 2.0.0 (intangibles) | 0.0474 | 0.1621 | 93.7% |
| **2.1.0 (+ kill feed)** | **0.0472** | **0.1613** | 93.7% |

Brier falls 15% against the box-score baseline for 2.0.0, and 15% for the published 2.1.0.
Both margins are smaller than the 22-23% this table reported over the CWL years alone, and
the reason is visible in the per-cohort rows below: the kill feed does not exist after
2018, and in the CDL era the two later versions have little to add to the box score beyond
what Search and Destroy's round-scale features supply. The kill-feed layer on top is a
much smaller gain again, and a fair reading is that it is close to a wash overall — it is
published as the default because it wins on both proper scoring rules, not because the
margin is decisive.

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
| 2020–2026 Hardpoint | 2,313 | 0.0420 | **0.0411** | **0.0411** |
| 2020–2026 Search & Destroy | 1,750 | 0.0641 | **0.0508** | **0.0508** |
| 2021–2025 Control | 968 | 0.0802 | **0.0794** | **0.0794** |

The CDL rows are given as ranges because every season in them tells the same story to
three decimal places; the artifact carries them one season at a time.

Four things to read out of it. Capture the Flag improves enormously, and this is
the row that should be read most sceptically: per-map captures is the CTF score, where
captures *per ten minutes* was that score divided by map length. So 2.0.0 did not
discover anything about Capture the Flag — it stopped dividing the win condition by a
nuisance variable. That is a units fix on a leaked column, and because CTF is 667 of the
9,202 maps it carries a visible share of the headline above. The kill feed helps in
exactly one place — WWII Search & Destroy, where trades decide rounds — and slightly
*hurts* WWII Hardpoint. Control is the cohort where the box-score model is barely beaten
in either era: in 2019 with only first-blood net and captures available 2.0.0 has nothing
to add, and after 2020 the two later versions are *identical*, because with no captures
column in the source there is nothing for a later version to be made of. And the CDL
Hardpoint gain is real but small — 0.0420 to 0.0411 — where the CWL-era gain came from
columns the modern source does not carry. A version that wins overall while barely moving a cohort is
the normal shape of this kind of result, and reporting it is cheaper than defending an
average.

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
| 2020 MW Search & Destroy | 186 | 92.5% | **93.3%** kills per round | −0.8 pt |
| 2021 BOCW Search & Destroy | 247 | 92.7% | **93.7%** kills per round | −1.0 pt |
| 2022 VG Search & Destroy | 219 | 93.2% | **93.3%** plants + defuses per round | −0.1 pt |
| 2023 MWII Search & Destroy | 261 | 93.9% | **94.8%** kills per round | −0.9 pt |
| 2024 MWIII Search & Destroy | 282 | 94.0% | 92.3% deaths per round | +1.6 pt |
| 2025 BO6 Search & Destroy | 267 | 90.3% | 89.3% plants + defuses per round | +0.9 pt |
| 2026 BO7 Search & Destroy | 288 | 95.1% | 94.6% deaths per round | +0.5 pt |

The Capture the Flag row is the cleanest statement of the problem: on every map where the
two teams did not tie on the column, the sign of the capture differential was **never
once wrong**, because outscoring the opponent in captures is the definition of winning
that mode. The fitted model, with five features and a ridge penalty splitting weight
between collinear ones, does *worse* than the identity buried inside it. WWII Hardpoint
is the same story one step weaker — hill occupancy is the Hardpoint score, up to
teammates standing on the hill at once — and it also beats the model outright.

The CDL rows make the general version of that point, and they make it worse for the
model. In four of the seven seasons a single column, unweighted and unfitted, picks map
winners more often than the whole fitted model does; across all seven the model's gain
ranges from −1.0 to +1.6 points and averages roughly zero. Two hundred-odd maps of five
collinear per-round features is simply not enough for a ridge to improve on "whoever got
more kills won", and the model does not pretend otherwise on this test.

Elsewhere the whole model adds between 0.9 and 2.1 points over its single best
column, and even a plain kill differential picks the winner on 86–93% of maps. Nothing
here starts anywhere near a coin flip. Several rows tie: in WWII Search & Destroy
kills, deaths and survivals per round all score 92.8%, and in most CDL seasons kills and
deaths per round tie exactly, because in a mode with a fixed four players a side those
columns are near-restatements of each other. The listed column is the first of the tie,
not a claim to be the only one.

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
541 transitions across nine season boundaries, from 96 (IW → WWII) down to 39 (BOCW → VG),
Pearson *r* with a 2,000-draw bootstrap over players.

| Predictor (season *N*) | → next rating | → next K/D z |
|---|---|---|
| Composite rating | 0.32 [0.24, 0.41] | 0.30 [0.22, 0.38] |
| Era-adjusted K/D z | 0.31 [0.23, 0.38] | **0.56** [0.50, 0.63] |

The contrasts are paired — the same resampled players scoring both predictors, because
comparing two intervals that happen to overlap answers nothing. Predicting next season's
K/D, Δ*r* = −0.26 [−0.34, −0.18], which excludes zero: **K/D z predicts a player's future
K/D better than the composite rating built on top of it, decisively.** That is the same
verdict this test returned on the CWL archive, at the same effect size, on two and a half
times the sample.

The other column has moved, and the earlier version of this page overstated it. On 216
CWL-era transitions the rating also lost at predicting *its own next value*, by Δ*r* =
−0.08 with an interval spanning zero, and the summary here read "raw K/D z is the better
predictor in both columns". On 541 transitions the sign reverses: Δ*r* = +0.02
[−0.07, +0.10], still spanning zero. The correct statement is the one that was true of
both samples — the two predictors are indistinguishable at forecasting next season's
rating — and "better in both columns" was reading a point estimate inside its own
interval. Only the K/D column separates, and it separates in the direction that is bad
news for the rating.

**Does a roster predict future map wins?** Walk-forward by event within each season: at
every event the whole rating pipeline is refit on maps from earlier events only, each
team's players are averaged into a roster strength for that map's mode, and the
differential becomes a win probability through a logistic also fit on those earlier maps.
Nothing from the event being scored enters. 9,030 maps survive on which every predictor
has an opinion; 1,796 are skipped for having no history and 761 for having no identifiable
roster.

| Predictor | Brier | Log loss | Accuracy | vs. coin flip |
|---|---|---|---|---|
| **RAPM, rating-centered** | **0.24517** | 0.6932 | 59.1% [58.1, 60.2] | **−0.0048** [−0.0086, −0.0009] |
| RAPM | 0.24592 | 0.6943 | 59.0% [58.0, 60.1] | **−0.0041** [−0.0079, −0.0001] |
| Roster composite rating | 0.24701 | 0.6889 | 56.1% [55.1, 57.1] | **−0.0030** [−0.0055, −0.0004] |
| Same rating, z-and-shrink | 0.24742 | 0.6899 | 56.2% [55.2, 57.2] | −0.0026 [−0.0051, 0.0000] |
| Glicko-2 team rating | 0.25085 | 0.7103 | **59.6%** [58.6, 60.6] | +0.0009 [−0.0033, +0.0053] |
| Roster K/D | 0.25148 | 0.7024 | 56.6% [55.6, 57.6] | +0.0015 [−0.0015, +0.0044] |
| Coin flip at 0.5 | 0.25000 | 0.6931 | — | — |

**Three of these now beat the coin flip on an interval that excludes zero, and on the CWL
archive alone none did.** That is the most substantive change on this page. The rating's
gap against always guessing 0.5 is −0.0030 [−0.0055, −0.0004], where on 3,760 CWL maps it
was −0.0019 with an interval spanning zero and the correct reading was "indistinguishable
from no model at all". It is no longer that.

The qualifier survives, and it is the same one as everywhere else here: none of the three
clears its own 80%-power threshold. The rating's −0.0030 sits under the 0.0037 that 9,030
maps can resolve, RAPM's −0.0041 under 0.0054, and the blend's −0.0048 under 0.0053. Every
one of them is a gap the interval catches and the power criterion says would usually be
missed, which is the shape of an effect that is real and small. "Roster strength forecasts
map wins slightly better than a coin flip" is what this table now supports. "By enough to
be worth acting on" is not.

One contrast does clear both tests, and it is the one that most directly answers what the
composite rating is for: **against roster K/D, the rating wins by −0.0045
[−0.0067, −0.0021], against a threshold of 0.0035.** On the CWL archive that contrast was
−0.0034 [−0.0072, +0.0003] and unresolved. The rating built on top of the box score does
forecast map wins better than the box score's own headline number — which is notable
precisely because [the persistence test above](#two-tests-the-rating-can-fail) says the
opposite about forecasting a *player*.

Against Glicko-2 the rating is −0.0039 [−0.0078, 0.0000], with the upper bound landing
exactly on zero; treat that as unresolved.

The fourth row is the same rating estimated the old way, and it is here because
[the rating is a posterior](#the-rating-is-a-posterior) needed a test rather than an
argument. Paired on identical maps the posterior wins by −0.00041 [−0.00090, +0.00001],
which now spans zero where on the CWL archive it did not, and the pick rates are a
coin-flip apart. Read as "the better-specified estimator does not cost anything out of
sample", which is the most this test could have established either way — and is now the
most it does.

The blend is the best row in the table, which reverses its earlier verdict; that is
discussed under [plus-minus](#plus-minus-value-in-wins-without-the-box-score) below.

Brier and accuracy still disagree, and reporting either alone would mislead, so both are
published. Every predictor picks the winner more often than chance — the rating's 56.1%
interval clears 50% comfortably — so roster strength carries directional signal. What it
carries much less of is a usable probability: the fitted logistic finds so little to work
with that its output barely leaves 0.5, which is what a Brier just under the floor with an
above-chance pick rate means. Glicko-2 is the most accurate and has the *worst* log loss,
the signature of a confident model that is over-confident, and it is the only predictor
here whose Brier is worse than guessing.

### Plus-minus: value in wins, without the box score

Every other player number here starts from the scoreboard, which is why the leakage section
exists and why the persistence test bites. RAPM asks the question from the other end: forget
what a player did, and look only at whether their side won and who else was on the server.
One row per map, one column per player, +1 for one side and −1 for the other, ridge-
regressed on the map result. A coefficient is a player's estimated contribution to the
log-odds of winning a map, holding the other seven constant. No box-score column enters at
any point, which is what makes it an independent check rather than another view of the same
data. 11,575 decided maps, 265 players with at least 20 of them.

Two things have to be reported rather than assumed away, and together they decide how much
the leaderboard means. Both have improved substantially with the CDL era, for a reason
worth naming: franchised rosters change more often than CWL ones did, and roster churn is
exactly what breaks the collinearity this method suffers from.

**Collinearity, and it is still severe, but no longer disabling.** Four players who never
appear apart are one column wearing four names; ridge responds by splitting the credit
evenly, which is correct and is also indistinguishable from a finding. So every coefficient
is published beside that player's *teammate concentration* — the share of their maps spent
alongside their most frequent teammate. The median is **0.68**, down from 0.81 on the CWL
archive alone, and **90 of 265 players sit at 0.9 or above** — a third, where it was 44%.
None of the top five coefficients now belongs to a player at concentration 1.00; two of
the five clear 1.96 standard errors with a concentration below 0.9.

**Shrinkage.** Standard errors come from the penalized Hessian and are published with every
coefficient. The median is 0.31 against a coefficient spread of 0.42, and **53 of 265
coefficients exceed 1.96 standard errors** — where on the CWL archive it was 7 of 196, with
a median standard error larger than the whole spread of the estimates. The ridge path still
says the penalty is doing real work: as it rises from 0.25 to 64 the spread of coefficients
collapses from 0.57 to 0.10 and the ordering's correlation with the lightest fit falls to
0.56. Nothing here tunes that penalty against the held-out maps — that would turn the
forecast above into a selection statistic rather than a test.

**The blend, and its verdict has reversed.** A natural extension is to use the box-score
rating as an informative prior on RAPM, which is a one-line change to what the penalty
shrinks toward: instead of zero, each player's coefficient is pulled toward their composite
rating converted into map-win logits, at an exchange rate estimated on the training maps
rather than assumed. The blended coefficients correlate 0.993 with plain RAPM. On the CWL
archive the blend was *worse* on Brier (0.24601 against 0.24467) and better on accuracy,
and this page reported a mixed result and declined to adopt it. On the full record it is
better on both: Brier 0.24517 against 0.24592, accuracy 59.1% against 59.0%. It is the best
Brier in the forecast table.

That is still not enough to adopt it. The two arms are 0.0008 of Brier apart on predictors
that correlate at 0.993, which is well inside what this sample can resolve, and a
0.1-point accuracy difference is nothing. The supportable statement is that the blend is no
longer measurably worse — not that it is better. The published RAPM stays the plain fit,
because "shrink toward the box score" is the assumption this whole section exists to avoid
making, and nothing in these numbers forces it.

**What RAPM is actually measuring.** At a median teammate concentration of 0.70, a player's
coefficient is still substantially their lineup's. That explains the shape of
the table above: RAPM's accuracy (59.0%) lands much closer to Glicko-2's team rating (59.6%)
than to the box-score rating's (56.1%), and it does so while never being told which team is
playing. It behaves like a team rating expressed one player at a time. That makes it the
best available answer to "does player-level information forecast map wins" and simultaneously
a warning against reading its leaderboard as a ranking of individuals.

The `rapm` artifact is stored with the published rating run, and both variants are scored by
the same walk-forward harness as the rating so the comparison happens on identical maps.

**What this means for the rating, and what has changed.** A single map in this league is
still close to a coin flip, and knowing which four players are on the server changes it by
about three parts in a thousand of Brier. The composite rating remains primarily a
*descriptive* measure: it summarizes what a player did, weighted by what actually
correlated with winning maps in that season and mode, and the leakage section above says
plainly why its map backtest scores as high as it does.

What no longer holds is the flat statement that it forecasts nothing. On the CWL archive
every predictor's gap over the coin flip spanned zero and the correct summary was "not a
forecasting tool". On the full record the rating, RAPM and the blend all beat the coin flip
on intervals that exclude zero, and the rating beats roster K/D by a margin clearing both
its interval and its power threshold. The site still does not present the composite rating
as a forecasting tool, and should not: the effects are a few thousandths of Brier, none of
the coin-flip gaps clears its own power threshold, and a measure that predicts a player's
own next season worse than raw K/D does has not earned that framing. But "indistinguishable
from no model at all" was the 2017-2019 result and is no longer this one.

Two directions were named here as ways to change the verdict, and both have now been
tried. The round-level model in Tier 1d works as a model of a round, but
the player value derived from it — win probability added per kill — turns out to be kill
rate in another unit, and the part that is not kill rate does not reproduce across a
player's own games. Plus-minus does better: RAPM posts the second-best Brier in the table
and a clearly above-chance pick rate without touching the box score, and its gap over the
coin flip now excludes zero, though it still does not clear what 9,030 maps can resolve.
Its coefficients remain entangled with lineups, if less so than before.

Player-level information does appear to forecast map wins slightly better than the
composite rating does, and RAPM against the rating directly is +0.0011 [−0.0025, +0.0046] —
better on the point estimate, unresolved on the interval, as it was. Neither result
promotes anything into the published rating.

The artifacts (`rating_persistence`, `roster_forecast`, `rapm`) are stored with the
published rating run and recomputed on every rerun.

The `rapm` artifact publishes the forty highest and forty lowest coefficients, which is
the right shape for reading the distribution and the wrong shape for reading a player: it
names 80 of the 265 players the model fits, so the other 185 could not be looked up at
all. `player_rapm` (migration 0013) stores the whole fit, one row per player per rating
run, and the player page reads that.

Publishing a per-player coefficient raises the obvious hazard, so the table is explicit
about it. **53 of the 265 coefficients exceed 1.96 SE, and 44 of those also sit below 0.9
teammate concentration** — so for the first time the method resolves a meaningful number
of individuals rather than a handful of duos. On the CWL archive alone that count was one.
The median standard error is 0.31 against a coefficient spread of 0.42, so for the other
212 players the ridge penalty is still comparable to or larger than the signal, and a
coefficient that does not clear its own error is not a ranking position.

So the player page draws the interval as the chart and prints the coefficient as a label
on it, states in words whether the interval covers zero, and adds a second notice when
concentration is at or above 0.9. Nothing renders the coefficient without its standard
error.

### Can the plus-minus have a time axis?

The coefficient above is one number for a whole career, which cannot express a peak, a
decline, or a player who changed. The obvious repair is one coefficient per player per
season. Whether this record supports that is a question with an answer, and it is answered
here before any such model is fitted rather than after — the `rapm_identification`,
`rapm_recovery` and `rapm_preflight_verdict` artifacts, stored under their own
`rapm_preflight` run.

**A season identifies lineups, not players.** Every row of the design is one lineup against
another, so the row space is spanned by lineup *differences*. That puts a hard ceiling on
what a season can identify: **distinct lineups minus the number of separate pools they fall
into**, no matter how many players those lineups contain. Measured on the 11,575 admitted
maps, with a column per (player, season) and a team-season column on each side:

| | Columns | Rank | What the schedule allowed |
|---|---|---|---|
| Season-expanded design | 1,188 | 427 | 428 |
| Career design (what ships today) | 340 | 270 | — |

The rank is not merely below the column count, it is **exactly what the schedule permits and
not one direction more**: 438 distinct lineups in ten seasons, all but one connected, so 428
directions are available and 427 are realized. 2021 is typical — 63 player columns and 12
team columns, 38 lineups, rank 37. The 761 missing directions are not weakly identified.
They are not identified at all, and any number a penalty puts there is the penalty's.

**Which is visible per column, too.** For each column, the share of its posterior variance
supplied by the penalty rather than by the data is λ·[(XᵀX + λI)⁻¹]ⱼⱼ. It cannot reach 1: a
player whose four-man lineup never changes shares one identified direction with three
teammates and a team column, so their share sits near k/(k+1) — 0.80 at 4v4, 0.83 at 5v5.
Against that reference, **42% of CDL player-season columns and 53% of CWL ones are penalty
dominated**, where the career design's figure is 9%. A flat threshold of 0.9 would have
reported no problem anywhere, which is why the reference is derived rather than picked.

**And a simulation says what that costs.** A generated league — known trajectories with
peaks, an explicit team-season effect, roster churn as a dial, mode margins censored at the
real caps of 250, 3 and 6, and map noise solved so the league is exactly as predictable as
this one — is handed to the estimator the phase would use. Recovery is scored on deviations
from the team-season mean, because separating *teammates* is the whole claim:

| Effective lineups per team-season | Teammate recovery *r* |
|---|---|
| 1.0 (nothing changes) | **−0.06** |
| 1.3 | 0.25 |
| 1.8 | 0.29 |
| 2.5 | 0.30 |
| 3.4 | 0.33 |

The first row is the negative control and it is the important one: where a lineup never
changes and no player transfers, the estimator recovers **nothing** about who inside it was
worth more. Turn transfers back on, change nothing else, and the same frozen season recovers
*r* = 0.50 — every point of it imported from other seasons by the random-walk penalty.
That is the failure mode this pre-flight exists to price, measured rather than argued.

The last row matters too, and it is not good news. **More roster movement than any real
team-season has still tops out near 0.33**, so the binding constraint is not only churn: a
map in this league is close to a coin flip, and the noise the calibration matches — the map
model picks 59.5% of maps — leaves little room to resolve one player inside a lineup however
often the lineup changes.

Two further numbers come from the same harness. The penalized-Hessian intervals cover the
truth at **91–98%** against a nominal 95%, so they are near enough nominal to use. And a
two-sided penalty scored on a forward test beats the one-sided fit by **+0.026** of
correlation while predicting a season it has already seen — small, real, and exactly the
contamination that makes the smoothed and filtered families a distinction rather than a
preference.

**The verdict, against thresholds declared before the measurement.** The rule stops a
season-varying plus-minus only if all three hold: the median team-season carries under 1.5
effective lineups, the design identifies under half its player columns, and the simulation
fails to separate teammates at that lineup variety. The two eras do not answer alike:

| | Effective lineups | Rank / player columns | Simulated recovery | |
|---|---|---|---|---|
| CDL 2020–2026 | 1.94 | 48% | 0.29 | two clauses — does not stop |
| CWL 2017–2019 | 1.00 | 41% | −0.06 | all three — stops |

So the time axis is as fine as each era can carry: **season resolution for the CDL era,
published as deviations from an explicit team-season effect, and pooled to era level for
2017–2019**, where within-season lineup variety identifies nothing. A rank-deficient design
that still recovers what was put into it is not a failure — that is what a penalty is for —
which is why the rule is a conjunction and why the CDL era, deficient on rank alone, ships.
But 0.29 is weak recovery, not good recovery, and the CDL season coefficients this permits
should be read as what they are: a noisy deviation from a team, not a ranking of four
players.

**And the forward test this feeds has less power than the plan assumes.** The record holds
**561 consecutive player-seasons**, against which the smallest detectable gap in next-season
persistence — over a baseline *r* of 0.564 — is **0.08**. That is a floor: it assumes
independent observations, and clustering the resample widens it. A gate declared below 0.08
cannot be met by a model that works.

Nothing in *this* section fits a season-varying model to real maps: everything above runs
on generated data. What the verdict permitted is fitted in the section that follows, at the
resolution it permitted per era, and the career plus-minus above is unchanged and still the
published one.

### Season plus-minus: the time axis the record turned out to carry

The pre-flight above ends in a fork rather than a yes, and both branches ship: **one
coefficient per player per season for the CDL era, pooled to one per player per era for
2017–2019**, all of them as deviations from an explicit team-season effect. The model reads
that resolution out of the verdict artifact rather than declaring it, so an era that grows
into season resolution gets it without a code change. The `rapm_season` artifact and the
`player_rapm` rows it summarizes are stored under their own run.

**The model.** One row per map, +1 for the four players on one side and −1 for the other,
expanded to one column per (player, time cell), plus a team-season column on each side.
Fitted by generalized ridge:

```
minimize  ‖y − Xβ‖²  +  λ₀ Σ β²_{p,t}  +  λ_w Σ (β_{p,t} − β_{p,t−1})²
```

The second term is a Gaussian random walk on player value — the state-space formulation
written as a penalty. Both λ are chosen by **generalized cross-validation** with the
hat-matrix trace computed exactly, never by searching against held-out maps, which would
turn the backtest into a selection statistic. On this record that lands at **λ₀ = 32.5,
λ_w = 7.4**, spending **255 effective degrees of freedom** on 938 columns: 711 player cells,
223 team-seasons and 4 replacement buckets.

**The response is the map's score margin, rank-transformed to normal scores within (season,
mode).** Margin carries far more per row than a binary win, and it is censored rather than
merely heteroskedastic — Hardpoint runs to 250, Control to 3, Search & Destroy to 6 — so the
raw number is non-linear in value at exactly the tail where the best players live, and ranks
survive the cap. 11,571 of the 11,575 admitted maps carry it; the other four have a margin
that is absent, zero, or contradicts the recorded winner, and they are **named by game id in
the artifact** rather than counted and forgotten. Two of the four are 2017 Uplink maps level
at regulation with a winner the archive knows, which is not an error; the other two are — a
Search & Destroy map recorded at 3-6 to the team that won it, and a Control map whose scores
are both −1, a sentinel for "unknown" wearing the type of a real number. Neither is fixable
from inside this repository, so both are declared here, reported by the ingest quality
checks, and excluded from the signed targets while staying in the binary one. Binary win and mode-standardized margin are fitted from the same factorization and
published as sensitivity, never averaged in: their coefficient orderings correlate **0.913**
and **0.982** with the published one.

**Three population rules, not two.** *Column admission*: below 8 maps in a cell a player
does not get a column and the map is not dropped — the slot joins a shared replacement
bucket for that cell, because dropping the map would discard a real result and bias the fit
toward teams whose opponents happened to be established. **21 players** are pooled this way,
into 4 buckets, and the buckets' own coefficients are the directly interesting number: what
a replacement-level slot is worth. They run from **−0.02** (2021, 2022) to **−0.24** (2026,
SE 0.08). *Fit inclusion*: every admitted cell enters however thin, which is what the walk
penalty is for. *Publication*: a higher floor of 20 maps, which leaves **1,010 published
player-cells** in 2,020 rows over two scopes.

**Two coefficient families, and only one may be read forward.** The walk penalty is
two-sided — β at 2023 is pulled toward 2024 as much as toward 2022 — so a `smoothed`
coefficient has already seen the season it would be asked to predict. That contamination
arrives through the penalty rather than through a column, so nothing written against the
design matrix can detect it. `filtered` refits through each cell and nothing later, and a
forward test reading a smoothed row raises rather than warning. The two families correlate
**0.996**, which is the honest way to read the split: the leak is small, real, and exactly
the size the simulation priced it at (+0.026 of forward correlation).

**What a season coefficient is worth, measured three ways.**

| | |
|---|---|
| Split-half reliability within a cell, whole series to a side | *r* = **0.50** [0.43, 0.56], Spearman-Brown 0.67 |
| Median share of a column the penalty supplied | **0.77**, against the 0.80 a never-changing 4v4 lineup cannot go below |
| Median teammate concentration inside a cell | **1.00** |

The third number is the one that governs how any of this may be presented. Inside a single
season the median published player **never appears without their most frequent teammate**,
so their coefficient and that teammate's are one direction wearing two names. The
reliability figure is directly comparable to the persistence *r* the composite rating
reports, and it is a genuine result — half the record predicts the other half at 0.50 — but
it is reliability of a *lineup-entangled* quantity, and 53% of player cells sit at or above
0.95 of the penalty-dominance reference. Per cell the reliability runs from **0.30** (2021)
to **0.69** (2022); the CWL era, pooled over three years, reaches 0.49.

**What the schedule allowed each cell, beside the scalar.** Concentration says how entangled
one player is; the lineup graph says whether the cell could have separated anybody at all.

| Cell | Maps | Player columns | Distinct lineups | Rank ceiling |
|---|---|---|---|---|
| CWL era (2017–2019) | 5,087 | 254 | 202 | 200 |
| 2020 CDL | 718 | 76 | 32 | 31 |
| 2021 CDL | 858 | 62 | 38 | 37 |
| 2022 CDL | 830 | 58 | 30 | 29 |
| 2023 CDL | 994 | 63 | 30 | 29 |
| 2024 CDL | 1,014 | 65 | 31 | 30 |
| 2025 CDL | 998 | 62 | 30 | 29 |
| 2026 CDL | 1,076 | 71 | 36 | 35 |

Every cell holds roughly twice as many player columns as its schedule can identify
directions. That is not a defect to be fixed by a better solver — it is the league's
schedule, and it is why the penalty exists and why these numbers are published as noisy
deviations from a team rather than as a ranking of four players.

**The penalties, reported rather than selected.** Moving λ₀ over 4× either side of the
chosen value takes the coefficient spread from 0.106 to 0.043 and the ordering's correlation
with the published fit down to 0.94–0.96, while GCV moves in the fourth decimal (0.8504 to
0.8548) — the criterion is nearly flat and the ridge dial is doing most of the shrinking.
The λ_w = 0 row, which is the fit with no time-borrowing at all, correlates **0.988** with
the published one: at this record's churn, the walk term is a modest smoother rather than
the thing holding the estimates up.

**Against the career fit that ships today.** An earlier version of this gate asked for a
reproduction of the published logistic fit, and that was the wrong thing to ask: the λ_w → 0
limit of a Gaussian fit on transformed margin is not a logistic fit on binary win, and no
amount of care makes it one. So the comparison is a rank correlation with the two changes
separated, over the 265 players both fits reach:

| This estimator, collapsed to one cell | Rank correlation with the published career fit |
|---|---|
| Gaussian on margin, with the team-season effect | 0.779 |
| Gaussian on margin, without it | 0.842 |
| Logistic on map win, with the team-season effect — the like-for-like arm | 0.784 |

Read the middle row against the other two: **the team-season effect moves the ordering more
than the link function does**. That is the change of interpretation stated plainly. Without
a team column, team quality has nowhere to go but into the four player columns and ridge
divides it evenly — "this was a good team" is published as "these were four good players".
With it, a player's number is what is left after their team-season is accounted for, and the
players whose published rank it moves most are the ones whose old coefficient was mostly
their roster's.

So: this is a time axis the record supports at the resolution measured for it, reliable at
*r* = 0.50 within a cell, and entangled with lineups to the point where the median player is
inseparable from a teammate. It is published with its standard error and its penalty share
on every row, and it is not a ranking of the players on a roster.

## Tier 2b: Series dynamics (shipped)

A Call of Duty series is a race to three maps, and much of what gets said about one is a
claim about the race itself: a 1-0 lead is worth more than arithmetic, teams ride momentum
through a series, a reverse sweep is a collapse rather than a coin landing the same way
twice. This section measures all three.

**One window now, where there used to be two.** The enumerated benchmarks below cover the
best-of-five series whose maps reconstruct their scoreline exactly. The enumeration needs
the title's declared mode rotation, and [as with `map_elo`](#map-elo) only the three CWL
titles declared one until recently — so 1,587 of the 2,859 loaded series produced no
benchmark at all, and this section's two halves were measured on different eras. All ten
titles declare a rotation now, and none of the loaded series is skipped for want of one.
What is still excluded is excluded for its shape and counted: 12 best-of-one, 23
best-of-three, 17 best-of-seven and 12 best-of-nine, 11 with a map that has no recorded
winner, 11 whose maps do not reconstruct the scoreline, and 5 with a gap in their map
ordinals. The *direct* momentum test that follows needs no rotation and runs on all 11,250
maps in those 2,859 series, so both halves of this section now cover 2017-2026.

**The null is conditional independence, enumerated rather than simulated.** Each series'
two teams have a map-level Elo — the blend arm from the section above — frozen *before its
first map*. The league's mode rotation says which five maps they would play. That gives
five independent per-map win probabilities and an exact enumeration of the race: every
scoreline it could have reached, with its probability. No memory of any kind is in that
calculation, so the difference between it and what happened is where a series dynamic would
have to live.

**Why the raw number is not the finding.** The map-1 winner takes 74.3% of these series,
and most of that is not a dynamic at all. Between two identical teams a 1-0 lead in a race
to three is already worth 68.8%, by arithmetic. Between *these* teams at their frozen
ratings it is worth 70.9%. And the ratings themselves are modest: their map-1 calibration
slope is 1.06, meaning true strength gaps are slightly wider than the ratings say — a check
run on map 1 because every series plays it, so unlike maps 4 and 5 that sample is not
conditioned on a result.

That last point is the whole difficulty. A team that wins map 1 is, on the evidence of
having won it, better than its rating said, so it wins map 2 more often than a rating-based
calculation predicts, with nothing carrying over between the maps. Every quantity here has
that problem. So each rate is stated against a second benchmark: the same enumeration at
the strength gap that best explains these results with *no* carryover, fitted below.

| | Observed | Coin flip | At the ratings | Allowing for quality |
|---|---|---|---|---|
| **Map-1 winner takes the series** | **74.3%** | 68.8% | 70.9% — **+3.4** [+1.8, +5.0] | 73.4% — +0.9 [−0.7, +2.5] |
| **Sweep (3-0)** | **35.1%** | 25.0% | 28.5% — **+6.5** [+4.9, +8.3] | 34.1% — +1.0 [−0.7, +2.7] |
| **Goes the distance (3-2)** | **28.5%** | 37.5% | 33.9% — **−5.4** [−7.0, −3.7] | 29.5% — −1.0 [−2.6, +0.7] |
| **Reverse sweep (0-2 down, won)** | **5.1%** | 6.3% | 5.6% — −0.5 [−1.3, +0.4] | 4.9% — +0.2 [−0.6, +1.1] |

Gaps in percentage points with 95% intervals, resampled over series; the pairing matters,
since both columns are computed on the same series. Bolded gaps exclude zero. Against the
ratings alone, every headline signature of momentum is there: too many sweeps, too few
deciders, a 1-0 lead worth three points more than it should be. **Against a strength gap
wide enough to explain the same series with no memory at all, all four vanish.**

That is a cleaner verdict than this table gave a moment ago, and the reason is worth
recording. On the CWL-only window the map-1 row read +2.7 points against the quality
benchmark with an interval excluding zero, and it had to be argued down: 2.7 was below what
that sample could resolve at 80% power, and the direct test disagreed with it. Doubling the
window to both eras — the same 2,859 series everything else in this section already
covered — puts that residual at +0.9 [−0.7, +2.5]. The earlier reading, that the quality
benchmark is a fitted approximation and a residual of that size is not evidence of
momentum, survives; what changed is that it no longer has to be argued, and the argument is
kept here because a result that needed defending and then stopped needing it is worth more
than one that was always comfortable.

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

This test never needed a mode rotation, and now neither does the table above; both cover
the whole record.
The fit, over 11,250 maps in 2,859 series: **sigma = 0.66 logits** of team quality the
ratings did not have — about 32 points of map win probability between a team one standard
deviation above the rating's estimate and one a deviation below — and **gamma = −0.003
[−0.065, +0.058]**, in points of map win probability between a team that just won a map and
one that just lost, **−0.2 pt [−3.2, +2.9]**, likelihood-ratio *p* = 0.92. Fitted on maps
1-3 only, the one panel with no stopping rule at all because every best-of-five plays all
three, it is −3.0 pt [−8.0, +2.0]: consistent, wider, and still pointing the wrong way for
a momentum story.

For contrast, the same data regressed the ordinary way — map 2 on the frozen strength logit
and the map-1 result, with no series offset — puts winning map 1 at **+8.6 pt**, *p* <
0.0001, over all 2,859 series. The regression is reported in the artifact next to the null
it produces, because the gap between +8.6 and −0.2 is the finding: the effect is entirely
the two teams being further apart than the rating knew.

**What this record could have found.** 2,859 series can resolve a carryover effect worth
4.4 points of map win probability at 80% power, down from the 6.7 points 1,272 series could
resolve. So the null is tighter than it was: momentum inside a series is worth less than
4.4 points of map win probability. It is still not a claim that carryover is exactly zero.
The rest of the
site's momentum question, at series level across an event, is in
[Does it actually predict better?](#does-it-actually-predict-better) — and note that the
two now point different ways: within a series, adjacency adds nothing; across an event,
recent form carries a small measurable edge. Those are compatible, and they are different
questions.

The model is `series_dynamics` v1.0.0; artifacts `series_dynamics` and `series_momentum`.

## Tier 2c: Player style (shipped)

Every roster in this sport is described in nouns — anchor, entry, flex, objective player —
and a signing is explained by the role it fills. Those nouns may well be true of how teams
play. This section asks the narrower question the box scores can answer: do they fall into
groups, or into a cloud?

**The null is that there are no groups.** k-means returns k clusters for any k, on any
data, including data with no structure in it at all, so a partition is never by itself
evidence of one. What is published here is the comparison between the partition this
archive gives up and the partition *the same cloud with no groups in it* gives up.

**Quality is removed before the question is asked.** Cluster raw box scores and the
leading axis is "more kills, better ratio, larger share" with every metric loading the
same way — that is a rating, and the site already publishes one, so the "archetypes" would
come out as tiers. Every feature is therefore residualised against the published composite
rating, and what is clustered is the remainder: how a player played at their level, not
what level that was. The rating explains 12.6% of the variance in the CWL features and
6.4% of the CDL ones, so the two are very nearly orthogonal and almost nothing is lost by
insisting on the distinction.

**The era is removed too, and this costs more than it sounds.** Metric coverage is not flat
across the record: the kill feed exists for two titles of ten, Hardpoint qualification
varies by a factor approaching two between titles, and everything denominated in map time
stops in 2019. Take every metric present in all ten seasons and demand a complete row and
*no player-season qualifies* — the richest-looking feature set describes nobody. The rows
surviving a looser cut are not a random sample either; they skew to the better-covered
seasons and the higher-volume players, so a cluster fitted on them can be an era wearing a
costume. A column is admitted only if it is attainable in every season of the era being
fitted.

**Which is why this is fitted twice, once per league.** A basis common to both eras would
be nearly empty, so the two are fitted separately and published separately:

| Basis | Era | Columns | Player-seasons | Worst season retained |
|---|---|---|---|---|
| core CWL | 2017–2019 | 26 | 484 of 487 | 99.0% |
| core CDL | 2020–2026 | 7 | 457 of 457 | 100% |
| extended CWL | 2017–2019 | 63 | 336 | 40.6% |
| extended CDL | 2020–2026 | 33 | 428 | 83.1% |

The two core bases are the published ones. They are not comparable to each other and are
not compared: 26 columns of streaks, multikills, headshots and pace against 7 columns of
kills, deaths, damage, engagements and share is a different question asked twice, not one
question asked of two eras. The CDL basis is thin because most of what the CWL archive
measured — streak depth, headshot rate, accuracy, suicides, per-10-minute anything — is
simply not in the CDL-era source.

**There is no taxonomy, in either era.** On both published bases the gap statistic prefers
a single cluster to every partition it tries.

On the CWL basis the best silhouette any k reaches is 0.255, at k=2, and a single Gaussian
with the same covariance and sample size scores 0.237 to 0.281 on the same test: the
separation observed is what no separation looks like. Bootstrap cluster stability at k=2 is
high — Jaccard 0.947 and 0.951 — and on its own means nothing, which is the trap this
section exists to avoid: bisecting an elongated cloud along its long axis is enormously
reproducible, and the Gaussian null reproduces itself just as well, at 0.874 to 0.971.
Every k from three up fails every test.

On the CDL basis the same thing happens with larger numbers on both sides. Its k=2
silhouette is 0.344, which looks like real separation until the null band is read: 0.325 to
0.371. Stability is 0.906 and 0.896 against a null of 0.863 to 0.969. Seven correlated
slaying columns produce an elongated cloud, and an elongated cloud bisects cleanly whether
or not anything is in it.

Both extended bases agree. The extended CWL k=2 scores a silhouette of 0.214 against a null
band of 0.187 to 0.219 and a stability of 0.949 against 0.869 to 0.972; the extended CDL
k=2 scores 0.236 against 0.204 to 0.262 and 0.925 against 0.671 to 0.973. All inside what
no clusters look like, so nothing is published from either.

**What is real is the axes.** Horn's parallel analysis — each eigenvalue against the 95th
percentile of the same matrix with every column independently permuted, which destroys
correlation while preserving each metric's own distribution — retains five components on
the CWL basis, together 69.0% of the residual variance, and two on the CDL basis, together
89.5%. Read in raw metric terms:

| Basis | Axis | Name | Share | Loads on |
|---|---|---|---|---|
| CWL | 1 | volume | 32.9% | kills, blitz index, kill share, K/D, multikills and plus/minus, all the same way |
| CWL | 2 | survival | 15.8% | more deaths and fewer engagements, with a better plus/minus and K/D |
| CWL | 3 | *axis 3* | 8.5% | assists, against team kills and kill share |
| CWL | 4 | streak depth | 6.2% | deep streaks and six- and seven-kill streaks, against headshot rate and four-streaks |
| CWL | 5 | risk | 5.6% | eight-plus streaks, against assists, suicides and team kills |
| CDL | 1 | volume | 58.4% | kills, kill share, K/D, plus/minus and damage, all the same way |
| CDL | 2 | survival | 31.1% | more deaths against fewer engagements, with a better plus/minus and K/D |

**A name belongs to what a component loads on, and is now assigned that way.** Each name
declares the column its axis should load hardest on — `volume` on kills, `survival` on
deaths, `streak depth` on deep streaks, `risk` on eight-plus streaks — with the mode prefix
and the per-10-minute/per-map suffix stripped, so a name survives both the extended bases'
per-mode duplicates and the denominator fork at the archive seam. Assignment is one-to-one
down the components; anything matching no marker keeps its number. Component 3 of the CWL
basis is the assists axis, nobody has a name for it, and *axis 3* is the honest label.

Naming by *position* is what this replaces, and it had already gone wrong. The basis grew
from 21 columns to 26 as the metric layer gained CWL-eligible columns, Horn's test began
retaining five components instead of four, an assists axis moved into third place, and the
four names slid one seat down: `streak depth` sat on assists, `risk` sat on the streak
axis, and the actual eight-plus-streak axis went unnamed. No number was wrong, which is why
nothing caught it. A release now fails if any published basis's component count or
top-loading column moves away from what this page documents, so the next time the basis
moves it is a decision rather than a silent relabelling.

The CDL basis gets its names from the same function rather than from nobody, which is the
other half of the fix: on what they load on, its two components are a volume axis and a
survival axis, and neither is anything a role vocabulary would recognise.

A player is published as a position on their era's axes rather than a label. Scores are
signed so that each axis's largest loading is positive, so a rerun cannot silently flip a
career's direction, and stored per player-season because the position moves — which is the
part a label could never have shown. A career crossing 2019 has positions on both sets and
they do not join up; nothing on the site draws a line between them.

**Power.** Every verdict is stated against the null it was measured on, with that null's
own spread, so "no taxonomy" always means "no taxonomy separated by more than an
unclustered cloud of this size and shape would show". A well-separated three-group
structure at either n and dimension is recovered easily; the test suite plants one and
requires the code to find it, and requires the same code to refuse an elongated cloud whose
bisection is 0.99-stable. What these bases rule out is groups of that kind. They do not
rule out roles too subtle for 26 CWL box-score columns to see, and the CDL basis, at 7
columns, rules out considerably less than that. No such claim is made from either.

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
  archive records, by season and mode: weapons across all three CWL titles, WWII divisions
  and basic training, Infinite Warfare rigs, payloads and traits, and Black Ops 4
  specialists. The CDL-era source carries no loadout column of any kind, so this tier stops
  at 2019 and always will unless a second source supplies one. Choices under 30 player-maps
  are suppressed. Win rates sit near 50% for
  every widely used option, which is the expected result when both teams field the same
  meta, and worth stating plainly rather than dressing up as an edge.
- **Map and mode analysis.** Scoring environments per map, side and streak effects
  where derivable, map-pool comparisons across eras.
- **LAN versus online.** A paired within-player comparison across the 2020-2022 online
  boundary, which is one of the few natural experiments available in esports, reported
  as effect sizes with confidence intervals. The events and their LAN flags are now
  loaded, so this is unwritten rather than unavailable.
- **Series dynamics (shipped).** P(win series | won map 1), sweep, decider and reverse-sweep
  rates against an enumerated no-memory race, and a direct test of momentum claims. See
  [Tier 2b](#tier-2b-series-dynamics-shipped).
- **Roster-change event studies.** Performance k series before and after a move against
  matched controls, reporting the distribution of chemistry effects, including when the
  effect turns out to be null.

## Tier 5: Finding generation (shipped)

A layer of rules and statistics scans model outputs after every run and emits ranked,
plain-English findings in sixteen kinds. Ten read the ratings, the era adjustment and
the series-dynamics run: trends, outliers, milestones, era context, head-to-head edges,
what-wins-maps weight comparisons per (season × mode), the top open-rating seasons, what
winning map 1 is worth against a race with no memory, the mode-specialization null from
[Map Elo](#map-elo), and published model nulls — the series-level momentum test, and the
carryover null from [Tier 2b](#tier-2b-series-dynamics-shipped).

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

There are currently 223. Each carries the numbers backing it and a link into the
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

## Reproducibility: the metric diff and what a run records

Every model on this page is refitted whenever the record grows or the code changes,
and every refit moves numbers. Until now "nothing else moved" was an assertion. It is
now a report.

### The published surface, snapshotted

At the end of each fit, every published number is flattened to a single
`key -> value` pair and written to a sorted, compressed snapshot: each metric-layer
cell, era-adjusted z-score, plus-minus coefficient, style axis, team rating, backtest
figure, finding, and every leaf of every stored artifact payload. The current run is
around a million such numbers.

A key names the thing rather than the row that held it — a player's handle, a season's
year and league, a metric's name, an artifact's JSON path — so two snapshots stay
comparable across a refit, a reload that renumbers a table, or a change to how player
identity resolves. Lists inside an artifact are keyed by the identity their elements
carry rather than by position, so a leaderboard that reorders reports the moves it
contains instead of reporting every row as changed.

### What counts as a move

A number counts as moved when

    |new - old| > 1e-9 + 1e-6 * |old|

Published numbers are stored as 32-bit reals, which carry about seven significant
digits, so that threshold sits just above the storage floor. It suppresses
representation noise and nothing else: every real change is counted and the largest
are named. Values that are not numbers — a finding's headline, a backtest window, a
verdict such as "the interval excludes zero" — are compared for equality, and a change
there is reported as a flip rather than a move, because a verdict that reverses is not
a small difference.

The report gives, per family of numbers, how many moved, how many flipped, how many
keys appeared and how many vanished; then the largest moves by name with both values;
then how many it did not name. A truncated list that reads as a complete one is the
failure this instrument exists to end.

**The first thing it caught was a whole class of them.** Every bootstrap and permutation
here draws *positions* in a population, and the populations were being ordered by database
ids — rows in `player_id` order, clusters in the order a `series_id` first appeared, groups
in the order a dictionary happened to fill. Those ids are assigned by the loader. Reloading
a source renumbers them, the population permutes, the same seed lands on different
observations, and a published interval moves while the estimate it brackets does not. The
harness found two instances and the class was then swept: every such population is now
ordered **by its own contents**, and every group that resamples on its own — a player's
maps, a cohort's, an era's — draws from a generator seeded from those contents rather than
from one generator threaded through a loop. Each site carries a test that renumbers the rows
underneath it and requires the interval to come back identical.

Landing it moved numbers once, and not only intervals — which is worth stating precisely,
because the reason is instructive. Every published interval that resamples moved. So did
14,750 season **ratings**, by up to 0.010 on a scale where the league average is 1.00: the
per-cohort observation-variance calibration is itself estimated by resampling each player's
maps, so reseeding it moves the shrinkage constant and therefore the ratings it shrinks.
Style scores moved with them — up to 0.068 — because the style basis residualizes on the
published rating before it factors anything. Both are the same fact seen twice: a quantity
estimated by Monte Carlo has Monte Carlo error, and the estimates it feeds inherit it. What
changed is that neither depends on the loader's row ids any more.

### The evaluation population is frozen

A rating version is only comparable to the version before it if both were scored on the
same maps. Repairs to the underlying record change which maps are eligible, so the
eligible set and the evaluation set are two different objects.

The evaluation set is cut once, hashed, and held fixed. Every model run records that
hash. Repairs change what is modelled and leave the ruler alone; the difference between
the frozen set and the currently eligible set is reported at every run and never applied
silently. Re-cutting is deliberate, takes a new label, and requires every prior version
to be re-scored on the new cut before the new one is published. A release gate fails when
a run records no evaluation-set hash, or records one that is not the frozen set's.

### What a run records about itself

A stored backtest that cannot be reproduced exactly is a claim rather than a record. Each
model run therefore stores, beside its hyperparameters and the commit that produced it:

- the fixed seed of every stage that draws random numbers, so a resampled interval can be
  recomputed rather than trusted;
- a hash of the resolved dependency lockfile, so the solver stack is part of the record;
- the interpreter, numpy and platform versions;
- the evaluation-set hash above.

Where a model builds a design matrix, the matrix itself is fingerprinted — shape, column
names and contents — and published with the fit, so a refit that produced different
numbers can be told apart from a refit that was handed different data. The test suite
refits the same data twice and requires the results to be identical bit for bit, and walks
the package to check that no stage owns a seed the run does not write down.

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
