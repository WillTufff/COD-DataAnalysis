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
model, the backtest harness, the finding generator, and career value and aging are
running on real data. The site covers 2013 to
2026 in three archives: wiki transcriptions through 2016, the CWL archive through
2019, and the CDL seasons from three sources joined on a per-row provenance tag.

**Not every section below covers the whole record, and each says which.** The three
box-score archives measure different things. The team ratings, the series
win-probability model, map-level Elo, the metric layer, plus-minus and the momentum
test run on everything loaded. The kill-feed tier and everything built on it are
2017-2018 by construction. The evaluation harness, the season score and the roster
forecast run from 2017 on, because that is the population the site publishes a season
score for. Two further surfaces are narrower than their data because of how they are
declared, not because of what was recorded. That is stated where it happens, not left
to be inferred. Where a result changed when the record grew, this page says what it
used to say.

## Principles

1. **Analysis over reference.** The work worth doing is the era-adjusted comparison,
   the open rating system, the aging curve, the roster-change study. Those don't
   currently exist for competitive Call of Duty.
2. **Methodological transparency.** Every model's spec, code, and backtest is
   published, including calibration curves, so the ratings can be audited, not taken
   on trust.
3. **Interpretation-first visualization.** Charts are annotated to make a point, every
   stat links to the evidence beneath it, and claims carry their uncertainty.

**On scale and model choice.** The dataset is thousands of series and tens of thousands
of stat lines. At that size the appropriate tools are hierarchical and Bayesian
statistics, regression, gradient boosting, and clustering, not deep learning.
That has a useful side effect: the models stay explainable, so this page can actually
explain them.

## Data sources

| Source | Coverage | License |
|---|---|---|
| [Activision `cwl-data` archive](https://github.com/Activision/cwl-data) | CWL 2017-2019 box scores, 44,552 player-game rows across 18 tournaments | BSD 3-Clause, © Activision Publishing 2017 |
| Same archive, structured event feeds | 2017-2018 kill feeds (Infinite Warfare, WWII); BO4 games carry no events | BSD 3-Clause (same repository) |
| [Cito API](https://citoapi.com) (carries Breaking Point match data) | CDL 2020-2026 box scores, 53,832 player-map rows across 1,713 series | Proprietary; attribution required, redistribution not permitted |
| [Liquipedia](https://liquipedia.net/callofduty) via LPDB API | Full-history structure: tournaments, placements and prize money, rosters, transfers, player bios, map-level results | CC-BY-SA 3.0 |
| [Call of Duty Esports Wiki](https://cod-esports.fandom.com) via Cargo API | 2013-2016 box scores, 40,542 player-map rows across 1,369 series; placements, event rosters and awards for 77 events | CC-BY-SA 3.0 |

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
site's export endpoint, and what is published from them is derived analysis:
ratings, era-adjusted metrics, model outputs, with attribution. Every row in the
database carries the source it came from, so that rule is a `WHERE` clause, not
an inference from the season year.

**The two box-score archives are not the same measurement.** The CWL archive carries
a kill feed, per-shot accuracy, streak and multi-kill counts, and a map clock. The
CDL-era source carries damage, contested hill time, non-traded kills and
source-counted clutches, and no map duration at all. Two consequences run through
everything below. Every per-10-minute metric stops at 2019, and a per-map
counterpart takes over from 2020; a cohort is never given both forms of one
quantity, and nothing is averaged across the seam. The kill-feed tier (trades,
clutch reconstruction, man-advantage, engagement distance) is 2017-2018 only, and
always was.

**And in Black Ops 7 they do not count deaths the same way.** Measured against the wiki's
independent transcription of the same maps, our deaths run one lower on 450 of 7,520 paired
lines while kills agree on 99.7% of them. Aggregated to the team-map the reason is plain:
our deaths equal the opposing team's kills on 92.9% of team-maps, and the wiki's exceed
them on a quarter. So the CDL-era source counts a death an enemy kill caused, and the wiki
counts every death, self-inflicted ones included. The source's `suicides` column is empty
for that title, so those deaths are recorded nowhere here. A 2026 kill-death ratio computed
on this page is therefore a little kinder than one computed from the wiki, by about one
death per sixteen player-maps.

**A trade is not the same thing as the kill feed, and this page used to conflate them.**
The feed is what allows a trade to be *reconstructed*: two death timestamps, two teams, a
window between them. That is 2017-2018 and nothing has changed about it. But trade economy
as a measured quantity is not only reconstructible. The CDL-era source counts it directly,
in a `non_traded_kills` column that is populated from 2022 onward. So the record carries
trade economy at both ends and not in the middle: reconstructed for 2017-2018, counted for
2022-2026, and genuinely absent for 2019-2021. The two are different measurements of the
same idea and are never mixed inside a cohort: the reconstructed form is a share of a
player's *deaths* that nobody answered, the counted form a share of their *kills* that
nobody answered, and no title has both.

Project code is licensed AGPL-3.0.

### Where the record starts, and what it is made of before 2017

Competitive Call of Duty starts as a formal circuit in 2008, when MLG added a Call of
Duty 4 pro ladder and ran the first national championship. The scoreboard record starts
five years later. Black Ops 2 in 2013 is the first title with enough transcribed box
scores to measure anything: 2011 and 2012 hold brackets and results and almost no
scoreboards, so **2013 is the floor for every per-map number on this site, and 2008 is
the floor for the sport it describes.** Nothing before 2013 is loaded.

The 2013-2016 rows are a different kind of evidence from the two archives above. They
are community transcriptions of broadcast scoreboards, not a publisher feed, so they
rank third: a wiki row loads only where no Activision or CDL-era row exists, and no
existing value is ever changed by one. Because the load window is a period the other two
sources cover not at all, nothing was overwritten and no disagreement had to be
resolved.

**The error rate is measured, not assumed.** The wiki also transcribes 2017 through
2026, which both other sources already cover, so its method can be checked against
83,418 player-lines it and this project both hold. Maps are paired on map name, mode and
day, ranked by how much of the lobby the two sources name in common. Where a map's best
candidate is tied by another, the pair is left out: two teams meet twice in a day often
enough that the wrong twin would turn two real games into a false error. On the pairs
that survive, kills agree at 99.34%, deaths at 98.60%, and both together at 98.43%.
**The disagreement rate is 1.57%, or 1.19% excluding forty maps where every line differs
and the two sources are plainly describing different games.** Two whole years agree
exactly: every one of the 18,810 lines from 2017 and 2019 that the wiki and the
Activision archive both carry matches on kills, deaths and the winner. That is the
strongest available evidence about the 2013-2016 half, which has nothing to check
against.

**The basket is thinner and the coverage rules do the rest.** The pre-2017 rows carry
kills, deaths, hill time, plants, defuses, opening kills and deaths, captures, and the
mode-split slaying counts. They carry no map duration, so the per-10-minute metrics do
not exist for this era either, and the per-map forms take over exactly as they do for
2020 onward. No coverage matrix is declared anywhere: a metric is published for a title
when that title's own rows carry enough non-zero values for it, so the thin columns drop
out of their own accord.

**Every pre-2017 event now carries its own size.** The wiki publishes a prize pool and a
tier for each tournament, and both are loaded onto the 76 events held from these years.
Before that a world championship and a regional open weighed the same, which is a
difference of about eleven times inside 2013 once the pool is known. Pools published in
sterling, euros or Australian dollars are converted at period averages for the years
involved. One event paid medals. Its pool is unknown, and no event is ever stored at
zero. The tier word is kept as the wiki writes it, in a column of its own, so loading it
cannot quietly change which events count as titles.

**Rotations before 2017 were not one rulebook.** MLG, UMG, ESWC and Gfinity each set
their own map order, so the best-of-five rollup declares a rotation only where the era
actually held one. Black Ops 2 and Black Ops 3 held theirs. Ghosts opened on Domination,
not Hardpoint, and is written out in full. **Advanced Warfare has no declared rotation
at all:** its third map splits Uplink against Capture the Flag 58 to 34 and its fourth
splits three ways, so no order was a rule both teams knew in advance. Advanced Warfare
series take no rollup, and the count of series that got none is published with the
model.

**These seasons are ranked, and two rules make them comparable.** A season score
standardizes a player against everyone who cleared the map floor in their season and
title. Before 2017 that field is an open bracket: the same event holds the best team in
the world and a team that qualified that morning. The first rule is that the comparison
happens inside one season, so an open field is never scored against a league one. The
second is that every season is then pulled toward its own season's mean by how many maps
it is, described under [career rank](#career-rank). That is what stops a 40-map season
from posting a number a 124-map season could not reach.

A per-map rating is still withheld here. `maprows.PUBLISHED_FROM_YEAR` stays at 2017 for
the site's own season ratings and for the evaluation harness, where a forward test run
on seasons nobody can see measures nothing. The all-time board asks a different
question and carries its own floor.

**Identity is quarantined, never guessed.** A handle in this era can belong to several
people, and the wiki says so by naming its pages `Realize (Derrek Jordan)` and `Realize
(Josh Taylor)`. Where several wiki pages resolve to one player row and the wiki gives
them different real names, only the page whose real name matches the one held here
survives, and where none matches, none does. Thirteen pages and 316 maps are held out
on that rule, and six more are held out as ambiguous. Each one is counted and named in
the load report, and none is attached to the nearest plausible career.

### Completeness is published

Map-level statistics do not exist for much of the pre-2018 record. The schema can
represent "series known, stats unknown" directly, and no value is ever fabricated to
fill a gap. Missing data is stored as NULL, and every aggregate carries a
`completeness` figure: the share of underlying maps that have full box scores. A
per-season coverage report is generated after each ingest and published, not buried.

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
simply not made. This applies everywhere cohort scoring is used (the era adjustment,
all 104 metrics, and the team metrics), and the threshold is recorded in each run's
parameters.

That threshold also decides what the CDL era can be asked. A CWL season fields well
over a hundred qualified players; a franchised CDL season fields twelve teams, so its
team cohorts sit below fifteen and publish percentiles with a null z. That is the
policy working as intended, not a gap: a twelve-team league is too small to
call a roster three standard deviations from its own mean.

Objective metrics are mode-specific: hill seconds per 10 minutes of map time for
Hardpoint, first bloods plus plants plus defuses per map for Search and Destroy, zone
captures for Control, flag captures plus returns for Capture the Flag, and uplink
points for Uplink.

Each z-score is stored with its own standard error, so the career arc can draw a real
interval, not a decorative one. A season K/D is a ratio of summed kills to summed
deaths over the maps played, so its sampling error comes from the delta method on that
ratio, using the second moments of the player's own maps. The covariance term matters:
kills and deaths correlate strongly across maps, since a map spent in heavy fighting
raises both, and treating them as independent would overstate the error. Dividing that error by
the same cohort SD that formed the z-score puts it in z units. Seasons of one map, or
with no deaths, get no error and therefore no band. The closed form is checked against a
direct bootstrap of the same maps in the test suite.

This adjustment drives the cross-era leaderboards and the percentile coloring
throughout the site. Player pages show raw and adjusted values side by side so the
adjustment stays visible instead of disappearing inside a number.

Splitting cohorts further by LAN versus online is a planned refinement, and the data for
it now exists: every event carries a LAN flag, and the 2020-2022 seasons are genuinely
mixed: 10 online events against 4 on LAN in 2020, 9 against 3 in 2021, and 5 against 6 in
2022 as the league returned. The CWL years are LAN throughout, so the contrast lives
entirely in the CDL era. It is not implemented, and the constraint on it is now work,
not coverage.

## Tier 1b: Metric layer (shipped)

The archive measures far more than kills and deaths. The metric layer turns every
measured column into a published, era-scored metric, so a player's season can be read
across 104 different lenses instead of four.

Those 104 are not all available in all ten titles, and the split is the seam between
the two box-score archives, not a curation choice. Fourteen metrics are
published for every title from 2017 to 2026; 81 exist only where the CWL archive's
columns do, and 4 only where the CDL-era source's do. Anything denominated in map time
is in the first group by construction, which is why the per-map counterpart of each
per-10-minute rate exists at all.

Metrics are stored in long form, one row per player, season, mode, and metric, each
carrying its own qualification denominator. That denominator is the real sample size
for that metric: maps for rate-per-map statistics, rounds for Search and Destroy round
rates, kills for kill-denominated shares, shots for accuracy. Qualification thresholds
are 8 maps, 50 rounds, 100 kills and 1,000 shots for those four, with smaller floors
where the denominator is itself a rare event: 25 rounds where a side held or conceded
a man advantage, 20 first deaths, and 5 or 15 clutch attempts depending on the metric.
Every threshold ships in the catalog next to the metric it governs. Rows below the
threshold are still written and still scored against the qualified cohort, so a small
sample can be shown and labelled, not hidden.

Two rules keep the numbers honest. First, numerators and denominators are summed across
a player's maps and divided once, so a season rate is never the average of per-map
rates. A player with one quiet twenty-minute map and one loud five-minute map has one
true rate, not the mean of two. Second, a metric is only published for a title whose
data actually supports it.

That second rule is enforced by measurement, not by a hand-written table. Each metric
declares the source columns it reads, and the pipeline counts how many rows carry a
non-zero value for each column in each title. A column counts as tracked once at least
twenty of its rows are non-zero. The threshold is an absolute floor, not a
percentage, on purpose: genuinely rare events, like four-kill rounds at roughly one
percent of rounds, must stay published, while a column that exists in the file but was
never populated must not. Twenty-five columns fall into that second group, in both
archives and in every era.

In the CWL years: Black Ops 4 records fields for time alive and for kills that were not
immediately answered, but both are zero on all 19,120 of its rows; WWII does the same
for hill captures and sneak defuses across 23,048; Black Ops 4 shots and hits are
populated on five rows out of 19,120. In the CDL years the same test catches more.
Contested hill time is declared but empty for 2020 (3,150 rows), 2021 (2,854) and 2026
(3,472), though populated in between. Non-traded kills are empty for 2020 and 2021.
Black Ops Cold War records no assists at all (zero on all 6,892 rows), and none of its
Control round counts, attack, defence or total, carry a value on any of 1,742. And
1v4 clutches sit under the floor in every CDL title, at one to five non-zero rows out of
two to three thousand, which is the shape of a genuinely rare event that this source
cannot separate from a data-entry artefact.

Treating any of those as data would publish a season of zeros as though it were a
finding. They are listed on the methodology page instead, and the metrics that depend on
them simply do not exist for those seasons. As a reading
instruction: title coverage on this site is derived from the data on every run, so a
metric absent for a season means the column was not populated, never that the events did
not happen.

The catalog itself, including each metric's formula, unit, direction, threshold, and
measured season coverage, ships as an artifact of the same run that computes the values.
The stat explorer and the metric glossary both render from it, so a definition and a
number cannot drift apart.

Team metrics use the same machinery with the roster as the subject. Results come in two
shapes. Map-shaped metrics (map win rate, kill differential per map, average Hardpoint
margin, Search and Destroy round win rate) sum over a team's maps exactly as player
metrics sum over a player's. Series-shaped metrics are built from series outcomes
instead: series win rate, and deciding-map win rate, the record on maps where both
teams stood one map from taking the series. A series is one result spanning several
modes, so these exist only for the all-modes cohort: slicing a series by mode would
count it once per mode it touched. The archive does not record a series format, but
every covered format is strictly first-to-N, so the winner's map count is the target;
a series whose maps are not all present is skipped, not replayed wrongly.

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
(game, player) the feed's normal-death count (suicides and team kills excluded) must
equal the box-score death total. WWII reconciles exactly, at 100.00% of 22,728
player-maps; the same rule holds Infinite Warfare to 94.97% of 2,384, the residual being
feed deaths the box never recorded. Player-maps that fail are excluded from every
kill-feed metric through a single queryable set, never patched. The full summary ships as
an artifact, and the WWII figure is a hard check in CI, so a regression in the importer
or the death classification fails the build.

On the reconciled feed the layer measures what the box score cannot. A death is *traded*
when a teammate kills the attacker within five seconds, the archive's own window, and
the untraded deaths are the ones that actually cost a numbers advantage. A *clutch* is
being the last player alive, scored 1vN by how many opponents remain. *Man-advantage
conversion* is whether the team that draws the round's first blood goes on to win it; its
mirror is the *steal*, winning a round opened a man down. The round-based measures are
Search and Destroy only; trades cover both feed titles, Uplink included. Round winners
come from the feed's own round scores, except the deciding round, which resets its score.
Its winner is recovered by matching the box-score map result.

Two limits are stated, not papered over. The per-kill distance field is Infinite
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
unit of play: one life each, a discrete winner, 9,282 of them. The `game_rounds` table
does carry rows for the other feed modes, but a Hardpoint "round" is the whole map and a
Capture the Flag one is a half, so there is no round-scale contest there to model. BO4 has
no feed at all. What is left reconciles cleanly: 1,023 of 1,024 SnD games resolve a round
winner, every one of them four a side. One round in 9,302 is dropped because its feed
contradicts itself, a player dying while their side is already empty, which is the same
treatment a failing player-map gets in the reconciliation view.

**The state is the survivor count, and that is nearly all of it.** Counting outcomes for
every (own alive, opponent alive) pair over both sides of every instant gives sixteen
non-terminal states from ~104,000 observations. Because each instant is recorded from both
teams' points of view, the table is antisymmetric by construction and every *n*-versus-*n*
state is exactly 0.500. That is not a finding, but a property of the encoding, stated so
nobody reads it as one.

| | opp 4 | opp 3 | opp 2 | opp 1 |
|---|---|---|---|---|
| **4 alive** | .500 | .716 | .912 | .988 |
| **3 alive** | .285 | .500 | .782 | .959 |
| **2 alive** | .088 | .218 | .500 | .846 |
| **1 alive** | .012 | .041 | .154 | .500 |

Standard errors run from 0.003 to 0.011 and are widened by √2, because each round enters
its cells twice and the naive binomial error would understate them.

Round win odds track the *ratio* of survivors, not the
difference. A ridge logistic on both gives

```
logit P(win) ≈ 2.01 · [log(own) − log(opp)] + 0.42 · [own − opp]
```

with an intercept of zero, again by construction. Being up one is worth 0.716 at 4v3 and
0.846 at 2v1: the same man advantage, nearly twice the swing, because it is a larger share
of what is left.

**What the backtest compares.** Walk-forward by event: the model scoring CWL Anaheim was
fitted on everything through CWL Seattle and nothing after, over 8,479 rounds in the ten
events that have a predecessor. Losses accumulate per round, not per state row, and
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
function to buy, and the parametric fit is beaten, narrowly, but by an interval that
excludes zero. The logistic is still reported because it is interpretable and the table is
not; it is not used for anything the table can do.

**Two nulls, both stated with an interval.** Each is an add-a-feature question, so each is
asked against the model the feature was added to, not against the published table.

- *Time elapsed in the round adds nothing.* Interacting elapsed time with both survivor
  terms moves Brier by +0.00004 [−0.00001, +0.00009], *p* = 0.13, and the sign is the
  wrong way. This archive could have detected 0.00008.
- *Neither does the bomb.* The feed carries no plant or defuse event; `means_of_death` is
  fifteen kinds of gunfire. It is not wholly unrecoverable, since round durations pile up
  at 90 seconds and then tail off, which is the regulation clock expiring, so a round
  still alive at 90 seconds implies a plant. Adding that indicator moves Brier by
  +0.000001 [−0.000015, +0.000016], *p* = 0.92. The reason is structural, not
  empirical: the feed never says which side planted, and an indicator that cannot be
  attributed to a team is symmetric under swapping the two teams while the target is
  antisymmetric, so it can only enter at zero. It is measured anyway, not argued
  away, because the argument is the kind that is easy to get wrong.

Recovering which side is attacking would be the single largest improvement available here,
and the archive does not currently support it.

### The round along its own clock

The table above answers what a state is worth. It says nothing about when states arrive, so
a third artifact, `round_timeline`, describes the round on a 5-second grid: survivors, win
probability, the model against the outcome, and how much of the sample is still playing.
It is description, not a second model: the probabilities are the same table read
*in sample*, on the rounds that fitted it, which is why a model-free series is published
next to them and why the out-of-sample claim stays with the walk-forward above.

Rounds are not decided early so much as leaned on early. Half of them are over by 60
seconds. At thirty seconds the eventual winner has 3.29 players up against 2.65, a gap of
under two thirds of a player, and the table already reads that as a 64.5% favourite,
climbing to about 0.70 by the seventy-second mark and then *falling back*, because the
rounds still alive that late are the ones nobody is winning.

**A body is worth more late than early, and the table prices both the same.** The third
series is a calibration check over time, on one subset: rounds where the two sides have
different survivor counts. What the table says the side ahead is worth, against how often
that side went on to win, at two single instants: single, because a round appears in
twenty bins and pooling them would treat one round as twenty observations.

| Instant | Rounds | Table says | Actually won | Gap |
|---|---|---|---|---|
| 15 s | 3,409 | 0.773 | 0.785 | +1.2 pt ± 0.7 |
| 60 s | 2,636 | 0.857 | 0.884 | +2.7 pt ± 0.6 |

The late gap clears its error and the early one does not. This does not overturn the "time
adds nothing" null above: that null is about *prediction*, and a small one-directional
calibration drift is entirely compatible with no measurable Brier gain. 0.00008 was the
smallest Brier difference this archive could resolve. Both statements are true, and the
combination is the ordinary situation of an effect that is real and small.

**What the trade window costs.** A death counts as traded when the killer is answered by
the victim's side within 5 seconds, a convention inherited from the archive's own
`kills_stayed_alive` column. Measuring the same latency without the cutoff: 44% of deaths
are ever answered by that side, the median answer takes 7.2 seconds, and only 42% of the
answers that arrive land inside the window. The distribution peaks in its first two
seconds, so the convention is not arbitrary, but it is a cut across a smooth decay rather
than a seam in the data, and it leaves more revenge kills outside than it counts. Trades
are also front-loaded: a quarter of deaths in the opening five seconds are answered in
time, against 16% of deaths at a minute in, when there are fewer teammates left to answer.

Three exclusions. The figure's axis stops at 105 seconds,
where 1.5% of rounds remain. 166 rounds of 9,282 record a length that ends before their own
last death, by 38 seconds at the median, so not rounding, and are dropped from the three
state panels, which need to know when a round stopped; they are kept for the trade
latency, which does not. And the calibration panel starts at 5 seconds: exactly two rounds
in the archive open uneven, and a rate over two rounds is not a rate.

### Win probability added, and why it is not a rating

Each kill moves the round from one state to the next, and the killer is credited with the
change in their own side's win probability. The credits telescope: summed in one team's
frame across a round they land exactly on that team's final win probability minus its
opening 0.500, so WPA is an accounting of the round, not a score attached to it.

As a description of what happened, it works. As a measurement of a *player*, it does not,
and both numbers are published so the leaderboard is not read as a rating.

Per player per round, WPA correlates **0.912** with kills per round. Most of it is a kill
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
small one is not, but nothing here supports the claim that *which* kills a player gets is
a repeatable skill this archive can measure.

So round WPA is published as a per-round description and is deliberately **not** promoted
into the player rating. That was the hope this model was built on: player value measured
in outcomes, not box-score totals, and the reliability test says it is not there.
Reporting it is the point of running the test.

Artifacts `round_win_prob` and `round_wpa` are stored with the `round_wp` run and
recomputed on every rerun.

## Tier 1e: Segment win probability (shipped)

The model above stops in 2018, because the kill feed does. This is the same question asked
of the era after it, at the only resolution that era's record supports: given the score
state of a map right now, what is the probability each team wins the map?

The input is the within-map time series the match record has always carried and the
transform used to discard: the cumulative score at every Hardpoint hill rotation, and the
result of every Control and Search and Destroy round, per team. It cost no new data: the
bytes were already stored. Three modes, measured: **33,780 hill rows over 1,573 maps,
22,181 SnD round rows over 1,233, and 4,126 Control round rows over 508.**

**Search and Destroy is fitted first, and that is the point of the phase.** SnD is the one
mode Tier 1d already models, from a completely different source, for a different era and a
different game engine. Fitting it here puts the same quantity on two independent records a
league era apart, which is a check nothing else in this project can make.

### What the table has to beat

Not a coin flip. The baseline is **the same race played forward with no memory at all**:
every remaining round an independent coin, every remaining hill an independent draw from
the league's own distribution of hill scoring, both enumerated exactly, not
simulated. A lead is already worth something under that baseline; that is arithmetic, not
a finding. The counted table earns its place only by showing that a lead is worth *more*
than the arithmetic says, which would mean the score state leaks information about which
team is better. It is scored walk-forward: fitted on every earlier event, scored on the
next, never on its own maps.

Losses accumulate per map, not per state row. A map contributes a dozen rows that
are the same map seen at successive states and from both sides, and counting them as
independent observations would shrink every interval by roughly the square root of that
count.

### The result is a null, and a useful one

The table does not beat the race arithmetic in any mode. On Search and Destroy and on
Hardpoint it is very slightly *worse*: the empirical cells add estimation noise to a
function the arithmetic already gets right. On Control the gap is smaller than what
this archive could have resolved, which is "too close to call" and not "equal".

Cell by cell, the agreement is close enough to read off the table: a side up 4–2 in a race
to six wins the map 81.3% of the time against the arithmetic's 81.2%, and 5–4 wins 74.7%
against 75.0%. **The score state carries no hidden signal about team quality.** Knowing a
team is ahead tells a reader exactly what the race says and nothing more, which is a direct
measurement against the broadcast instinct that a team "has the map now" beyond the
scoreline.

### Two sources, one era apart, agree

The same SnD table fitted on the 2018 kill feed and on the modern match record shares 35
score states over 1,190 CDL maps and 931 feed maps. **No state disagrees by as much as one
standard error**; the widest gap is 0.032 at 1–2, which is 0.94 standard errors.

93 maps from 2017 are excluded and counted, not dropped quietly: 92 of them end the
moment a side reaches five rounds, so Infinite Warfare played the mode as a race to five. A
4–3 in a race to five is one round from the map and a 4–3 in a race to six is two, so
pooling the two eras would compare different games.

### The win-type splits

Every round arrives labelled with how it was decided, and the vocabulary is richer than any
published Call of Duty analysis separates. Control: `time` 1,888, `kills` 1,116, `ticks`
1,086. Search and Destroy: `kills` 12,273, `bomb_defuse` 4,055, `pre_plant_kills` 3,056,
`post_plant_kills` 1,942, `bomb_explosion` 455, `time` 268. The plant-and-defuse economy is
the half of the mode the kill feed cannot see at all: the 2017–2018 events carry no plant
and no defuse event of any kind.

The swing attached to each type, what taking the round was worth in map win probability,
is flat across all of them. A round won on a defuse counts the same as a round won on
kills, which follows from the null above: the table only knows the score.

### The known failure: resolution

**Segments are reported per team; the box score is reported per player.** Nothing in the
record locates a player action inside a hill or a round. A per-kill leverage weight,
discounting kills taken in an already-decided segment against the same kills in a contested
one, therefore cannot be built from this data, and is not attempted.

What exists instead is a map-level competitiveness weight: the mean distance of the map's
win probability from a coin flip. It removes blowout maps, not the decided minutes
inside close ones, which is coarser than per-kill leverage by exactly the resolution the
record lacks. It is published and **nothing consumes it**; a weight on a player-map line
belongs with the career work, and wiring an untested weight into a rating is not something
this phase does.

### The holes, and the rules that made them

Seasons 2021, 2022 and 2023 carry no segments at all, 2026 Overload has no block, and
Control exists for 2024 and 2025 only. Nothing is interpolated across any of them.

Three anomaly rules, declared before the fit and each one counted in the artifact:

- A round is scored only when both teams have a row and exactly one says it won. A map that
  fails is **truncated** at that round and keeps its prefix, because the score after an
  unknown result is itself unknown. One map loses 8 rounds this way.
- A Hardpoint map whose cumulative score decreases, passes 250, or contradicts the recorded
  map score is dropped whole. Three maps are.
- 37 Search and Destroy maps name only one team from the first round to the last, and 14
  maps have no recorded winner. Both are dropped and reported under their own reason,
  not folded into the others.

Eight maps carry a hill series and no recorded map score. The segments could supply it.
They are not used for that here: filling a missing score is an ingestion change with its own
check, not something a model does quietly on the way past.

Artifacts `segment_win_prob` and `segment_competitiveness` are stored with the `segment_wp`
run and recomputed on every rerun.

## Tier 2: Rating systems

**Team strength over time (shipped).** Elo (K=32) and Glicko-2 (τ=0.5) are fit over the
full history at series level. The rating chart on the overview and team pages toggles
between the two, and the Glicko-2 view shades each team's rating deviation, which is the
thing Elo cannot express: a team's first few series carry a deviation near the 350
starting value, and it narrows as the record accumulates.

**Rating periods.** Glicko-2's deviation is only meaningful if it grows while a team is
idle, and that requires periods. One event is one period: a CWL event is a few days of
dense play followed by weeks of nothing, and a CDL major is the same shape around a
league schedule, which is what the method assumes. The paper wants ten to fifteen games
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
which meant the inflation never ran and the deviation tracked games played, not
time elapsed. That was a bug, and the numbers on this page postdate its fix.

**Hyperparameter sensitivity (shipped).** Elo's K, Glicko-2's τ, and the period length
were asserted constants in a project whose rule is that a model ships with its backtest,
so all three are now swept over the same walk-forward evaluation and the grid is stored
as an artifact of the Glicko-2 run.

The sweep does not choose the published settings, and that is deliberate. Picking the
grid's argmin on the same 4,369 series the score is reported over would be selection on
the test set: the published Brier would then be the best of twenty draws, not an
estimate of anything. The constants stay declared, and the grid is published as
sensitivity analysis: its job is to show how much the choice matters, not to make it.

Ratings are org-lineage-aware: rating state is keyed on the organisation, not the brand,
so a rebrand continues one curve instead of restarting at 1500. The stored rows still
name the team that actually played, so the site shows the brand of the day on a
continuous line, and a lineage is rated under its founding team.

Lineage membership is declared in the importer's identity file, and it is asserted only
where two brands' series windows do *not* overlap. A same-brand roster playing
concurrently is an academy team, not a rebrand, so `Mindfreak` / `Mindfreak Black`,
`EZG` / `EZG Blue` and the three `GGEA` teams stay on separate curves. `Morituri
eSports` / `Regal Morituri` is left unmerged for the same kind of reason: the older
brand reappears *after* the newer one, which is not the shape of a rebrand.

Applying that test now yields twelve lineages spanning 27 brands, and they touch 1,731
of the 4,369 decided series, about two in five. Almost all of it is the franchised
era, where relocation and title sponsorship rename a team without changing the
organisation: `Chicago Huntsmen` → `OpTic Chicago` → `OpTic Texas`, `Las Vegas
Legion` → `Vegas Falcons` → `Riyadh Falcons`, `Los Angeles Guerrillas` → `Los Angeles
Guerrillas M8` → `Paris Gentle Mates`, and nine more of the same shape. The CWL years
contribute one, `eRa` → `eRa Eternity`, over 23 series.

That is a change in what this feature is worth. An
earlier version of this page described the lineage machinery as real, tested and
near-inert, because on the 2017-2019 archive alone it merged a single pair. On the full
record it is load-bearing. Without it the site would restart more than half its rating
curves at 1500 on a rename.

**Series win probability, `winprob_v1` (shipped).** This is not a third rating system;
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
fits. The settings that define a fit (rating period, lineage map, K, τ) are now passed
in from the same values the published Elo and Glicko-2 runs use, and a test pins the
identity phase against the published Glicko-2 prediction by prediction, at every period
length, so the two cannot drift apart again in silence.

**The answer changed when the record did, and it changed sign.** Over the 2017-2019
archive alone this section reported a null: the added features moved Brier by 0.0014
with an interval spanning zero, and recent form and
head-to-head history did not improve series prediction by any amount that archive could
measure. Over the full 2013-2026 record of 4,369 series the same comparison, run the
same way, no longer spans zero. Against the Glicko-2 it is built on, `winprob_v1`
moves Brier from 0.22371 to 0.21969, an improvement of 0.0040, 95% CI +0.0019 to
+0.0060, Diebold-Mariano *p* = 0.0002. That interval excludes zero, so the previous
claim that the added features "do not separate in either direction" is not a
conservative statement of the current result; it is the wrong statement, and it is
retracted here, not softened.

Accuracy has now moved with it, which it had not before. `winprob_v1` calls 65.39% of
series correctly against Glicko-2's 64.41%, a gap of 0.98 points on an interval of 0.07
to 1.85 points that excludes zero. On the 2017-2026 record that gap was 0.03 points and
covered zero comfortably, and this page said the supportable reading was an edge on the
probability and nothing on how often the favourite is named. The larger record does not
support that qualifier any more.

The learned coefficients say where it came from. At the final refit, on 4,350 training
series, `form_diff` sits at **+0.42** on a feature spanning roughly −1 to +1, the
second-largest weight in the model, ahead of Glicko-2's own logit. On the CWL archive
alone the same coefficient fitted at −0.16, small and pointing the wrong way for a
momentum story, which is what a weak feature looks like beside strong collinear ones.
It is now neither small nor wrongly signed. Head-to-head contributes −0.06, the summed
rating deviation +0.10, and the ridge still splits the two rating logits unevenly (0.58
on Elo against 0.23 on Glicko-2, which are near-restatements of each other); read those
two together, as with the slaying pair in the player rating.

The gap carries a power statement as well as an interval, and on the larger record the
two agree with room to spare. Every model predicts the same 4,369 series, so the
comparison is paired: the per-series difference in squared error is one observation, its
mean is the gap, and a 2,000-draw bootstrap over series gives the interval. The 0.0040
gap sits above the 0.0030 that 4,369 series can resolve at 80% power.

The same closed form says what a form effect would have to be worth to show up here.
Suppose the true probability is Glicko-2's logit plus β × `form_diff`; the expected
paired Brier gain and its variance both follow directly, so the smallest detectable β
does too. At 4,369 series, 80% power and a two-sided 5% level, **β would have to be
0.80 or larger**, a team arriving on a 10-0 run against one on 0-10 being about 19
percentage points more likely to win than the ratings alone say. The fit found 0.42.

So the null has become a bounded positive, not a null, and the bound is what
matters. Recent form and head-to-head carry information the ratings do not, worth a few
thousandths of Brier and about one point of accuracy. An effect twice that size would
have been comfortably visible and is not there. "Momentum decides series" remains
something this record does not support; "momentum is worth nothing" is what it no longer
supports either.

**Validation (shipped).** Models are evaluated by walk-forward backtest, which is to
say each prediction is made using only data available before that series. Current
results, over the full 2013-2026 record of 4,369 decided series:

| Model | Brier | Log loss | Accuracy |
|---|---|---|---|
| Elo | 0.21940 | 0.6295 | 65.2% |
| winprob_v1 | 0.21969 | 0.6304 | 65.4% |
| Glicko-2 | 0.22371 | 0.6413 | 64.4% |

All three are fitted the same way: same lineage map, same K and τ, and, where the model
has periods at all, the same event-length rating periods. That was not true until
recently, and the row that changed is `winprob_v1`, which had been carrying a per-series
Glicko-2 of its own.

**`map_elo` is scored in its own section, not this one, and it is now scored on
the same series.** Its series rollup needs the title's mode rotation to enumerate a
best-of-five, and for two years only the three CWL titles declared one, so 1,633 CDL
series were rolled up for no arm at all, the rollup covered 1,310 series over 2017-2019
against 3,027 for every other model, and the two could not be paired. Thirteen of the
fourteen titles now declare a rotation, which puts the rollup on 3,849 of these 4,369
series; the remainder are the races to four or five that a best-of-five enumeration does
not describe, and the Advanced Warfare series that have no rotation to enumerate. The contrast against the three models above is [in that section](#map-elo),
paired series by series, and it is the comparison this table used to be unable to make.

The spread across the table is about 0.004 of Brier and 1.0 points of accuracy, on 4,369
series, and because every model predicts the same series, those gaps are paired data
with intervals, not a leaderboard to be read off:

| Contrast | Brier gap | 95% CI | DM p | Detectable at 80% power |
|---|---|---|---|---|
| Elo − Glicko-2 | −0.00432 | −0.00685 to −0.00167 | 0.001 | 0.00364 |
| Elo − winprob_v1 | −0.00030 | −0.00197 to +0.00141 | 0.734 | 0.00245 |
| Glicko-2 − winprob_v1 | +0.00402 | +0.00187 to +0.00599 | 0.0002 | 0.00303 |

A negative gap favours the first model. Two of the three contrasts exclude zero, and
they put Elo and `winprob_v1` together at the front with Glicko-2 behind them. **The
simplest model on the page is no longer distinguishable from the one built to improve on
it, in either direction**: Elo − `winprob_v1` is 0.00030 on an interval of −0.00197 to
+0.00141, against a detectability threshold of 0.00245. On the shorter record this gap
favoured Elo and excluded zero. It no longer does, and the reading that survives is that
the two models predict this record equally well.

What both clear is Glicko-2. Elo beats it by 0.00432 against a threshold of 0.00364, and
`winprob_v1` by 0.00402 against 0.00303. Accuracy now separates once: `winprob_v1` calls
0.98 points more series correctly than Glicko-2, on an interval of 0.07 to 1.85 points
that excludes zero. The other two accuracy intervals span zero, including the 0.8-point
spread between Elo and Glicko-2. Every number in this paragraph moved when the record
grew from 3,027 series to 4,369; what used to be here is above.

The whole table is computed by `ratings/significance.py` and stored as a `model_gaps`
artifact with the winprob run, so it is remeasured on every rerun.

One caution about reading Glicko-2's row as a verdict on rating periods: the
hyperparameter sweep finds series-length periods scoring better (Brier 0.22037 at τ=0.2,
against 0.22371 for the published event-length periods), and they were *not* adopted for
it. The period length is argued from the shape of the calendar: an event is a few days
of dense play then weeks of nothing, which is what Glicko-2's periods assume. The
sweep is published as sensitivity, never as the selection rule. Picking hyperparameters
on the backtest that then validates them is how a backtest stops meaning anything. The
same sweep puts Elo's best K at 40 against the declared 32, worth 0.0004 of Brier, and the
declared value is kept for the same reason: the sweep is sensitivity, not selection.

Brier score, log loss, accuracy, and calibration curves are published for every model
version. The Brier and accuracy *differences* between them carry intervals, as above;
log loss does not, and no statement here rests on a log-loss gap.

Model outputs are versioned against the run that produced them, recording code version,
hyperparameters, and training window. A rerun replaces a whole run, not editing
rows in place, so any published number can be traced back to the exact code and data
window that generated it.

### Map Elo

The team ratings above rate 4,369 series while the 16,865 decided maps underneath them go
unrated. That is the smaller half of what this section is about. The larger half is that
a series result is a blend of three or four different games (a Hardpoint, a Search and
Destroy, a Control or Capture the Flag), and Call of Duty rosters are not equally good at
all of them. A single number per team cannot say "top three in Hardpoint, mid-table in
Search", and as far as we can tell nothing published anywhere says it.

So `map_elo` fits three arms, all Elo, all on the same 16,865 maps, all strictly
walk-forward:

- **global**: one rating per team, updated once per map. The control: it answers "is the
  extra sample worth anything" without changing the model.
- **mode**: one rating per (team, mode). Full mode specificity, and a fifth of the sample
  behind each number.
- **blend**: the two mixed per team by how much mode history it has, `w = m / (m + 40)`
  maps in that mode. It nests both: `w = 0` is global, `w = 1` is mode. It needs no
  choice between them, which is why it is the arm whose rollup goes in the table above.

All three share one K (16, half the series-level 32 on the ground that a map carries less
information than a series, and declared, not tuned). Sharing it is deliberate: the
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

The rotation is a league rule, known before the series starts, and it is not one rule.
From Infinite Warfare on, maps 1, 2, 4 and 5 are Hardpoint, Search, Hardpoint, Search,
so only the third map is declared per title: Uplink (IW), Capture the Flag (WWII),
Domination (MW19), Overload (BO7), and Control everywhere else. Before 2017 no league
set the order — MLG, UMG, ESWC and Gfinity each ran their own — and the shorthand does
not describe what they ran. Black Ops 2 held Hardpoint, Search, Capture the Flag,
Hardpoint, Search. Ghosts opened on Domination and is written out in full. Black Ops 3
held Hardpoint, Search, Uplink, Capture the Flag, Search. **Advanced Warfare declares
nothing at all**: its third map splits Uplink against Capture the Flag 58 to 34 and its
fourth splits three ways, so no order was a rule both teams knew in advance.

It stays declared, not derived: reading the rotation off the series being predicted
would leak the result. But a declared constant nobody checks is just an assertion, so a
test holds each one to the archive — at 95% of the maps in that slot for the
league-mandated rotations, and at 90% for the four pre-2017 titles, which no league
mandated. All 35 CDL (title, map) cells are unanimous; the CWL titles run 95.3% to
99.6% and the pre-2017 titles 91.8% to 99.6%, the exceptions being series that swapped
a map.

Thirteen of the fourteen titles declare a rotation; for two years three did, and the
1,633 CDL series that declared none were dropped from every arm's rollup without
anything failing. The rollup below covers 3,849 series over 2013-2026. It leaves out
279 series in another format — the races to four or five, plus series the archive holds
only part of — and the 192 Advanced Warfare series that have no rotation to enumerate.
The `best_of` column cannot be used to find the first group, since it records seven on
five-map scorelines and five on seven-map ones, so they are identified by the winner's
own map count and counted rather than scored against a question they did not ask. A
release fails if the count of series missing a rotation is anything but zero. Advanced
Warfare is counted apart from that, because a title with no rotation is a different
thing from a title whose rotation nobody declared.

**The result on maps.** Scored on all 16,865 maps, against the 0.25000 a coin flip gets:

| Arm | Brier | Log loss | Accuracy |
|---|---|---|---|
| global | 0.23544 | 0.66367 | 60.0% |
| blend | 0.23558 | 0.66361 | 60.2% |
| mode | 0.23958 | 0.67199 | 58.5% |

**A mode-specific rating does not beat a global one at predicting map winners. It loses.**
Global − mode is −0.00414, 95% CI −0.00539 to −0.00280, DM p < 0.0001, against a
detectability threshold of 0.00185. It clears both tests, and it clears them by more than
it did on the CWL archive alone. Global beats mode at every K in the sweep, from 4 to 48,
so this is about the granularity of the state and not about a constant chosen for one arm.
The blend is indistinguishable from global (−0.00014, CI −0.00097 to +0.00076, p = 0.75)
and beats mode outright by 0.00400 (CI +0.00336 to +0.00461).

That is the answer to the question this was built to ask, and doubling the sample did not
change it: cutting the record by mode costs more in precision than mode identity returns
in signal. What that does *not* say matters too. The mode arm's problem is that
each rating sees a fraction of a team's maps, so the result is about sample. A larger
record makes it *more* visible, not less, because global and blend sharpen with
the extra data while mode stays thin.

**Where it goes wrong, per mode.** The overall number hides a real pattern, so the same
contrast is computed within each mode:

| Mode | Maps | global | mode | blend | global − mode | 95% CI |
|---|---|---|---|---|---|---|
| Hardpoint | 6,075 | 0.23108 | 0.23361 | 0.23150 | −0.00253 | −0.00433 to −0.00079 |
| Search and Destroy | 5,510 | 0.24809 | 0.24753 | 0.24536 | +0.00057 | −0.00206 to +0.00306 |
| Control | 1,681 | 0.23043 | 0.23916 | 0.23343 | −0.00873 | −0.01315 to −0.00433 |
| Capture the Flag | 1,399 | 0.22405 | 0.23893 | 0.22799 | −0.01488 | −0.01991 to −0.01030 |
| Uplink | 1,073 | 0.22314 | 0.23175 | 0.22495 | −0.00861 | −0.01383 to −0.00354 |
| Domination | 585 | 0.23504 | 0.24315 | 0.23624 | −0.00811 | −0.01327 to −0.00262 |
| Overload | 282 | 0.22902 | 0.23886 | 0.23002 | −0.00984 | −0.02028 to +0.00060 |
| Blitz | 260 | 0.22167 | 0.24170 | 0.22665 | −0.02003 | −0.02897 to −0.01097 |

Search and Destroy is still the only mode whose gap does not run against the mode arm,
and its interval still spans zero (p = 0.66, detectable at 0.00363). The correct statement
remains that Search is the one mode where mode-specific state is *not shown to hurt*, not
one where it helps. It is also the mode where the blend does best, beating global by
0.00274 (CI +0.00083 to +0.00456), which is suggestive and sits just inside its own power
threshold of 0.00264. Search is the mode with the least scoreboard signal and the most
distinct skill, so a residual there is the result worth chasing with more data. Going
from 1,656 Search maps to 3,810 narrowed that interval without resolving it.
Control has crossed the other way: on 485 BO4 maps its gap was the second-widest in the
table, and on 1,681 maps across both eras it is a firm loss for the mode arm. The three
thinnest modes go the other way hard: Uplink, Overload and Domination each lose the better
part of a hundredth of Brier to mode-specific state, and only Uplink's interval clears
zero. That is what a rating with one to three hundred maps behind it looks like from
both sides at once.

**Is mode specialization real at all?** A spread of per-mode ratings proves nothing on its
own. Fit five noisy numbers per team instead of one and they will differ. So the spread
is tested against a permutation null. Mode labels are shuffled *within each event*, which
keeps every team, opponent, result, date and the event's own mode mix and destroys only
the association between a team and which mode it was playing. The statistic is the SD
across qualified (team, mode) cells of that cell's rating minus the team's own global
rating.

Over 263 cells with at least 25 maps each, the observed spread is **66.5 rating points**
against a permuted null of 65.6 (95% range 62.5 to 69.1) over 300 refits: **p = 0.28,
well inside the null.** About 0.9 points of spread survive what noise alone supplies, out
of 66.5.

This is the same verdict the CWL archive gave, and every enlargement of the record has
moved it further the wrong way for a mode-specialization story: with 98 cells the observed
spread cleared the null's midpoint by 3.4 points at p = 0.06, close enough to be worth
another look; with 162 cells it cleared by 1.4 at p = 0.27; with 263 it clears by 0.9.
**This record cannot show that Call of Duty teams have real per-mode strengths, distinct
from being good or bad in general**, and it now says so with more sample, not less. The
per-mode table is still stored and shown, because the ordering is the thing readers ask for
and hiding it would not make it less tempting elsewhere. It is published with this number
attached, and the largest gaps in it (Team Kaliber −215 in Uplink, Team Kaliber −188 in
Blitz, Chicago Huntsmen −188 in Domination) are within the range shuffled labels produce. Note what the null does *not* rule out: an
effect too small for 16,865 maps to separate from noise. "Mode specialization is not
measurable here" is what this says; "mode specialization does not exist" is not.

**Reading `mode_ratings.delta` off the artifact.** The stored `delta` is a cell's rating
minus the team's global rating, and it is not centred: across the 263 qualified cells it
averages **−25** and is negative in 173 of them. That is a property of the estimator, not
of the league. A mode rating is fit on a fraction of the maps the global rating sees, so
it regresses further toward the initial value, and the size of the pull depends on how
much of the rotation the mode is: blitz −62 on average, control and capture the flag −41,
domination −28, search −22, hardpoint −19, and the thinnest modes least of all because
their cells barely clear the 25-map floor. Printed raw, `delta` says almost every team is worse at every mode
than they are overall, which cannot be true of a set of modes that make up the whole. The
figures quoted above carry that offset and are quoted only to show the range the null
covers.

Team pages therefore subtract the field's mean gap in each mode before drawing anything,
which leaves a gap against the field, not against the estimator. The chart shades
the null band behind the bars and mutes every bar that falls inside it, and the verdict
above travels with it in the same component, so no page can render the ordering without
the number that says how much of it is real.

**What the extra sample does buy.** The one thing that clearly works is rating maps at
all. Rolled up to series and paired against the series-level models on the same 3,849
series they both cover:

| Model | Brier | Accuracy |
|---|---|---|
| map_elo, blend | 0.21650 | 65.4% |
| Elo | 0.21843 | 65.6% |
| winprob_v1 | 0.21878 | 65.6% |
| map_elo, global | 0.21905 | 65.2% |
| map_elo, mode | 0.21938 | 65.3% |
| Glicko-2 | 0.22245 | 64.6% |

| Contrast | Brier gap | 95% CI | DM p | Detectable at 80% power |
|---|---|---|---|---|
| blend − Elo | −0.00193 | −0.00342 to −0.00043 | 0.011 | 0.00213 |
| blend − winprob_v1 | −0.00228 | −0.00440 to −0.00014 | 0.030 | 0.00293 |
| blend − Glicko-2 | −0.00595 | −0.00874 to −0.00311 | <0.0001 | 0.00398 |

**Rating maps and rolling them up beats rating series directly, and it now survives the
test it had never been given.** For two years this held only over the 1,310 CWL-era
series a declared rotation reached, against an Elo row covering 3,027. The numbers were
not paired, and the result had not been tested since 2019.
Paired over all three eras it holds against all three series-level models. Nothing about
the model changed; it sees 2.9× as many results.

The margins are small and two of the three are near the edge of what this many series
resolves. Against Elo the gap is 0.00193 and against `winprob_v1` 0.00228; both
intervals exclude zero and neither is far from doing otherwise. Against Glicko-2 the gap
is 0.00595 and clears comfortably. The claim this record supports firmly is that map
ratings beat Glicko-2; against Elo and `winprob_v1` it is ahead on a margin at the edge
of what 3,849 series can resolve.

The rollup also separates the arms in a way the map-level scores could not. On maps the
blend was indistinguishable from global (p = 0.75); on series it beats global by 0.00255
(CI +0.00075 to +0.00428, p = 0.005), while global and mode are indistinguishable there
(−0.00033, CI −0.00318 to +0.00248, p = 0.82). Nothing about the ratings differs between the two views. The same numbers are
being asked a harder question, and enumerating five maps rewards a rating that is right
about *which* map more than a single map's Brier does. Accuracy moves with Brier in every
one of these contrasts but resolves in only one of them (blend over winprob_v1, +1.2
points), which is the usual gap between a proper score and a threshold count.

Sensitivity is stored as a `map_sweep` artifact and, as everywhere else on this page, does
not choose anything: K is declared at 16 (the grid's best for the global arm is 12, for
the mode arm 16 and for the blend 20, all within 0.0003 of Brier of each other) and the
blend constant at 40 (the grid mildly prefers 160, by 0.0006, and the curve is flat from
40 upward). All of it (`map_backtest`, `series_rollup`, `mode_specialization`,
`mode_ratings`, `map_sweep`) is computed by `ratings/maplevel.py` and rewritten on every
pipeline run.

**Open player rating (shipped).** The composite rating, built in four steps, each of
them auditable:

1. *Learn what wins maps.* For every (season × mode), each map is one observation:
   the difference between the two teams' per-10-minute profiles (kills, deaths,
   assists, mode objective), standardized, regressed against which team won the map.
   The regression is L2 logistic (λ=1 on standardized features), fit by iteratively
   reweighted least squares in ~40 lines of published numpy, no black box. Cohorts
   with fewer than 40 maps are not fit. The learned weights are stored with the run
   and published: they are data-derived answers to "how much was a one-SD edge in
   hill time worth, against the same edge in kills, in this title?" One caveat for
   reading them: in respawn modes a team's kills mirror its opponent's deaths almost
   exactly, so those two coefficients are near-collinear and the ridge penalty splits
   their shared weight; read them jointly as slaying. Every coefficient also ships
   with a bootstrap interval, because a few hundred maps of collinear features do not
   pin one down as tightly as a single number implies; see
   [how much of the weights is signal](#how-much-of-the-weights-is-signal) below.
2. *Score players with those weights.* Each player-season-mode aggregate is z-scored
   against its qualified cohort (≥ 8 maps, as in the era adjustment) and dotted with
   the mode's weights. That score is the observation; it is not yet a rating.
3. *Estimate what the score means.* Within each cohort, a two-level normal-normal
   model says a player's maps are noisy reads of a true skill and true skills are
   spread across the league. Fitting it gives the posterior for each player.
   Partial pooling means a hot 12-map season cannot outrank a great 200-map one, and it
   gives the interval in the same closed form, not from a bootstrap bolted on
   afterwards. See [the rating is a posterior](#the-rating-is-a-posterior) below,
   and [how many maps a season needs](#how-many-maps-a-season-needs) for the
   pooling strength it implies.
4. *Normalize.* The season rating blends mode posteriors weighted by maps played,
   centred so the qualified cohort averages 1.00 and scaled so one rating point is
   0.15 of the cohort's estimated true-skill spread. The published `rating_sd` is
   the posterior SD, on every row including the per-mode ones.

**What the rating covers, and what it costs across the seam.** Step 1 fits 44 cohorts, one
per (season × mode): every mode every season played, with no exceptions and none withheld.

It fitted sixteen two phases ago and 36 one phase ago, and how each batch went missing
matters, because all three had the same shape. A feature declares the columns it reads. A
cohort keeps the features whose columns it has. A cohort with too few features is not
rated. So a mode does not fail loudly when the archive expresses a quantity in a form the
feature set did not name — it fails silently, and the season above it publishes an
all-modes rating over whatever survived.

Three forms of that, all now repaired. *A denominator the source does not carry.* Only the
numerator was declared, so a CDL Hardpoint cohort reported itself available on kills,
assembled, and emptied one zero denominator at a time; the CDL box scores carry no map
clock, which left Search and Destroy as the only CDL cohort standing. Every 2020-2026
composite was a Search and Destroy rating wearing an all-modes label. Search and Destroy
itself was denominated per round and nothing else, and no pre-2017 title counts rounds, so
all four of them lost the mode outright — a third of that era's maps, on cohorts whose
kills, deaths and bomb plays were all present. *A numerator one source splits and another
does not.* Ghosts records bomb plants on 79% of its rows and defuses on 6%, so a feature
reading plants + defuses dropped the only objective column that cohort has. *A mode nobody
named.* Domination, Blitz and Overload had no entry in the feature-set dictionary at all,
and a dictionary lookup that misses returns no features rather than an error, so 2014 was
never rated and 2020 and 2026 published two modes out of three.

A feature slot is now an ordered list of forms and the cohort decides which one it can
fill: per ten minutes where the clock exists and per map where it does not, per round
where rounds are counted and per map where they are not, the paired numerator where both
columns are there and the single one where only one is. Two release gates hold the repair
in place — one fails the run when a mode with maps in the archive has no feature set, the
other when a (season × mode) with box scores produces no cohort.

The seam still costs something, and it is now visible instead of hidden. What each cohort
fits on is recorded per cohort in the `mode_weights` artifact, so this table is generated
, not transcribed:

| Era | Hardpoint | Search and Destroy | Third map |
|---|---|---|---|
| 2013-2016 | kills, deaths, and hill time or the round score, per map | kills, deaths, and plants + defuses or plants alone, per map | captures, returns, uplink points, or Blitz and Domination captures |
| CWL | kills, deaths, hill time, hill captures, time per life, per 10 min | per round, with the kill feed where there is one | kills, deaths, captures, first-blood net |
| CDL | kills, deaths, hill time, per map | per round | kills, deaths, and assists where the season records them |

The rows thin out where the source thins out, and a coverage measurement rather than a
declaration is what drops each column. The 2013-2016 titles carry a scoreboard and no
clock, no round tally and no intangibles, so their cohorts fit on three columns; Black Ops
2 and Advanced Warfare record no hill time on any row, and the round score is the only
reading of Hardpoint objective play they have. Hill captures and Control captures are not
in the Cito box scores at all. Two CDL third maps have no objective column in any source —
2020 Domination and 2026 Overload — and rate on kills, deaths and assists.

One cohort on this page still has no non-slaying feature and therefore no
[beyond-the-gunfight ratio](#how-much-of-the-weights-is-signal): 2021 Control. Black Ops
Cold War records no assists, no Control round counts and no first bloods, so two columns
is what that box score supports. 2022-2025 Control had the same shape until assists were
admitted as the fallback for the Control intangibles the CDL source does not carry — the
box-score feature set had been giving those seasons a column the per-mode set took away.

Its validation is walk-forward within each (season × mode): every event's maps are
predicted using weights trained only on earlier events. That number establishes one
narrow thing: that the learned weights generalize across events instead of memorizing
them. It is not evidence that the model can forecast anything. Several of the
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
happening. The published interval had to come from a bootstrap bolted on at the
end, because neither step could produce one.

One two-level normal-normal model states all of it at once. Inside a cohort
(season × mode), a player's per-map score is a noisy read of a true skill, and true
skills are spread across the league:

    y_ij = θ_i + ε_ij,   ε_ij ~ N(0, σ²)     what a single map measures
    θ_i  ~ N(μ, τ²)                          how good players actually are

A season is summarized by x_i, the score of the season profile, whose sampling variance
is v_i = σ²/m_i over m_i maps. The posterior for θ_i is then closed form, no sampler,
no probabilistic-programming dependency, about thirty lines of numpy:

    B_i = τ² / (τ² + v_i)        how much of this season is signal
    θ̂_i = μ + B_i (x_i − μ)      posterior mean
    V_i = B_i v_i                posterior variance

Three things follow that the old pipeline could not state.

**The shrinkage was this model all along.** B_i is exactly m_i / (m_i + k) with
k = σ²/τ², so the estimated-k work described below is not discarded by this change; it
is recovered as a consequence of it. What changes is that k no longer has to be
estimated by a separate moment decomposition; the same fit that produces the ratings
produces it.

**The scale is τ, not the observed spread.** A rating point is now 0.15 of the estimated
*true* spread between players in that cohort. Under the old estimator it was 0.15 of the
observed spread, which is √(τ² + mean v) wide, so "one rating SD" quietly meant
something different in every cohort, depending on how many maps its players happened to
play. The ratio of the two is worth publishing on its own: **τ over the observed spread
is how much of the leaderboard's range is real difference between players, not
noise.** Across all 28 cohorts it runs from 0.36 in 2017 IW Search & Destroy, where a
721-map cohort of five-map seasons leaves most of the visible spread unexplained by
skill, to 0.94 in 2022 Vanguard Hardpoint. The pattern tracks mode more than era:
Hardpoint sits between 0.87 and 0.94 in the CDL years, Control between 0.77 and 0.82, and
Search and Destroy between 0.54 and 0.79. Search is where a season's visible spread is
least about skill, in both eras and by a wide margin.

**Two cohorts used to fit at exactly zero, and the reason is worth keeping.** In 2021
Black Ops Cold War and 2022 Vanguard the fit landed on τ² = 0, so B_i was zero for
everyone, every posterior mean collapsed onto μ, and all 63 and 61 players in those
seasons carried a published rating of exactly 1.00. Nothing failed and nothing said so.

The cause was not the data. Those cohorts are not thin: 63 and 61 qualified players over
2,296 and 2,262 maps, against 62 and 2,632 for 2025, which fitted normally, and their
per-map spread in kills per round matches their neighbours to two decimal places. The
cause is that τ² = 0 is a **fixed point** of the EM iteration: at τ² = 0 every B_i is
zero, so every θ̂_i is μ, so the M-step returns zero, and the loop exits reporting
convergence after a single step having looked at nothing. The iteration was started at
Var(x) − mean(v) floored at zero, and that moment estimate goes negative whenever σ² is
large. That is a property of the noise, not of the players, and it started those two cohorts
exactly on the point they could not leave. Both showed one iteration where every other
showed 39 to 1,336.

The start is now floored at a strictly positive share of the observed spread instead. EM
is monotone in the marginal likelihood, so a cohort whose optimum really is on the
boundary still descends to it; it just has to get there by iterating instead of
assuming it. Both cohorts now fit in the interior: 2021 BOCW Search & Destroy at τ = 2.22
over 343 iterations, 2022 VG at τ = 1.51 over 399, and the whole record fits between 20
and 1,336 iterations with nothing on the boundary.

Two things guard it. "Collapsed" is now a statement about the fit, not the
optimizer: τ² negligible beside what one season's maps measure. So a run that never
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

**One assumption, measured, not asserted.** v_i = σ²/m_i treats the season
profile as a mean of m maps, but it is a ratio of summed numerators to summed
denominators, close but not identical. Instead of caveating that, each cohort measures it:
every player-season's score is resampled from its own maps, and the ratio of that
variance to σ²/m is averaged over the cohort. The median is **0.964** (0.877 to 1.024 by
cohort, over 2,711 player-seasons). The profile is a few percent steadier than the plain
form assumes, and σ² is scaled by the measured factor before the fit, not after. It
matters more than it looks: v enters τ² = Var(x) − mean(v) with a minus sign, so an
overstated observation variance does not merely widen intervals, it eats the
between-player variance and reports a cohort as flatter than it is. Left uncalibrated,
2017 IW Search & Destroy fits at τ² = 0; calibrated, it fits at 0.36 and is the lowest
value on the page.

**What moved.** The published ratings shift by 0.019 on average and 0.070 at most, on a
scale whose league SD is 0.15; the rank correlation between the two estimators is 0.988
and seven of the top ten qualified seasons are the same players. It remains a
re-estimation, not a re-ranking.

**Does it forecast better?** Being better specified is an argument, not evidence, so the
new estimator and the old one are both run through the roster forecast in
[two tests the rating can fail](#two-tests-the-rating-can-fail): identical maps,
identical weights, identical prefixes, differing only in the step being tested. The
posterior wins by −0.00087 of Brier [−0.00180, +0.00000] over 9,391 maps, an interval that
now reaches zero at its upper end, on a gap that sits under the 0.00126 this sample can
resolve. It read [−0.00180, −0.00010] over 9,257 maps before the recovered modes enlarged
the population, and on the CWL archive alone the same contrast was −0.00112 [−0.00220,
−0.00010]. Two of those three readings exclude zero and the current one does not, which is
what a gap this far under the resolvable size looks like when the sample moves. The
posterior does not cost anything out of sample and may be worth a little. Pick rates are a
coin flip apart, 56.6% against 56.7%. The case for the change rests on the specification and the intervals, and the
forecast says it costs nothing, which is what it had to say.

(μ, τ²) are fitted by EM: closed form per step, monotone in the marginal likelihood,
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
table or plot it sits in. A band that is only comparable to itself hides the one thing
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

A season with no stored SD is drawn as a point with no band. A zero-width band would
read as certainty. Two intervals are called separated only when they do not
touch, which is the conservative direction, and on the full record that test now
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
partial pooling while k was fixed at 15 for every cohort: the right functional form
with an invented constant, which is not the same claim. k is now read straight off the
fit as σ²/τ², the ratio of within-player noise to the real spread between players, and
the number it lands on is a fact about the mode.

| Cohort | Players | k (maps to keep half the signal) | vs the old 15 | Signal share |
|---|---|---|---|---|
| 2013 BO2 Hardpoint | 94 | 6.5 | −8.5 | 0.76 |
| 2013 BO2 Capture the Flag | 92 | 24.0 | +9.0 | 0.43 |
| 2015 AW Hardpoint | 136 | 6.8 | −8.2 | 0.81 |
| 2015 AW Uplink | 131 | 14.4 | −0.6 | 0.65 |
| 2015 AW Capture the Flag | 120 | 8.4 | −6.6 | 0.68 |
| 2016 BO3 Hardpoint | 217 | 9.5 | −5.5 | 0.87 |
| 2016 BO3 Uplink | 217 | 7.7 | −7.3 | 0.88 |
| 2016 BO3 Capture the Flag | 211 | 16.1 | +1.1 | 0.72 |
| 2017 IW Hardpoint | 128 | 9.7 | −5.3 | 0.66 |
| 2017 IW Search & Destroy | 128 | 32.7 | +17.7 | 0.38 |
| 2017 IW Uplink | 128 | 18.5 | +3.5 | 0.53 |
| 2018 WWII Hardpoint | 165 | 13.0 | −2.0 | 0.90 |
| 2018 WWII Search & Destroy | 165 | 33.7 | +18.7 | 0.76 |
| 2018 WWII Capture the Flag | 165 | 21.4 | +6.4 | 0.79 |
| 2019 BO4 Hardpoint | 204 | 11.7 | −3.3 | 0.88 |
| 2019 BO4 Search & Destroy | 204 | 22.0 | +7.0 | 0.76 |
| 2019 BO4 Control | 204 | 10.7 | −4.3 | 0.83 |
| 2020 MW19 Hardpoint | 76 | 9.3 | −5.7 | 0.90 |
| 2020 MW19 Search & Destroy | 76 | 35.4 | +20.4 | 0.68 |
| 2021 BOCW Hardpoint | 63 | 13.6 | −1.4 | 0.88 |
| 2021 BOCW Search & Destroy | 63 | 76.2 | +61.2 | 0.57 |
| 2021 BOCW Control | 63 | 16.4 | +1.4 | 0.79 |
| 2022 VG Hardpoint | 61 | 6.1 | −8.9 | 0.94 |
| 2022 VG Search & Destroy | 61 | 91.6 | +76.6 | 0.54 |
| 2022 VG Control | 63 | 18.6 | +3.6 | 0.77 |
| 2023 MWII Hardpoint | 63 | 9.0 | −6.0 | 0.93 |
| 2023 MWII Search & Destroy | 63 | 46.1 | +31.1 | 0.69 |
| 2023 MWII Control | 63 | 16.8 | +1.8 | 0.81 |
| 2024 MWIII Hardpoint | 65 | 10.3 | −4.7 | 0.91 |
| 2024 MWIII Search & Destroy | 65 | 35.8 | +20.8 | 0.73 |
| 2024 MWIII Control | 65 | 17.0 | +2.0 | 0.81 |
| 2025 BO6 Hardpoint | 62 | 14.5 | −0.5 | 0.89 |
| 2025 BO6 Search & Destroy | 62 | 26.7 | +11.7 | 0.78 |
| 2025 BO6 Control | 62 | 15.6 | +0.6 | 0.82 |
| 2026 BO7 Hardpoint | 76 | 7.7 | −7.3 | 0.93 |
| 2026 BO7 Search & Destroy | 76 | 33.2 | +18.2 | 0.73 |

The old constant was close for the respawn modes. Hardpoint lands between 6.1 and 14.5 in
every title on record, Control between 10.7 and 18.6, Uplink between 7.7 and 18.5, and it
is far too weak everywhere else. Search & Destroy wants 22 to 92 maps in every title it
appears in, in every era and under every format: a round-scale scoreline with four players
a side is noisy enough that a season needs two to six times as many maps before it says as
much about a player as a Hardpoint season of the same length. Capture the Flag sits
between the two, from 8.4 to 24.0. That ordering is not something a fixed constant could
express; it is the substantive result here, and adding the pre-2017 seasons left it
standing.

The two extreme rows are 2021 BOCW and 2022 VG Search & Destroy at 76.2 and 91.6. Those
are the cohorts that used to fit at τ² = 0 and publish 1.00 for everyone; fitted properly
they are not degenerate, they are noisy. That is the largest σ² of any Search cohort on record,
about 20% above their neighbours, against an ordinary spread of true skill. A season of
those two years says less about a player than any other season in the archive, which is a
real finding about those seasons and was previously reported as the players being
indistinguishable.

The moment estimator that first produced these numbers is still fitted and still shipped
as the `rating_shrinkage` artifact, next to the model's k in `rating_posterior`. Its
median across all 36 cohorts is 15.9 maps against the model's 15.9. The two agree closely
where a cohort is well sampled, and diverge exactly where they should. On 2017 IW Search &
Destroy, where 5.6 maps per player is thin enough that how you weight players changes the
answer, they differ by 20 maps (32.7 against 52.9). Keeping both visible is cheaper than
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
97.5th percentiles of 200 draws. Refitting, not conditioning on the original
standardization matters, because the standardization is estimated from the same maps.
The ratio is recomputed per draw, not propagated from the per-coefficient
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
else: the Cito box scores carry no Control captures. So there is no "beyond" to put in a
numerator, and a ratio is not published for a cohort that has only one half of it.

The intervals are wide, and unequal by a wide margin: Search & Destroy's span roughly
±15% of the point estimate in the deep CWL cohorts, while Uplink's runs from 1.29× to
6.05×, a factor of five, on 79 maps. Reporting those two side by side as "0.35×" and
"2.61×" was the problem. The CDL cohorts sit in between, at 234 to 440 maps each, and
their intervals are correspondingly looser than the CWL Search & Destroy rows without
being anywhere near Uplink's.

Twenty of the twenty-one cohorts resolve, in the only sense that matters here: their
interval excludes 1.0, so the sign of the claim survives. One does not. 2017 IW Hardpoint
sits at 0.74× with an interval of 0.46 to 1.04, and 126 maps cannot say which half
carried that mode. Its finding is suppressed, not published with a hedge, and
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
though it were. A CWL Hardpoint cohort puts three columns in the numerator (hill time,
hill captures, time per life), while a CDL one has only hill time, because the other two
are not in the source. The CDL ratio is therefore one strong objective column against two
slaying columns, where WWII's is three against two, and a numerator concentrated in its
best column will rate higher than one diluted across three. The comparison that survives
that objection is the sign and the interval, not the multiple: modern Hardpoint is decided
away from the gunfight, decisively, in every season on record. Whether it is *twice* as
decided as WWII's is a question this basket cannot answer.

The intervals ship in the `mode_weights` artifact with every rating run, per coefficient as
well as per ratio, and the artifact now records which denominator each feature resolved
to, so this table and the feature table above are remeasured on each rerun instead of
transcribed.

### What the rating measures: four feature sets, compared

Steps 2 to 4 above never change. What changed across versions is step 1's answer to
"which numbers describe a team's map", and all four answers are kept runnable so the
choice can be checked, not asserted.

- **1.0.0**: kills, deaths, assists and one objective column per mode, all per ten
  minutes. The box score, essentially. The objective slot ends in the round score, which
  is what a title carrying a scoreboard and nothing else has.
- **2.0.0**: per-mode feature sets drawn from the metric layer, with per-mode
  denominators: Search & Destroy is measured per *round*, not per minute, because a
  round is what the mode actually spends. First bloods, first deaths, survival, time
  per life, hill captures and flag carry time enter here. Where a mode's own columns are
  absent the slot falls back rather than emptying: the round score for Hardpoint, the
  bomb plant alone for Search & Destroy, assists for Control.
- **2.1.0**: adds the kill-feed tier to the modes where a trade means something:
  untraded-death rate and trade kills in Hardpoint and Search & Destroy, plus deaths
  that surrendered a man advantage in Search & Destroy. **This is the published
  version.**
- **2.2.0**: claims the columns both archives already populate and that no earlier
  version had named. Nothing new was fetched: damage, the share of a player's kills
  nobody traded back, contested hill time, shot accuracy, headshots per kill and hill
  defends were all loaded, all coverage-measured, and all unused. Fitted, backtested
  and compared here; **not yet the published version.** Promoting one changes what the
  site's leaderboards mean, which is a decision this page records, not a
  consequence of a feature set existing.

No version declares which titles it applies to. Every feature names the source columns
it reads, and a cohort keeps a feature only if its title actually populated those
columns, measured from the data on every run. That is why the feature sets below
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
2.1.0 cohorts fall back to exactly the 2.0.0 set instead of being fed zeros. An
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
not have that problem: it is a share of a player's own kills, and correlates 0.13 to 0.39
with the kills differential in Search & Destroy. It is the column that gave **2022-2025
Control a beyond-the-gunfight ratio** before the published version had one; those seasons
now carry assists and have a ratio without it.

**2021 Control still has none, and that is a fact about the season, not a gap.**
Non-traded kills and the Control round counts are declared and empty for Black Ops Cold War,
it records no assists at all, and damage belongs to the slaying pair. There is nothing beyond
the gunfight in that box score to report.

**Contested hill time is the one recovered column that is a new axis, not a sharper
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
already reaches the model where it belongs, as the round denominator derived from it, and
as a feature it would have put the coin toss in the rating.

**What the new columns cost, since a rate needs a denominator on both sides.** Eight of 737
WWII Capture the Flag maps and one of 256 Black Ops 6 Control maps leave the design because
one team recorded no shots, or no kills, so accuracy or a per-kill share cannot be formed for
that side. A half-measured map is not an observation, so it is dropped, not imputed;
the same nine maps also leave the four-way version comparison, which is why the earlier
versions' comparison numbers move very slightly while their ratings do not move at all.

**2.2.0 also loses the map backtest, and that comes first, not last.** Over the
13,876 maps all four versions predict, Brier goes 1.0.0 0.06354, 2.0.0 0.05710, 2.1.0 0.05696,
**2.2.0 0.05769**, a small regression, losing in 19 of 41 cohorts. Two things about that,
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

Where a new column is a genuinely new axis, not a rival reading of an old one, it wins:
2024 Control improves by 0.0048 and 2023 Control by 0.0029, the two cohorts where non-traded
kill share arrives into a set that previously held nothing but kills and deaths.

This is one reason 2.2.0 is not the published version. The other is that promoting a version
is a decision about what the site's leaderboards mean, and a feature set does not earn that by
existing.

The denominators fork with the era, because map duration is the one column the CDL source
does not carry: Search & Destroy is per round throughout, while Hardpoint and Control are
per 10 minutes in the CWL years and per map in the CDL years. Every feature declares its
denominator as a source, so a rate whose clock a title does not record resolves to its
per-map twin instead of quietly emptying the cohort, which is what it used to do, and is
why the CDL Hardpoint and Control rows are here at all.

**One family is deliberately excluded.** The kill-feed tier can also measure rounds won
while up a man, and clutch wins. Neither is used as a rating feature, because both
contain the round outcome, and round wins are what decide maps. Regressing map wins on
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
That was the undeclared-denominator defect, not a fact about 1.0.0: a rate resolves
to its per-map twin where there is no clock. With it fixed, all three versions predict
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
much smaller gain again, and a fair reading is that it is close to a wash overall. It is
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
discover anything about Capture the Flag. It stopped dividing the win condition by a
nuisance variable. That is a units fix on a leaked column, and because CTF is 667 of the
9,202 maps it carries a visible share of the headline above. The kill feed helps in
exactly one place, WWII Search & Destroy, where trades decide rounds, and slightly
*hurts* WWII Hardpoint. Control is the cohort where the box-score model is barely beaten
in either era: in 2019 with only first-blood net and captures available 2.0.0 has nothing
to add, and after 2020 the two later versions are *identical*, because with no captures
column in the source there is nothing for a later version to be made of. The CDL
Hardpoint gain is real but small, 0.0420 to 0.0411, where the CWL-era gain came from
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
is the same story one step weaker. Hill occupancy is the Hardpoint score, up to
teammates standing on the hill at once, and it also beats the model outright.

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
rating run, so it is measured on every rerun, not transcribed once. Its consequence
is accepted, not patched over: the version comparison above is not evidence of
predictive quality. The two tests that *are* out-of-sample follow, and the rating does not
come out of them well.

### Two tests the rating can fail

Both predict something that has not happened yet, so no feature can contain its answer.

**Does the rating persist?** For every player with two consecutive seasons and at least
eight maps on each side, season *N* predicts season *N+1*. Two predictors (the composite
rating and the era-adjusted K/D z) against two targets, the same pair one season later.
The 2×2 is deliberate: predicting next season's rating flatters the rating, predicting
next season's K/D flatters K/D, so the off-diagonal is where the question actually lives.
566 transitions across nine season boundaries, from 100 (IW → WWII) down to 41 (BOCW → VG),
Pearson *r* with a 2,000-draw bootstrap over players.

| Predictor (season *N*) | → next rating | → next K/D z |
|---|---|---|
| Composite rating | 0.49 [0.42, 0.55] | 0.34 [0.26, 0.41] |
| Era-adjusted K/D z | 0.43 [0.36, 0.49] | **0.55** [0.49, 0.62] |

The contrasts are paired: the same resampled players score both predictors, because
comparing two intervals that happen to overlap answers nothing. Predicting next season's
K/D, Δ*r* = −0.22 [−0.29, −0.15], which excludes zero: **K/D z predicts a player's future
K/D better than the composite rating built on top of it, decisively.** That is the same
verdict this test returned on the CWL archive and on every population since, in the same
direction and at a similar size.

The other column has moved, and the earlier version of this page overstated it. On 216
CWL-era transitions the rating also lost at predicting *its own next value*, by Δ*r* =
−0.08 with an interval spanning zero, and the summary here read "raw K/D z is the better
predictor in both columns". On 566 transitions the sign reverses: Δ*r* = +0.06
[−0.00, +0.13], still spanning zero. The correct statement is the one that was true of
both samples: the two predictors are indistinguishable at forecasting next season's
rating. "Better in both columns" was reading a point estimate inside its own
interval. Only the K/D column separates, and it separates in the direction that is bad
news for the rating.

**These figures moved, and for two years nothing was checking that this page still matched
the run.** Until [the evaluation harness](#the-evaluation-harness-what-a-rating-has-to-beat-declared-in-advance)
below was built, this section carried 541
transitions and Δ*r* = −0.26 from a run that predates the identity merges, the plus-minus
lineup rule, the fourth feature set and the mode recovery that added the last two
transitions. The pre-flight section further down this
same page already quoted the current count. Every verdict here survived the refresh; the
magnitudes did not, and the largest single move is the rating predicting its own next
value, 0.32 to 0.49. The numbers on this page are now pinned in the harness and a run
that disagrees with them fails the release gate.

**Does a roster predict future map wins?** Walk-forward by event within each season: at
every event the whole rating pipeline is refit on maps from earlier events only, each
team's players are averaged into a roster strength for that map's mode, and the
differential becomes a win probability through a logistic also fit on those earlier maps.
Nothing from the event being scored enters. The population is the published one, floored
at 2017 like every other figure in this section. 9,391 maps survive on which every
predictor has an opinion; 1,548 are skipped for having no history and 822 for having no
identifiable roster.

| Predictor | Brier | Log loss | Accuracy | vs. coin flip |
|---|---|---|---|---|
| **RAPM** | **0.24636** | 0.6952 | 58.9% [57.9, 59.9] | **−0.0036** [−0.0073, +0.0001] |
| RAPM, rating-centered | 0.24688 | 0.6971 | **59.2%** [58.2, 60.2] | −0.0031 [−0.0069, +0.0008] |
| Roster composite rating | 0.24763 | 0.6913 | 56.6% [55.6, 57.6] | −0.0024 [−0.0054, +0.0007] |
| Same rating, z-and-shrink | 0.24850 | 0.6941 | 56.7% [55.7, 57.7] | −0.0015 [−0.0046, +0.0016] |
| Glicko-2 team rating | 0.25006 | 0.7055 | 59.2% [58.2, 60.1] | +0.0001 [−0.0038, +0.0041] |
| Roster K/D | 0.25181 | 0.7038 | 56.5% [55.5, 57.5] | +0.0018 [−0.0014, +0.0050] |
| Coin flip at 0.5 | 0.25000 | 0.6931 | — | — |

**None of these separates from the coin flip. RAPM comes closest, at −0.0036 [−0.0073,
+0.0001], and its interval reaches zero.** An earlier version of this page read RAPM's
interval as excluding zero, on a fit drawn before the 2026-08-17 identity pass merged two
player rows and two team rows; the reading moved from "resolves, barely" to "does not
resolve" and has stayed there. The composite rating's own gap is −0.0024 [−0.0054,
+0.0007] and spans zero, which is what the CWL archive said before it.

The qualifier is the same one as everywhere else here, and it now applies to every row.
RAPM's −0.0036 sits under the 0.0053 that 9,391 maps can resolve, so the size of the effect
was already below what this test can see. "Roster strength forecasts map wins slightly
better than a coin flip" is the direction every predictor points; this table does not
establish it.

One contrast does clear both tests, and it is the one that most directly answers what the
composite rating is for: **against roster K/D, the rating wins by −0.0038
[−0.0058, −0.0017], against a threshold of 0.0029.** On the CWL archive that contrast was
−0.0034 [−0.0072, +0.0003] and unresolved. The rating built on top of the box score does
forecast map wins better than the box score's own headline number. That is notable
precisely because [the persistence test above](#two-tests-the-rating-can-fail) says the
opposite about forecasting a *player*.

Against Glicko-2 the rating is −0.0030 [−0.0067, +0.0008]; unresolved.

The fourth row is the same rating estimated the old way, and it is here because
[the rating is a posterior](#the-rating-is-a-posterior) needed a test, not an
argument. Paired on identical maps the posterior wins by −0.00095 [−0.00180, −0.00010],
which excludes zero and still sits under the 0.00126 this sample can resolve, and the pick
rates are a coin-flip apart. Read as "the better-specified estimator does not cost anything
out of sample", which is the most this test could have established either way.

The blend is no longer the best row in the table; that reversal is discussed under
[plus-minus](#plus-minus-value-in-wins-without-the-box-score) below.

Brier and accuracy still disagree, and reporting either alone would mislead, so both are
published. Every predictor picks the winner more often than chance: the rating's 56.6%
interval clears 50% comfortably. Roster strength carries directional signal. What it
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
any point, which is what makes it an independent check and not another view of the same
data. 11,575 decided maps, 265 players with at least 20 of them.

Two things have to be reported, not assumed away, and together they decide how much
the leaderboard means. Both have improved substantially with the CDL era: franchised rosters
change more often than CWL ones did, and roster churn is
exactly what breaks the collinearity this method suffers from.

**Collinearity, and it is still severe, but no longer disabling.** Four players who never
appear apart are one column wearing four names; ridge responds by splitting the credit
evenly, which is correct and is also indistinguishable from a finding. So every coefficient
is published beside that player's *teammate concentration*: the share of their maps spent
alongside their most frequent teammate. The median is **0.68**, down from 0.81 on the CWL
archive alone, and **90 of 265 players sit at 0.9 or above**, a third, where it was 44%.
None of the top five coefficients now belongs to a player at concentration 1.00; two of
the five clear 1.96 standard errors with a concentration below 0.9.

**Shrinkage.** Standard errors come from the penalized Hessian and are published with every
coefficient. The median is 0.31 against a coefficient spread of 0.42, and **53 of 265
coefficients exceed 1.96 standard errors**, where on the CWL archive it was 7 of 196, with
a median standard error larger than the whole spread of the estimates. The ridge path still
says the penalty is doing real work: as it rises from 0.25 to 64 the spread of coefficients
collapses from 0.57 to 0.10 and the ordering's correlation with the lightest fit falls to
0.56. Nothing here tunes that penalty against the held-out maps. Doing so would turn the
forecast above into a selection statistic, not a test.

**The blend, and its verdict has reversed twice.** A natural extension is to use the box-score
rating as an informative prior on RAPM, which is a one-line change to what the penalty
shrinks toward: instead of zero, each player's coefficient is pulled toward their composite
rating converted into map-win logits, at an exchange rate estimated on the training maps
, not assumed. The blended coefficients correlate 0.993 with plain RAPM. On the CWL
archive the blend was *worse* on Brier (0.24601 against 0.24467) and better on accuracy,
and this page reported a mixed result and declined to adopt it. On an earlier cut of the
full record it was better on both, and this page said so. On the current one it is mixed
again: Brier 0.24688 against plain RAPM's 0.24636, accuracy 59.2% against 58.9%.

**A verdict that has moved three times on samples this close together is the finding.** The
two arms are 0.0007 of Brier apart on predictors that correlate at 0.993, which is well
inside what this sample can resolve: the paired contrast is +0.0011 [−0.0022, +0.0041]
against a threshold of 0.0047. A 0.2-point accuracy difference is nothing. The
supportable statement is that these two are indistinguishable out of sample and that the
sign of the difference is not stable to a change of population. The published RAPM stays
the plain fit, because "shrink toward the box score" is the assumption this whole section
exists to avoid making, and nothing in these numbers forces it.

**What RAPM is actually measuring.** At a median teammate concentration of 0.70, a player's
coefficient is still substantially their lineup's. That explains the shape of
the table above: RAPM's accuracy (59.0%) lands much closer to Glicko-2's team rating (59.1%)
than to the box-score rating's (56.6%), and it does so while never being told which team is
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

What no longer holds is the flat statement that it forecasts nothing, though less of it
survives than an earlier version of this page claimed. On the CWL archive every predictor's
gap over the coin flip spanned zero and the correct summary was "not a forecasting tool".
On the current record only plain RAPM's gap excludes zero; the composite rating's own does
not. What does still hold, on an interval and a power threshold, is that the rating beats
roster K/D at forecasting map wins. The site does not present the composite rating as a
forecasting tool, and should not: the effects are a few thousandths of Brier, the one gap
that resolves does not clear its own power threshold, and a measure that predicts a
player's own next season worse than raw K/D does has not earned that framing.

Two directions were named here as ways to change the verdict, and both have now been
tried. The round-level model in Tier 1d works as a model of a round, but
the player value derived from it, win probability added per kill, turns out to be kill
rate in another unit, and the part that is not kill rate does not reproduce across a
player's own games. Plus-minus does better: RAPM posts the best Brier in the table and a
clearly above-chance pick rate without touching the box score, but its gap over the coin
flip no longer excludes zero — −0.0036 [−0.0073, +0.0001] — and it does not clear what
9,391 maps can resolve either. Its coefficients remain entangled with lineups, if less so
than before.

Player-level information does appear to forecast map wins slightly better than the
composite rating does, and RAPM against the rating directly is +0.0013 [−0.0021, +0.0046]:
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
teammate concentration**. For the first time the method resolves a meaningful number
of individuals, not just a handful of duos. On the CWL archive alone that count was one.
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
here before any such model is fitted, not after, in the `rapm_identification`,
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
directions are available and 427 are realized. 2021 is typical: 63 player columns and 12
team columns, 38 lineups, rank 37. The 761 missing directions are not weakly identified.
They are not identified at all, and any number a penalty puts there is the penalty's.

**Which is visible per column, too.** For each column, the share of its posterior variance
supplied by the penalty, not by the data, is λ·[(XᵀX + λI)⁻¹]ⱼⱼ. It cannot reach 1: a
player whose four-man lineup never changes shares one identified direction with three
teammates and a team column, so their share sits near k/(k+1): 0.80 at 4v4, 0.83 at 5v5.
Against that reference, **42% of CDL player-season columns and 53% of CWL ones are penalty
dominated**, where the career design's figure is 9%. A flat threshold of 0.9 would have
reported no problem anywhere, which is why the reference is derived, not picked.

**A simulation says what that costs.** A generated league is handed to the estimator the
phase would use: known trajectories with peaks, an explicit team-season effect, roster churn
as a dial, mode margins censored at the real caps of 250, 3 and 6, and map noise solved so the
league is exactly as predictable as this one. Recovery is scored on deviations
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
*r* = 0.50, every point of it imported from other seasons by the random-walk penalty.
That is the failure mode this pre-flight exists to price, measured, not argued.

The last row matters too, and it is not good news. **More roster movement than any real
team-season has still tops out near 0.33**, so the binding constraint is not only churn: a
map in this league is close to a coin flip, and the noise the calibration matches (the map
model picks 59.5% of maps) leaves little room to resolve one player inside a lineup however
often the lineup changes.

Two further numbers come from the same harness. The penalized-Hessian intervals cover the
truth at **91–98%** against a nominal 95%, so they are near enough nominal to use. And a
two-sided penalty scored on a forward test beats the one-sided fit by **+0.026** of
correlation while predicting a season it has already seen: small, real, and exactly the
contamination that makes the smoothed and filtered families a distinction, not a
preference.

**The verdict, against thresholds declared before the measurement.** The rule stops a
season-varying plus-minus only if all three hold: the median team-season carries under 1.5
effective lineups, the design identifies under half its player columns, and the simulation
fails to separate teammates at that lineup variety. The two eras do not answer alike:

| | Effective lineups | Rank / player columns | Simulated recovery | |
|---|---|---|---|---|
| CDL 2020–2026 | 1.94 | 48% | 0.29 | two clauses, does not stop |
| CWL 2017–2019 | 1.00 | 41% | −0.06 | all three, stops |

So the time axis is as fine as each era can carry: **season resolution for the CDL era,
published as deviations from an explicit team-season effect, and pooled to era level for
2017–2019**, where within-season lineup variety identifies nothing. A rank-deficient design
that still recovers what was put into it is not a failure. That is what a penalty is for,
which is why the rule is a conjunction and why the CDL era, deficient on rank alone, ships.
But 0.29 is weak recovery, not good recovery, and the CDL season coefficients this permits
should be read as what they are: a noisy deviation from a team, not a ranking of four
players.

**And the forward test this feeds has less power than the plan assumes.** The record holds
**561 consecutive player-seasons**, against which the smallest detectable gap in next-season
persistence (over a baseline *r* of 0.564) is **0.08**. That is a floor: it assumes
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
that resolution out of the verdict artifact instead of declaring it, so an era that grows
into season resolution gets it without a code change. The `rapm_season` artifact and the
`player_rapm` rows it summarizes are stored under their own run.

**The model.** One row per map, +1 for the four players on one side and −1 for the other,
expanded to one column per (player, time cell), plus a team-season column on each side.
Fitted by generalized ridge:

```
minimize  ‖y − Xβ‖²  +  λ₀ Σ β²_{p,t}  +  λ_w Σ (β_{p,t} − β_{p,t−1})²
```

The second term is a Gaussian random walk on player value: the state-space formulation
written as a penalty. Both λ are chosen by **generalized cross-validation** with the
hat-matrix trace computed exactly, never by searching against held-out maps, which would
turn the backtest into a selection statistic. On this record that lands at **λ₀ = 32.8,
λ_w = 5.2**, spending **256 effective degrees of freedom** on 1,537 columns: 1,141 player
cells, 388 team-seasons and 8 replacement buckets.

**The response is the map's score margin, rank-transformed to normal scores within (season,
mode).** Margin carries far more per row than a binary win, and it is censored, not
merely heteroskedastic: Hardpoint runs to 250, Control to 3, Search & Destroy to 6. So the
raw number is non-linear in value at exactly the tail where the best players live, and ranks
survive the cap. 11,571 of the 15,830 admitted maps carry it, and the 4,259 that do not are
**named by game id in the artifact**, not counted and forgotten. Almost all of them are
pre-2017: the wiki transcribes a scoreboard, not a score, so a 2014 map records who won and
not by how much. Four are from the two publisher archives. Two of those are 2017 Uplink maps
level at regulation with a winner the archive knows, which is not an error; the other two
are a Search & Destroy map recorded at 3-6 to the team that won it, and a Control map whose
scores are both −1, a sentinel for "unknown" wearing the type of a real number. Neither is
fixable from inside this repository, so both are declared here, reported by the ingest
quality checks, and excluded from the signed targets while staying in the binary one. Binary win and mode-standardized margin are fitted from the same factorization and
published as sensitivity, never averaged in: their coefficient orderings correlate **0.913**
and **0.982** with the published one.

**Three population rules, not two.** *Column admission*: below 8 maps in a cell a player
does not get a column and the map is not dropped: the slot joins a shared replacement
bucket for that cell, because dropping the map would discard a real result and bias the fit
toward teams whose opponents happened to be established. **21 players** are pooled this way,
into 4 buckets, and the buckets' own coefficients are the directly interesting number: what
a replacement-level slot is worth. They run from **−0.02** (2021, 2022) to **−0.24** (2026,
SE 0.08). *Fit inclusion*: every admitted cell enters however thin, which is what the walk
penalty is for. *Publication*: a higher floor of 20 maps, which leaves **1,010 published
player-cells** in 2,020 rows over two scopes.

**Two coefficient families, and only one may be read forward.** The walk penalty is
two-sided: β at 2023 is pulled toward 2024 as much as toward 2022. So a `smoothed`
coefficient has already seen the season it would be asked to predict. That contamination
arrives through the penalty, not through a column, so nothing written against the
design matrix can detect it. `filtered` refits through each cell and nothing later, and a
forward test reading a smoothed row raises instead of warning. The two families correlate
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
reports, and it is a genuine result: half the record predicts the other half at 0.50. But
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
directions. That is not a defect to be fixed by a better solver: it is the league's
schedule, and it is why the penalty exists and why these numbers are published as noisy
deviations from a team, not as a ranking of four players.

**The penalties, reported, not selected.** Moving λ₀ over 4× either side of the
chosen value takes the coefficient spread from 0.106 to 0.043 and the ordering's correlation
with the published fit down to 0.94–0.96, while GCV moves in the fourth decimal (0.8504 to
0.8548). The criterion is nearly flat and the ridge dial is doing most of the shrinking.
The λ_w = 0 row, which is the fit with no time-borrowing at all, correlates **0.988** with
the published one: at this record's churn, the walk term is a modest smoother, not
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
| Logistic on map win, with the team-season effect (the like-for-like arm) | 0.784 |

Read the middle row against the other two: **the team-season effect moves the ordering more
than the link function does**. That is the change of interpretation stated plainly. Without
a team column, team quality has nowhere to go but into the four player columns and ridge
divides it evenly: "this was a good team" is published as "these were four good players".
With it, a player's number is what is left after their team-season is accounted for, and the
players whose published rank it moves most are the ones whose old coefficient was mostly
their roster's.

So: this is a time axis the record supports at the resolution measured for it, reliable at
*r* = 0.50 within a cell, and entangled with lineups to the point where the median player is
inseparable from a teammate. It is published with its standard error and its penalty share
on every row, and it is not a ranking of the players on a roster.

### Opponent adjustment: what the box score owed to who was across from it

Until now nothing in the published stack corrected a stat line for the strength of the
opposition that produced it. A player's cohort z-score treated a line farmed against the
bottom of an open bracket identically to one earned against the eventual champion, which is
the first thing anyone says about K/D and the most-cited omission in the game's own analyst
community.

**What is adjusted, and what is not.** The plus-minus already conditions on opposition: the
opposing four *are* the −1 columns of its design. Nothing here touches it, and reading
this section as "the plus-minus was opponent-adjusted" would double-count the correction.
What is adjusted is the **box score**: the per-map rates every published per-player statistic
is built from.

The unit is the (numerator, denominator) pair, not the rate, because the metric layer
and the rating both sum numerators and denominators across maps and divide once. So one
observation is a player-map's numerator over its denominator, weighted by that denominator,
and an adjusted season value is Σ adjusted numerator / Σ denominator. Exposure weighting falls
out of that instead of being bolted on: a map that ended early carries less of the fit.

#### Four rungs, and the rule for stopping

No single implementation is picked in advance. The ladder is fitted and the leaderboard
movement reported at each rung, in cohort standard deviations. 141 cohort-features over 36
cohorts:

| Rung | What it conditions on | Median move | Placebo ratio | Reliability vs raw |
|---|---|---|---|---|
| `team_rating` | the opposing **team's** walk-forward Glicko-2 | 0.025 | — | +0.0003 |
| `lineup_fe` | the opposing **lineup**, as fixed effects | 0.113 | 1.55 | **−0.0057** |
| `pooled_context` | the same, pooled, with teammates entering | 0.058 | 1.85 | +0.0047 |
| `shrunk` | empirical-Bayes shrinkage of the season values | 0.123 | — | — |

Three criteria, declared before anything was fitted. A rung has to **move** the leaderboard
against the rung below it by at least 0.01 cohort standard deviations; what it moves has to
stand clear of its own **placebo**, which permutes which lineup each line faced and refits, at
a ratio of at least 1.5; and it must not leave the statistic less **repeatable**, measured as
split-half reliability on whole series against the unadjusted number.

**The ladder is not monotone, and the rule does not assume it is.** The two-way rung moves the
most and comes out *less* repeatable than the raw number: it overshoots, fitting schedule
noise. The pooled rung above it repairs exactly that at a better placebo ratio. A
climb-until-failure rule would have stopped at the cheap rung and discarded the one that works,
so each rung is judged on its own and the adopted one is the highest that clears all three.

#### The cheap rung is blind where the disparity is largest

Residualizing on a team rating needs the team to have a rating. **7.0% of lines face an
opponent still sitting on Glicko-2's 1500 prior**, a team that has played nothing the rating
could have learned from, and a further 550 face a team the rating never reached. That is not
spread evenly: it is **100% of the 2017 season**, whose only event opens the CWL archive,
and it concentrates again wherever an era's first event is.

So the rung the plan proposed as the cheap baseline has nothing to say about precisely the
era where the competitive spread is widest. The rungs above it estimate opponent quality from
the lineups themselves and do not have this problem: the CWL era's median correction at the pooled
rung is roughly nine times what the team rung finds there.

#### Two facts about identification, stated, not discovered

*The coefficients are not identified; the correction is.* Every row carries one own-player
column and four opponent columns, so adding a constant to every own effect and subtracting a
quarter of it from every opponent effect leaves every fitted value unchanged: the same
minimum-norm shift the plus-minus has, one level down. Measured on a typical cohort, **31 of
115 columns carry no separable direction at all**. The correction subtracts the opponent
contribution *centred on its cohort*, and a uniform shift moves every line's contribution by
the same constant, so it cancels. Coefficients are published under an explicit sum-to-zero
convention; the correction is invariant to it.

*Kills are zero-sum, so the two blocks are estimated from the same events.* A kill one player
takes is a death another takes, which makes the correction circular: a player's own lines help
estimate the opponent effects that player is then adjusted for. The opponent block is therefore
**cross-fitted over five folds cut on whole series**, so every line is adjusted by effects
fitted without its own fold. The gap between the in-sample and cross-fitted corrections is
published per cohort and is not small: for the 2018 Hardpoint slaying columns it is roughly
half the correction's own spread.

Leave-one-series-out was the first design and is not usable on this record: with 31 columns
already unidentified, removing a single series unidentifies more, the exact downdate divides
by a singular matrix, and the correction inflates by a factor of forty. That is a property of
the schedule, not of the arithmetic: the downdate itself is exact and is tested against
explicit refits.

#### The controls

**Placebo.** Permuting which lineup each line faced and refitting leaves a correction 1.52×
smaller than the real one at the two-way rung and 1.69× smaller at the pooled rung. Both clear
the declared threshold, and neither clears it by much: a real share of the raw two-way
correction is the design fitting a schedule that carries no information. This is the single
most important number in the section, because without it the two-way rung's much larger
movement reads as a much larger finding.

**Positive control.** A synthetic league with known per-player offensive and defensive effects
and a randomly paired schedule: the rung recovers the planted opponent effect at *r* = 0.993
with a slope of 1.00. The machinery is correct, and the limits on real data are the schedule's
, not the estimator's.

**Shape.** Three assumptions the cheap rung makes were tested, not asserted. Against a
four-bin step function of the same rating the straight line loses nothing (weighted residual
ratio 1.001 at the median, 1.007 at worst), so linearity holds. Map duration adds nothing the
denominator has not already absorbed. And the slope does not differ between players above and
below their cohort's median (median |t| of 0.77), so one additive correction is the right
object, not a role- or level-dependent one.

**Connectivity.** Every one of the 28 cohorts' opponent graphs is a single connected component
with no bridges, so no correction anywhere in this section compares two players the schedule
never linked. A null, and worth stating: it was a live risk in the open brackets.

#### The size of the correction

Per line, opposition is worth a great deal: its standard deviation is **0.500 cohort standard
deviations in the CDL era and 0.497 in the CWL era**, and the 95th percentile is above a full
standard deviation in both. A map against the top of the table and a map against the bottom
are genuinely different maps, and every bootstrap interval (200 draws resampling whole
series, over the headline slaying columns of every cohort) excludes the 0.01 threshold. The
correction is real and it is measurable.

**Over a season it very nearly cancels.** Averaged across a player's schedule the mean
correction is **−0.00003 sd for the CDL era and +0.00062 sd for the CWL era**: zero to three
decimal places on both sides of the seam. Schedules are close enough to balanced that
opposition strength averages out of a season total almost exactly.

#### Was any CWL-era open-bracket reputation built on soft fields?

The gate for this phase names that question, and the answer is **no**.

An event's label is not used. The amount the adjustment removes from a line *is* what the
opposition was worth on it, so averaging that over an event measures softness directly. Over
every event with at least 200 lines, the largest average is **0.114 cohort standard
deviations**, and the softest fields are not the CWL open brackets at all. They are CDL
events: `CDL Major 1 Qualifiers` at −0.114, `CDL Major 4` at +0.112, `CDL Major 1` at +0.109.
The highest-ranking CWL entry is `CWL Pro League 2018 Relegation` at +0.095, which is a
relegation bracket, not an open one.

At the player level the same holds. The most schedule-affected player-season in the archive is
worth **0.25 cohort standard deviations**, over 107 lines, and the rest of that list sits near
0.18. Several entries are four players from one roster carrying an identical schedule, which
is what a team-level effect looks like from the player side.

So the widely-assumed asymmetry is not in this record. Open brackets do contain lopsided maps,
which shows up clearly in the per-line spread, but a player's *season* is not meaningfully
inflated by them, because nobody plays enough of a season against the bottom of a bracket for
it to survive averaging. Published as a null, which is what it is.

**What this does not license.** The correction is not promoted into the published per-player
statistics here; the site continues to show unadjusted numbers, and changing that is a
lockstep change across two languages that belongs with the publishing work. What is stored is
the ladder, its controls and its verdict, as a versioned run.

### Match context: the venue, the stage and the map

Four columns describe the circumstances of a map and, until this phase, no analytics module
read any of them: whether the match was played on a LAN stage or online, what round of what
bracket it was, which map it was, and what the event paid. A player's cohort z-score treated a
qualifier map played online as the same kind of event as a grand final on a stage.

**What is adjusted is the box score**, on the same terms as the opponent ladder: the plus-minus
already conditions on who was on the server, so nothing here touches it. The design holds the
line's own player and the opposing lineup fixed, so a coefficient below is what survives both.

**Every term is an adjusted association, and none of them is a cause.** "Venue-associated
deviation" is what the data supports. "Plays better on LAN" is not, because who attends a LAN,
which teams qualify, and which stage is played there are all selected.

#### The venue flag had to be fixed before it could be used

`events.is_lan` was once two assertions wearing one column: the CWL archive importer stamped
`true` on everything it created, and the Liquipedia loader mapped a tournament `type` it only
sometimes had. The derivation is now stated: a curated verdict, else Liquipedia's tournament
`type`, else undecided. And **`location` is never consulted**, because nine of the 2020
regular-season weeks kept their host-city branding after March 2020 moved them online. A venue
string is what an event was called, not where it was played.

Nine CWL open events carried curated verdicts on the grounds that Liquipedia had no page for
them. It does: the tournament pull was scoped to the premier circuit and all nine are tier 2.
Pulled at every tier, each of the nine returns `Offline` with a named venue and a start date
matching the event exactly. The curated file is now empty, and every published venue label
derives from the source.

#### What the record can be asked

The two eras are not symmetric, and this bounds every claim in this section.

| | LAN lines | Online lines | Undecided |
|---|---|---|---|
| CWL (2017–2019) | 43,766 | 0 | 0 |
| CDL (2020–2026) | 15,904 | 32,906 | 480 |

The CWL era carries **no venue contrast at all**. Every LAN/online comparison here is
identified inside the Call of Duty League era, and era and venue are perfectly confounded
across the seam between them.

Inside that era, venue is half a stage term: LAN is where the Major bracket is played and
online is where the qualifier is. Regressing the venue flag on the stage classes returns
R² ≈ 0.51, leaving a residual standard deviation of 0.33 against 0.47 raw. The question is
answerable, at roughly twice the variance the raw split suggests.

#### The ablation table, declared before anything was fitted

One row per feature family, each judged on two numbers: how far it moves the leaderboard, in
cohort standard deviations, and whether it lowers out-of-fold error on the per-map rate over
five folds cut on whole series. A family that moves the table without predicting is fitting its
own noise. The families that did nothing are published as families that did nothing.

| Family | Median move (cohort sd) | Median Δ out-of-fold RMSE | Cohorts improved | Verdict |
|---|---|---|---|---|
| `venue` | 0.0000 | 0.00000 | 10/60 | dropped: does nothing either way |
| `stakes` | 0.0078 | 0.00000 | 19/60 | dropped: does nothing either way |
| `elimination` | 0.0027 | +0.00001 | 9/60 | dropped: does nothing either way |
| `prize_pool` | 0.0167 | −0.00001 | 31/60 | kept, by one cohort |
| `host_team` | 0.0000 | 0.00000 | 6/60 | dropped: does nothing either way |
| **`map_identity`** | **0.0931** | **−0.12088** | **51/60** | **kept** |

**`prize_pool` changed verdict on 2026-08-21, and the reason is a data load rather than a
result.** The 2013-2016 events carried no prize pool at all until the wiki's was loaded
onto them, so in those cohorts the column was a constant and could not lower any error.
With the pools on, the family goes from 21 of 60 cohorts improved to 31, which clears the
0.5 share declared before fitting by a single cohort.

Read the size beside the count. The median improvement is 0.00001 of a per-map rate, and
almost all of the gain is in two pre-2017 Hardpoint cohorts, Black Ops 2 in 2013 at
−0.148 and Black Ops 3 in 2016 at −0.019. The rule counts cohorts and does not weigh
them, so a family can pass it on a margin this thin. The rule is not being changed after
seeing that, because a threshold rewritten once the result is visible is not a threshold.
What is published is the verdict the declared rule returns, next to the effect size that
says how little is behind it. No published number moves either way: these verdicts are
reported, and no family is applied as a correction to any box score.

`prize_pool` was predicted in advance to be event tier under another name, and it is. So are
four of the other five. On the shorter record three of the five moved the table without
predicting anything; on this one none of them moves it either.

**Map identity is the one family that earns its place**, and it is fitted as a random effect,
not as one dummy per map: the rotation changes every title, several maps carry only a
few hundred rows, and the aim is a statement about a map, not about the maps that
happened to be in one season's rotation. Each map's deviation is pooled by empirical Bayes
against its own precision. Hill time on one map is not hill time on another, and the cohort the
rating standardizes within, season by mode, averages over the whole rotation.

#### The LAN effect, per player: a null

Each player's LAN-minus-online deviation is pooled the same way. The interval is placed around
the **cohort's common venue effect**, not around zero: shrinkage pulls every player toward that
common value, so asking whether a player's pooled effect differs from zero asks whether the
cohort's does, and answers yes for every player at once. The question a per-player finding can
answer is whether this player differs from the others in the same cohort.

Of **1,278 player-cohort-features over 133 players, 10 clear their 95% interval.** Chance alone
would put about 64 outside it. A player needs eight balanced maps, at least three on each side,
to be estimated at all.

**The online warrior is not in this record.** This is not a case of a small effect. The players
cannot be told apart in how they carry between the two venues.

#### The home-market effect

Several Majors are played in a competing team's home market, so a home advantage is testable.
No source carries a team's home city: Liquipedia records a region for 457 of 458 teams, and an
event's organizer is the publisher on every league event. The map from venue to franchise is
therefore hand-curated, and it is published with a reason and a confidence for each of its 33
entries: 21 where the venue is in the franchise's own city and 12 elsewhere in the same
metropolitan area. Six of the 33 are neutral sites, named as neutral instead of left out; an
absent event and an event with no home team are different claims. Teams are named as they were
branded that season, so a franchise that
moved is not credited with a market before it moved there. One consequence: the 2023 Raleigh
Major has **no** home team, because the Royal Ravens were still London-branded that year.

**6 of 24 cohort-features clear their interval**, on a flag set for one team at one event, and
the family does not survive the ablation. The large coefficients sit on a few dozen lines each.
Reported as it landed.

#### What this costs the cross-era claims

Era and venue are perfectly confounded across the 2018/2020 seam, and no fit can separate them.
The mitigating finding is that the venue effect is a null in the era where it *is* estimable, so
the seam is a gap in what can be tested, not a known bias of a known size. Any career
number spanning it inherits that sentence, not a correction factor.

### The evaluation harness: what a rating has to beat, declared in advance

Everything above this point was scored by code written after the model it scores. That is the
normal order and it is the wrong one: a harness written afterwards is written by whoever wants
the model to pass, and every choice inside it (which test is the headline, which resampling
unit, which comparison gets an interval) can be made once the answers are already visible.
The next planned change to the rating is a large one, so the harness for it was built first,
against nothing, and committed before there was a model to run through it.

**The declaration is hashed.** One primary test, a labelled secondary set, the metric, the
resampling unit per family, the bootstrap seed, and the one coefficient family a forward test
is allowed to read. The manifest's SHA-256 is pinned in the source; a run whose manifest does
not hash to the pin fails the release gate. Relaxing a unit or swapping the primary test for one
the model passed both move that hash, which is the difference between declaring a test and
describing one afterwards.

**A declaration can be extended, and that is a different act from editing one.** The first
version pinned a single hash over the whole thing, which left no legal way to add a predictor
later. A rule with no legal path through it gets edited, at which point it stops being a
rule. So the declaration is now in two halves. What the test *is*: the target, the baseline,
the statistic, the resampling unit, the seed and the rule that computes the threshold, hashes
separately and never moves. The list of predictors may grow, and only grow: every superseded
version stays in the file with its own hash, and the release gate checks that each new list
still contains the last. A predictor can therefore be named before the model that produces it
exists, which is how the next rating was entered into this test in advance; until it is fitted,
the run reports it by name as declared-and-unfitted instead of leaving a silence.

**The primary test is next-season persistence on the baseline's own ground.** Season *N*'s
rating is tested against season *N+1*'s era-adjusted K/D z, the off-diagonal cell of
[the 2×2 above](#two-tests-the-rating-can-fail). One test, chosen because a fifteen-test suite
with no declared primary is a licence to pick a winner afterwards. Everything else the harness
computes is labelled secondary in the stored payload and published without significance claims.

| Predictor, season *N* | *r* with next K/D z | Δ*r* vs. K/D z | Detectable at |
|---|---|---|---|
| Era-adjusted K/D z (baseline) | 0.559 [0.470, 0.634] | — | — |
| Composite rating | 0.340 [0.217, 0.443] | **−0.219** [−0.305, −0.145] | 0.113 |
| `openskill` player rating | 0.098 [−0.001, 0.203] | **−0.461** [−0.576, −0.333] | 0.139 |

Both gaps exclude zero and both exceed what this record can detect, in the losing direction.
566 transitions over 190 players, and none of them dropped for want of a predictor. This is
the three-way panel, which the harness keeps computing after a fourth predictor narrowed the
shared one, so neither set of figures restates the other.

**The threshold is computed, not chosen.** A gate declared below the smallest effect the record
can resolve can be failed by a model that works, so each predictor gets its own floor from the
[pre-flight's](#can-the-plus-minus-have-a-time-axis) closed form at the measured sample size,
the measured baseline correlation and that predictor's measured agreement with the baseline:
0.09 for the composite, which agrees with K/D z at 0.564, and 0.11 for `openskill`, which
agrees at 0.240. Then both are widened by the design effect the clustering costs, measured at
**1.312**, measured, not assumed.

**The next rating's floor is computed on the panel it will actually occupy, before it exists.**
A rating built on the season plus-minus can only be scored where a season-resolution coefficient
exists, which is the CDL era alone: **269 of the 566 transitions, over 90 of the 190 players.**
Fewer clusters is a higher floor, so the threshold that rating will be held to is not the 0.110
above but **0.175**, an independent floor of 0.11 widened by a design effect of 1.59 measured on
that narrower panel, well above the 1.312 the full panel costs. On it the composite loses by
0.258 against a baseline *r* of 0.630, so the rating has to move **0.433** in correlation to
clear a gate that says beat, not tie.

The same computation on the current record returns 0.1734, on 269 transitions over the same 90
players, and the rating would have to move 0.4076. That is reported beside the threshold and
does not replace it: 0.175 was written before the model existed, and a threshold recomputed
once the result is visible is not a threshold declared in advance. The plus-minus the panel
reads has moved four times: first when the 2013-2016 seasons entered the fit, again when the
recovered modes gave 2014 a rating and every pre-2017 season its Search and Destroy cohort,
again when ten identity merges put split careers back together and changed the lineups the fit
is built on, and again when two further merges did the same. Those last merges also grew the
panel by one transition, because a career the archive held under two gamertags is now one and
its season boundary is a transition the panel can read. A larger archive is not a better
result.
That number is stored in a run that precedes any run carrying the model it judges, and the
release gate checks that ordering, because a threshold written beside a result is not a
threshold declared in advance.

**The resampling unit is the series everywhere except here, and the exception is stated rather
than fudged.** Maps inside a series share a lineup, a day, a patch and an opponent, so anything
keyed by a map or a series resamples whole series. A persistence observation is not keyed by
either: it is a player-season transition assembled from tens of series, and no series contains
a whole one. The smallest cluster that does is the player, which is what the primary test draws
on. It is strictly coarser than the per-observation draw the published test uses. The
1.312 design effect above is exactly the price of the coarser draw, measured by running both
and dividing.

**A rating that never sees the box score is the adversary.** `openskill` (Weng-Lin /
Plackett-Luce) is the obvious thing to do with a record of 4v4 map results and nothing else,
and unlike the published Glicko-2 it rates *players*, so it can enter the persistence test at
all. It runs as a pipeline stage with its own run, artifacts and backtest row, because a
baseline in a hard gate that cannot be reproduced makes the gate unenforceable. Nothing about
it is tuned: library defaults, on the same eight-map qualification floor the era adjustment
uses: 11,609 maps, 340 players, 943 published player-seasons. Walk-forward it picks the map
winner 59.1% of the time and posts a Brier of **0.26416**, worse than always guessing 0.5. It
is the sharpest instance of the disagreement [the forecast table](#does-it-actually-predict-better)
already shows. A predictor can rank teams well and still be worse than useless as a
probability. Its persistence *r* of 0.04 says that knowing only who won tells you almost
nothing about who a player will be next season.

**The scope rule is the single most important line in the harness.** The season plus-minus
stores two coefficient families, and the smoothed one at season *t* has already seen *t+1*,
because the random-walk penalty is two-sided. A forward test that reads it is scored against a
target containing its own answer. The manifest names `filtered` as the only family a forward
test may read, the harness routes its read through the estimator's own check instead of
reimplementing it, and that check raises instead of warning. It is exercised on real
coefficients every run instead of lying dormant: the filtered season plus-minus reaches *r* =
0.196 against next season's K/D z over 269 season-resolution cells, against the baseline's 0.630.

**That figure was 0.291 over 553 cells until the resolution split was added.** A coefficient is stored against every season it covers, so an era-resolution
row (the CWL years, which the identification pre-flight never allowed a season on) files one
estimate under 2017, 2018 and 2019 alike. Pooling the two resolutions put 286 such rows into a
forward test as though they were 286 season estimates, when they are one number per player
repeated. Read apart, the era rows score 0.371 and the season rows 0.196: the pooled figure was
higher precisely *because* an estimate averaged over three seasons is quieter than one season's,
which is a property of the estimator's resolution and not of the plus-minus reading forward well.
All three are now published, and the season figure is the one that answers the question.

**Negative controls, three of them, run against the plus-minus every run.**

| Placebo | What it should say | What it says |
|---|---|---|
| Shuffled sides | intervals cover zero at about 95% | 97.4% mean coverage over 8 shuffles, minimum 96.5% |
| Permuted seasons | persistence collapses | mean *r* −0.001 against the real 0.335, largest \|*r*\| 0.070 |
| Duplicated player | the copy is caught | a 341st column, rank unmoved at 270, deficiency 70 → 71 |

The venue permutation the plan also asks for is **declared and not run**, and says so in the
payload: no model in this stack estimates a venue effect yet, so there is nothing for the
permutation to falsify. It arrives with the phase that fits one. The placebos prove the
machinery finds nothing where there is nothing; they are not sufficient alone, because a
maximally shrunk estimator passes every one of them, so the pre-flight's positive control
(which plants a known effect and asks for it back) is reported beside them, not instead
of them.

**The secondary set, reported without verdicts.** Leave-one-title-out moves the composite's
persistence between 0.317 and 0.355 across the six titles, so no single title is carrying it.
Leave-one-event-out moves the baseline's map Brier between 0.26312 and 0.26498 over 94 events.
Persistence is higher for players who stayed on their roster (0.346) than for those who moved
(0.308), and higher after a player's first qualified season (0.373) than during it (0.318).
`openskill` inverts that last one, at 0.206 for first seasons against 0.017 later, which is
what a rating that mostly measures a team looks like when the team is all it has ever seen. The
roster forecast is well calibrated in the CDL era, at a 0.0004 gap between predicted and
observed win rate over 6,522 maps, and over-predicts on the CWL archive by 0.0134 over 5,087.
None of these get an interval and none of them can promote anything.

**The gate is that the harness recovers what is already published before it scores anything
new.** Eleven cells of the persistence test, recomputed by a second implementation instead of
by calling the code being reproduced, plus every validation figure printed on this page. It failed
that gate the first time it ran, and it was this page that was wrong: the validation section
above had been carrying a run from before the identity merges, the plus-minus lineup rule and
the fourth feature set, while its own pre-flight section quoted the current numbers. Both now
agree, and a run that disagrees with either fails the release gate instead of printing a
number nobody compares.

### Which rating is the rating

Three player ratings are published, and they are not three attempts at one number. Each
names a different object, is judged by a different test, and carries a different known
failure:

| Rating | Answers | Judged by | Known failure |
|---|---|---|---|
| **SKILL** | how good is this player now | next-season persistence, declared before the fit | lost that test to raw K/D z, and takes 83% of its weight from the prior |
| **VALUE** (composite, v2.1.0) | what was that season worth | the map backtest, per cohort | describes a season played; most of a career's seasons have overlapping intervals |
| **Season plus-minus** | what won the map | split-half reliability, and a simulation of recovery | 16 of 1,010 published coefficients clear 1.96 SE |

**The composite rating is no longer the rating the site leads with.** It answers "what was
that season worth", and it was being read as "who is good now," a question it was never
fitted against. It has not changed, nothing it produced has been withdrawn, and every one of
its numbers is still published. It sits behind the rating that claims the forward question.

**The rule degrades by era, and has to.** SKILL exists for 2021-2026 only: an earlier season
has no season before it to train the prior on, and the CWL years carry no season-resolution
coefficient to blend with. A SKILL-first page for a CWL player would render nothing at all, so
those pages lead with the composite rating and say why. This is the failure mode the era
coverage work was about: an empty surface returns 200 and looks like a working page.

And the rating now in front is the one that lost its own gate. It leads because it is the only
one of the three that answers the forward question, not because it answers it well; the size of
that loss is in the SKILL section below, next to the number, not only here.

### SKILL: the box score fitted to predict wins, and what it did not fix

The composite rating loses the persistence test to a single K/D column, and the reason is
structural, not a matter of tuning. It is fitted against map outcome, so a column that
*names* the result (hill time, flag captures) earns weight for naming it. That is a
decomposition of a scoreboard, not a measurement of a player.

So this asks the inverted question. The target is the season plus-minus at `filtered` scope:
what a player's presence was worth in score margin, fitted on maps through that season and
nothing later. The box score is the predictor. A column earns weight only if the profile it
belongs to preceded a player who moved the margin. The fit is walk-forward by season, trained
on the seasons before *t*, scoring *t*, and the resulting prediction is blended with the direct
plus-minus coefficient by inverse variance. That posterior is **SKILL**.

**The most important number the phase produced is about the target, not the model.** Over the
431 player-seasons that carry a filtered season coefficient, the coefficients' standard
deviation is 0.0622 against a mean standard error of 0.1270, and the standard error exceeds the
absolute coefficient on 94.2% of them. Empirical Bayes returns a between-player variance of
2.2×10⁻⁷ against a mean observation variance of 0.0162, four orders of magnitude below the
threshold at which this project calls a variance component collapsed. *Taken at face value with
its own uncertainty, the season plus-minus does not establish that these players differ.*
Nothing downstream can recover from that, and the rest of this section is written against it
, not around it.

**What the fit reads.** One row per player-season with a season-resolution filtered coefficient:
431 rows, 149 players, 2020–2026. The CWL era's coefficients are estimated once over three
seasons and filed against each of them, so training on them would enter one observation three
times; they are excluded and the exclusion is published. The design is 16 columns: the per-mode
box-score profile, standardized inside its own season-and-mode cohort, for the three modes the
CDL era plays, plus one indicator per mode saying whether the player has a profile in it. A
feature a mode reports in some seasons and not others is not admitted, because a training fold
whose design differs from the fold it predicts is not a walk-forward fit.

**Maps played and teammate concentration sit beside the design and never inside it.** A
plus-minus coefficient is shrunk toward zero by the penalty in proportion to how little its
lineup varied, so a model handed exposure as a feature can predict the shrinkage and publish it
as skill. The check is a regression of the fitted prior on those two columns alone. The first
version of it admitted them as features and then measured how well they explained the result:
R² of 0.60, which said nothing about the box score and everything about handing the fit the
answer. With them held out, the prior loads **0.2625**, and the target it predicts loads
**0.2977** on the same two columns. The declared threshold was an absolute cap at 0.25, and
that cap asks a faithful fit to load on exposure *less than the quantity it predicts does*,
which no faithful fit can do. It was replaced, with the owner's approval and before the result
was read, by a ratio against the target's own loading: below 1.0 the fit attenuates the
relationship, above 1.0 it amplifies it. Measured, **0.8819**. The superseded threshold, the
value that replaced it, and the measurement that forced the change are all carried in the
source, and the release gate reads the ratio.

**Three model arms were declared before any of them was fitted, with the rule for keeping
them.** A regularized linear fit, a random forest and a gradient-boosted tree, on identical
folds, identical weights and identical drawn targets, and the boosted arm ships only if it
beats the linear one by a paired bootstrap whose interval excludes zero. On out-of-fold
correlation with the observed coefficient:

| Arm | Out-of-fold *r* | vs ridge | Kept? |
|---|---|---|---|
| Ridge (GCV penalty) | **0.4659** | — | published |
| Random forest | 0.4486 | −0.0173 [−0.0637, +0.0280] | no |
| Gradient-boosted trees | 0.4329 | −0.0330 [−0.0965, +0.0258] | no |

Neither non-linear arm beat the ridge, on 431 rows against a target whose noise exceeds its
signal: the regime where that was the predicted outcome. So the ridge publishes and **neither
dependency was merged**: they were installed to be judged, measured once, and removed, with the
verdicts kept in the source so a comparison that can no longer be re-run has not quietly become
"there was only ever a ridge".

**The blend has almost nothing to blend, which follows from the collapse above.** The prior's
own out-of-fold residual variance is 0.0032 against a mean observation variance of 0.0161, so
inverse-variance weighting puts **83% of the posterior's weight on the prior** (0.74 to 0.87
across rows). SKILL correlates 0.93 with the prior it came from and 0.78 with the coefficient it
was blended with. The architecture diagram's "posterior blend" is, on this record, a formality:
SKILL is the box-score prior with the direct estimate as a correction, and it is published that
way instead of being described as a balance of two comparable estimates.

**The result, against a floor computed before the model existed.** The gate runs on the
transitions carrying all four predictors: 220 over 75 players. That is fewer than the 269 the
floor was computed for, and the difference is not a coverage failure. SKILL is predicted from
the seasons before it, so the earliest CDL season has no rating and its 49 transitions cannot
carry one. On its own panel the smallest resolvable gap is 0.16.

| Predictor | Δ*r* vs K/D z | 95% interval | Detectable at | Beats the baseline? |
|---|---|---|---|---|
| SKILL | **−0.2401** | [−0.3582, −0.0955] | 0.16 | no |
| Composite | −0.2710 | [−0.4277, −0.1249] | 0.16 | no |
| `openskill` | −0.6937 | [−0.9799, −0.3740] | 0.20 | no |

**The architecture did not reverse the persistence failure.** SKILL predicts next season's
era-adjusted K/D z materially *worse* than K/D z does, by a margin larger than this panel can
mistake for noise, and K/D z is left standing as the recommended forecaster. The three-way
comparison the earlier figures were computed on is retained beside it (566
transitions over 190 players, composite at −0.2189), so nothing published before this phase was
restated by a fourth predictor narrowing the panel.

**One secondary test, declared in the manifest before the model existed, says where the failure
comes from.** The primary test scores every rating against next season's K/D z, which is the
baseline's own ground: a rating built to predict plus-minus is being asked to beat K/D z at
being K/D z. Scored instead against the quantity it was fitted for, next season's filtered
plus-minus, over 216 transitions, SKILL reaches *r* = 0.4002 against the composite's 0.2906 and
K/D z's 0.2565. That is a diagnostic and carries no interval and no verdict; it does not soften
the gate, which SKILL failed. What it says is that the object was fitted to a target the record
cannot measure precisely enough to be worth predicting, and then judged against a target it was
never built for. Both of those are real, and only the first is fixable by a better model.

### Four checks the ratings could have failed

Every test above scores a rating against the record it was fitted on. These four score it
against things outside that record: a rating built by somebody else, a set of awards voted
on by somebody else, the record with a season removed, and the moments a team swapped a
player. All four were written down with their populations and their verdict rules before any
of them ran. None is a fitting target and none can move a coefficient.

**Against an outside rating.** The [Cito API](https://citoapi.com) publishes its own per-map
player rating for 2020-2026, and it is licensed against redistribution, so what appears here
is derived from it and never the values themselves. Over 468 player-seasons the composite
VALUE agrees at Spearman 0.647 (95% interval 0.564 to 0.715, clustered on the player); over
the 359 seasons SKILL covers, SKILL agrees at 0.757 (0.691 to 0.807). Neither number is a
result on its own. Both ratings read the same box score, so agreement measures shared
arithmetic.

The first run of this comparison was wrong, in a way worth recording. It returned a pooled
Spearman of 0.477 while every individual season fell between 0.53 and 0.79, and 2020 came
back with a Pearson of 0.00 against a Spearman of 0.47. The cause was that an `overall` of
exactly zero marks an unrated map. Cito rates Hardpoint, Control and Search and Destroy;
Domination was a CDL mode in 2020 alone, and all 1,820 of its player-map rows read zero. With
another 505 zeros on early 2020 maps, 32% of that season was being averaged in as a rating of
zero. Excluding them moves 2020 to a Pearson of 0.623 and the pooled figure to 0.647.
Coverage is published per season beside the correlation, because a reader comparing 2020 to
2024 is comparing 68% of a season against all of one.

**Against the awards.** Two sources carry individual awards: Liquipedia for 2017 onward and
the wiki for 2013-2016. 88 of them name a player for a whole season. Scored against the top
*n* of that season's VALUE table, where *n* is the number of players the season actually
selected, **29 of 88 land in the top n against 7.8 expected by chance.**

| Season | Selected | Scored | Field | In top n | Expected |
|---|---|---|---|---|---|
| 2016 | 18 | 17 | 217 | 1 | 1.41 |
| 2017 | 8 | 8 | 128 | 1 | 0.50 |
| 2018 | 8 | 8 | 165 | 0 | 0.39 |
| 2019 | 10 | 10 | 204 | 5 | 0.49 |
| 2020 | 5 | 5 | 76 | 4 | 0.33 |
| 2021 | 4 | 4 | 63 | 2 | 0.25 |
| 2022 | 8 | 8 | 63 | 2 | 1.02 |
| 2023 | 8 | 7 | 63 | 2 | 0.89 |
| 2024 | 8 | 7 | 65 | 4 | 0.86 |
| 2025 | 8 | 7 | 62 | 3 | 0.90 |
| 2026 | 8 | 7 | 76 | 5 | 0.74 |

The 2017-2019 rows are new. Liquipedia holds no all-league team before 2020, so the CWL
years had no first-team credit while the CDL years had it, and the wiki's 26 CWL All-Star
selections now fill that hole. Nothing is counted twice: the two sources hold no award kind
in common there.

An award is a vote. It tracks team success and airtime, so a disagreement is evidence about
the ballot as readily as about the rating, and none of this is fitted against.

**Five of the 88 referents are missing, and four of them are one player.** The 2020 team
selected five players; every season since has selected four. Scrappy holds a first-team
selection in four seasons and carries no rating under the name the award was given to: the
box score sits under `Scrap` and the roster history under `Scrappy`, and nothing links them.
The fifth is `Vortex (Brandon Gomes)`, a 2016 first-team selection and one of the three
quarantined Vortex pages the wiki load could not place. Twelve more players carry a split of
that shape, found by grouping on real name. They are named in the artifact and left alone here, because merging an
identity moves every number downstream of it and that is not a validation decision.

**One test in the plan could not be run.** It asked whether SKILL identifies Rookie of the
Year before the season it was awarded for. All five winners have zero rated seasons before
their award, which is what being a rookie means, and no Challengers tier exists in this
record to have rated them in. What replaces it is the winner's rank inside their own season's
rookie cohort: Gwinn 2nd of 11, RenKoR 3rd of 12, Nium 3rd of 21, and Pred 10th of 10.

**With a season removed.** Take one CDL season out of the plus-minus fit, refit, and see how
much the remaining seasons reorder. The weakest of six holdouts reorders the later cells at
Spearman 0.997, against a floor of 0.8 set in advance. Removing a season also cannot touch
any cell before it, because the one-sided family solves through each cell and no further, and
the check confirms that on all 5,061 earlier cells rather than assuming it. Read this as a
weak test passed: one season is 7 to 9% of the admitted maps, and a fit that survives losing
it has not been asked a hard question.

**When a team swaps a player.** A CDL team changed exactly one of its four players between
consecutive events on 122 occasions, read from the maps actually played. On the 94 swaps both
ratings can score, **SKILL moves with the outcome and VALUE does not.** A one-standard-deviation
difference between the departing and arriving player is worth 7.2 points of map win rate under
SKILL (95% interval 4.0 to 10.9) and 1.5 points under VALUE (-3.2 to 6.1). VALUE's interval is
wider than this many swaps could resolve, so it is an absence of power and not a measured null,
and it is reported that way.

Read as an association and not an effect. Teams replace a player for reasons that correlate
with form, and the prediction ignores the three players who stayed. The outcome is map win
rate because a score margin means a different thing in each mode.

## Tier 2b: Series dynamics (shipped)

A Call of Duty series is a race to three maps, and much of what gets said about one is a
claim about the race itself: a 1-0 lead is worth more than arithmetic, teams ride momentum
through a series, a reverse sweep is a collapse rather than a coin landing the same way
twice. This section measures all three.

**One window now, where there used to be two.** The enumerated benchmarks below cover the
best-of-five series whose maps reconstruct their scoreline exactly. The enumeration needs
the title's declared mode rotation, and [as with `map_elo`](#map-elo) only the three CWL
titles declared one until recently, so 1,587 of the 2,859 loaded series produced no
benchmark at all, and this section's two halves were measured on different eras.
Thirteen of the fourteen titles declare a rotation now, and no loaded series is skipped
for want of one; the 141 Advanced Warfare series are skipped because that title has no
rotation to enumerate. What is still excluded is excluded for its shape and counted: 23
best-of-one, 93 best-of-three, 127 best-of-seven and 15 best-of-nine, 13 with a map that
has no recorded winner, 11 whose maps do not reconstruct the scoreline, and 36 with a gap
in their map ordinals. That leaves 3,833 of the 3,974 loaded series, and both halves of
this section now cover 2013-2026.

**The null is conditional independence, enumerated rather than simulated.** Each series'
two teams have a map-level Elo (the blend arm from the section above) frozen *before its
first map*. The league's mode rotation says which five maps they would play. That gives
five independent per-map win probabilities and an exact enumeration of the race: every
scoreline it could have reached, with its probability. No memory of any kind is in that
calculation, so the difference between it and what happened is where a series dynamic would
have to live.

**Why the raw number is not the finding.** The map-1 winner takes 74.5% of these series,
and most of that is not a dynamic at all. Between two identical teams a 1-0 lead in a race
to three is already worth 68.8%, by arithmetic. Between *these* teams at their frozen
ratings it is worth 71.0%. And the ratings themselves are modest: their map-1 calibration
slope is 1.12, meaning true strength gaps are wider than the ratings say. This is
a check run on map 1 because every series plays it, so unlike maps 4 and 5 that sample is not
conditioned on a result.

That last point is the whole difficulty. A team that wins map 1 is, on the evidence of
having won it, better than its rating said, so it wins map 2 more often than a rating-based
calculation predicts, with nothing carrying over between the maps. Every quantity here has
that problem. So each rate is stated against a second benchmark: the same enumeration at
the strength gap that best explains these results with *no* carryover, fitted below.

| | Observed | Coin flip | At the ratings | Allowing for quality |
|---|---|---|---|---|
| **Map-1 winner takes the series** | **74.5%** | 68.8% | 71.0%, **+3.5** [+2.2, +4.9] | 74.2%, +0.3 [−1.0, +1.7] |
| **Sweep (3-0)** | **36.5%** | 25.0% | 28.7%, **+7.8** [+6.3, +9.4] | 35.8%, +0.7 [−0.8, +2.3] |
| **Goes the distance (3-2)** | **27.4%** | 37.5% | 33.8%, **−6.4** [−7.8, −5.0] | 28.3%, −0.9 [−2.3, +0.5] |
| **Reverse sweep (0-2 down, won)** | **4.9%** | 6.3% | 5.6%, −0.7 [−1.3, +0.0] | 4.6%, +0.3 [−0.4, +1.0] |

Gaps in percentage points with 95% intervals, resampled over series; the pairing matters,
since both columns are computed on the same series. Bolded gaps exclude zero. Against the
ratings alone, every headline signature of momentum is there: too many sweeps, too few
deciders, a 1-0 lead worth three points more than it should be. **Against a strength gap
wide enough to explain the same series with no memory at all, all four vanish.**

That is a cleaner verdict than this table gave a moment ago. On the CWL-only window the map-1 row read +2.7 points against the quality
benchmark with an interval excluding zero, and it had to be argued down: 2.7 was below what
that sample could resolve at 80% power, and the direct test disagreed with it. Doubling the
window to all three eras (the same 3,833 series everything else in this section already
covered) puts that residual at +0.3 [−1.0, +1.7]. The earlier reading, that the quality
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
The stopping rule needs no such care: a series ending at three wins is a deterministic
function of results already in the likelihood, so the truncation is ignorable.

This test never needed a mode rotation, and now neither does the table above; both cover
the whole record.
The fit, over 15,520 maps in 3,974 series: **sigma = 0.70 logits** of team quality the
ratings did not have (about 34 points of map win probability between a team one standard
deviation above the rating's estimate and one a deviation below), and **gamma = +0.022
[−0.031, +0.075]**, in points of map win probability between a team that just won a map and
one that just lost, **+1.1 pt [−1.6, +3.8]**, likelihood-ratio *p* = 0.41. Fitted on maps
1-3 only, the one panel with no stopping rule at all because every best-of-five plays all
three, it is −1.7 pt [−6.0, +2.6]: consistent, wider, and on the other side of zero, which
is what a coefficient this close to nothing does.

For contrast, the same data regressed the ordinary way (map 2 on the frozen strength logit
and the map-1 result, with no series offset) puts winning map 1 at **+9.6 pt**, *p* <
0.0001, over all 3,974 series. The regression is reported in the artifact next to the null
it produces, because the gap between +9.6 and +1.1 is the finding: the effect is almost
entirely the two teams being further apart than the rating knew.

**What this record could have found.** 3,974 series can resolve a carryover effect worth
3.8 points of map win probability at 80% power, down from the 6.7 points 1,272 series could
resolve. So the null is tighter than it was: momentum inside a series is worth less than
3.8 points of map win probability. It is still not a claim that carryover is exactly zero.
The rest of the
site's momentum question, at series level across an event, is in
[Does it actually predict better?](#does-it-actually-predict-better). The
two now point different ways: within a series, adjacency adds nothing; across an event,
recent form carries a small measurable edge. Those are compatible, and they are different
questions.

The model is `series_dynamics` v1.0.0; artifacts `series_dynamics` and `series_momentum`.

## Tier 2c: Player style (shipped)

Every roster in this sport is described in nouns (anchor, entry, flex, objective player)
and a signing is explained by the role it fills. Those nouns may well be true of how teams
play. This section asks the narrower question the box scores can answer: do they fall into
groups, or into a cloud?

**The null is that there are no groups.** k-means returns k clusters for any k, on any
data, including data with no structure in it at all, so a partition is never by itself
evidence of one. What is published here is the comparison between the partition this
archive gives up and the partition *the same cloud with no groups in it* gives up.

**Quality is removed before the question is asked.** Cluster raw box scores and the
leading axis is "more kills, better ratio, larger share" with every metric loading the
same way. That is a rating, and the site already publishes one, so the "archetypes" would
come out as tiers. Every feature is therefore residualised against the published composite
rating, and what is clustered is the remainder: how a player played at their level, not
what level that was. The rating explains 12.6% of the variance in the CWL features and
6.4% of the CDL ones, so the two are very nearly orthogonal and almost nothing is lost by
insisting on the distinction.

**The era is removed too, and this costs more than it sounds.** Metric coverage is not flat
across the record: the kill feed exists for two titles of ten, Hardpoint qualification
varies by a factor approaching two between titles, and everything denominated in map time
stops in 2019. Take every metric present in all ten seasons and demand a complete row and
*no player-season qualifies*: the richest-looking feature set describes nobody. The rows
surviving a looser cut are not a random sample either; they skew to the better-covered
seasons and the higher-volume players, so a cluster fitted on them can be an era wearing a
costume. A column is admitted only if it is attainable in every season of the era being
fitted.

**Which is why this is fitted once per archive.** A basis common to all three eras would
be nearly empty, so each is fitted separately and published separately:

| Basis | Era | Columns | Player-seasons | Worst season retained |
|---|---|---|---|---|
| core 2013-2016 | 2013–2016 | 6 | 377 | 0% |
| core CWL | 2017–2019 | 26 | 483 | 92.1% |
| core CDL | 2020–2026 | 7 | 457 | 100% |
| extended 2013-2016 | 2013–2016 | 12 | 266 | 0% |
| extended CWL | 2017–2019 | 76 | 318 | 41.0% |
| extended CDL | 2020–2026 | 33 | 428 | 83.1% |

**The first era is named by its span rather than by a league.** The other two eras each
cover one league, so each takes that league's name. The 2013-2016 archive covers three MLG
seasons and 2016, which ran as Call of Duty World League, so no league name fits it without
naming one of its seasons wrongly. The span names all four correctly.

The three core bases are the published ones. They are not comparable to each other and are
not compared: 26 columns of streaks, multikills, headshots and pace against 7 columns of
kills, deaths, damage, engagements and share is a different question asked three times, not
one question asked of three eras. The CDL basis is thin because most of what the CWL archive
measured (streak depth, headshot rate, accuracy, suicides, per-10-minute anything) is
simply not in the CDL-era source, and the wiki-era basis is thinner still.

**One season of the wiki era retains nothing, and that is stated rather than smoothed.**
2014 contributes no player-season to either 2013-2016 basis: its rows carry a different set of
non-zero columns from 2013, 2015 and 2016, and a column has to be attainable in every
season of the era to be admitted. So "2013–2016" on this page means 2013, 2015 and 2016,
and the axes below are fitted on 377 player-seasons drawn from those three.

**There is no taxonomy, in any era.** On all three published bases the gap statistic
prefers a single cluster to every partition it tries.

On the CWL basis the best silhouette any k reaches is 0.248, at k=2, and a single Gaussian
with the same covariance and sample size scores 0.227 to 0.266 on the same test: the
separation observed is what no separation looks like. Bootstrap cluster stability at k=2 is
high (Jaccard 0.951 and 0.954) and on its own means nothing, which is the trap this
section exists to avoid: bisecting an elongated cloud along its long axis is enormously
reproducible, and the Gaussian null reproduces itself just as well, at 0.884 to 0.964.
Every k from three up fails every test.

On the CDL basis the same thing happens with larger numbers on both sides. Its k=2
silhouette is 0.366, which looks like real separation until the null band is read: 0.332 to
0.383. Stability is 0.922 and 0.939 against a null of 0.837 to 0.976. Seven correlated
slaying columns produce an elongated cloud, and an elongated cloud bisects cleanly whether
or not anything is in it.

The 2013-2016 basis is the thinnest and answers the same way. Its best partition is k=6 at a
silhouette of 0.337, inside a null band of 0.315 to 0.352, and the gap statistic still
prefers one cluster. Six columns over three seasons is the least this test has ever been
given, and it finds what the other two find.

All three extended bases agree. The extended 2013-2016 k=2 scores a silhouette of 0.264 against
a null band of 0.240 to 0.307; the extended CWL k=2 scores 0.194 against 0.171 to 0.215
and a stability of 0.929 against 0.885 to 0.953; the extended CDL k=2 scores 0.256 against
0.215 to 0.279 and 0.963 against 0.863 to 0.979. All inside what no clusters look like, so
nothing is published from any of them.

**What is real is the axes.** Horn's parallel analysis (each eigenvalue against the 95th
percentile of the same matrix with every column independently permuted, which destroys
correlation while preserving each metric's own distribution) retains two components on the
2013-2016 basis, together 83.8% of the residual variance, five on the CWL basis, together 66.0%,
and two on the CDL basis, together 89.5%. Read in raw metric terms:

| Basis | Axis | Name | Share | Loads on |
|---|---|---|---|---|
| 2013-2016 | 1 | volume | 49.5% | kills, K/D, plus/minus and engagements, all the same way |
| 2013-2016 | 2 | survival | 34.3% | more deaths against fewer engagements, with a better plus/minus and K/D |
| CWL | 1 | volume | 30.3% | kills, blitz index, kill share, K/D, multikills and plus/minus, all the same way |
| CWL | 2 | survival | 15.4% | more deaths and fewer engagements, with a better plus/minus and K/D |
| CWL | 3 | *axis 3* | 8.5% | assists, against team kills and kill share |
| CWL | 4 | streak depth | 6.1% | deep streaks and six- and seven-kill streaks, against headshot rate and four-streaks |
| CWL | 5 | risk | 5.7% | eight-plus streaks, against assists, suicides and team kills |
| CDL | 1 | volume | 58.4% | kills, kill share, K/D, plus/minus and damage, all the same way |
| CDL | 2 | survival | 31.1% | more deaths against fewer engagements, with a better plus/minus and K/D |

The 2013-2016 basis carries the same two axes the CDL basis does, on six columns instead of
seven, which is what a thin basket recovers: how much a player did, and how much they
survived doing it.

**A name belongs to what a component loads on, and is now assigned that way.** Each name
declares the column its axis should load hardest on: `volume` on kills, `survival` on
deaths, `streak depth` on deep streaks, `risk` on eight-plus streaks, with the mode prefix
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
moves it is a decision, not a silent relabelling.

The CDL basis gets its names from the same function, not from nobody, which is the
other half of the fix: on what they load on, its two components are a volume axis and a
survival axis, and neither is anything a role vocabulary would recognise.

A player is published as a position on their era's axes, not a label. Scores are
signed so that each axis's largest loading is positive, so a rerun cannot silently flip a
career's direction, and stored per player-season because the position moves, which is the
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

## Tier 2d: Role at the opening engagement (shipped)

The nouns Tier 2c refused to fit are still worth one number. This section asks how often a
player is in the first fight of a Search and Destroy round, and what taking that fight
goes with. A contact is an opening kill or an opening death, so the contact rate is
`(first_bloods + first_deaths) / maps`, and the contact win rate is the share of those
opening fights won. A player-season qualifies at 30 maps. 311 of them do, across 109
players.

Nothing is labelled. A high rate is not called an entry and a low one is not called an
anchor, because Tier 2c found no partition to hang either noun on. The player page prints
the rate, its percentile among that season's qualified players, and the win rate.

**What the opening job costs.** K/D, damage per map and the untraded share of kills are
each regressed on contact rate, all four standardised inside the season, with players as
the resample unit for the interval:

| Outcome | Per SD of contact rate | 95% interval | Separates |
|---|---|---|---|
| K/D | +0.031 SD | [-0.125, +0.167] | no |
| Damage per map | -0.171 SD | [-0.312, -0.045] | yes |
| Non-traded kill rate | +0.140 SD | [+0.026, +0.239] | yes |

The K/D interval is tight around zero. This is a null with power behind it, and it
contradicts the premise the phase was built on: on this record the opening job costs no
measurable K/D. Two quantities do move, in opposite directions. A player who takes more
opening fights does less total damage per map, and a larger share of their kills goes
unanswered. That is an association between a role and an outcome, and no mechanism is
claimed for it.

The player page publishes the raw K/D, the part the contact rate accounts for, and what is
left, all three together. An adjustment whose size a reader cannot audit is worse than no
adjustment. This one is small, because the fit above could not separate the two
quantities in the first place.

**Do the style axes already carry role?** If they did, the modern era could be described
with them. The question is answerable only where the record names a weapon, and the
favourite-weapon column exists on every CWL title and no CDL title, so the test runs on
2017-2019. The 27 weapon names are mapped to classes. Five of those names also appear in
the kill feed with an observed class, so the mapping is checked against the feed instead of
asserted, and the feed decides where they differ: it reads `ar` for the KBAR-32. A release
gate fails the run if the table and the feed ever disagree.

Held out by player, the five CWL style axes recover the observed class 72.3% of the time
against a base rate of 57.5%, over 285 player-seasons and 169 players. The interpretation
rule was written before the number was seen: 75% or above means the axes carry role, 60%
or below means they do not, and between the two is ambiguous. The result lands in that
band and is published as ambiguous. No modern-era claim on this site rests on the style
axes. The strongest single axis is survival, at an AUC of 0.22, which separates hard in
the direction that says SMG players die more.

**The eras do not share a question.** `first_deaths` and `non_traded_kills` are zero on
every CWL title, and the weapon label is absent on every CDL title, so the cost and the
label never coexist. The cost is measured on 2020-2026 and the recovery test on 2017-2019,
and neither number crosses the seam. Nothing outside Search and Destroy is claimed:
opening contact is a round-based idea, and the other modes respawn.

## Tier 3: Career and player-shape modeling (shipped)

Two models live here. One adds seasons up. The other asks what age does to them.

### Career value: what gets added up, and what gets credited

Every other number on this site describes one season. A career total is the sum of them
over a baseline, and the arithmetic is the easy part. The two hard questions are which
season quantity to add, and who gets credit for it.

**Two quantities are summed, and they answer different questions.** The composite
season rating asks what a season was worth on the scoreboard. It covers all ten seasons
from 2017 to 2026 and it separates players cleanly, because the box scores under it are
measured precisely. It is fitted against map outcome, so a column that names the result
earns weight for naming it, and a career total built on it is a career of scoreboard
contribution. The season plus-minus asks what a player's presence was worth in score
margin. That is the question most arguments are actually about. On this record it is
very quiet, for reasons [the season plus-minus section](#season-plus-minus-the-time-axis-the-record-turned-out-to-carry)
gives in full.

**The baseline is the qualified-cohort minimum for each season.** Not a percentile. A
percentile is a chosen number wearing a definition, and the choice would move every
total. The floor to qualify is eight maps, the same floor the era adjustment and the
plus-minus admission rule already use. That threshold selects on playing time, which is
itself an outcome, and the same problem returns one level down in the aging section
below.

**Peak, best three consecutive, and career total are three columns.** They rank
different players first. A single number would hide the disagreement that makes the
question worth asking.

A three-season window is three consecutive seasons of the league, so a player who sat one
out scores that window on the two seasons they played. On the composite axis 47 of the
best-three windows span the 2019/2020 league change. That is allowed here because the
season rating is scored against each season's own cohort, which is what makes seasons
comparable at all. It is worth knowing when reading one: those three seasons cover two
leagues, two box-score sources and a title change.

#### The credit rule, and what it turned out to be worth

A season coefficient in the plus-minus is a deviation from the player's team-season.
So a career total has a choice. It can credit the deviation alone, which under-credits
the four players who *were* a great team. Or it can credit the deviation plus a share of
the team term, which brings back the ambiguity the team column was added to remove.

The plan that specified this phase predicted the choice would change the order of the
table. Both columns were published so the difference could be read.

**It changes almost nothing.** Over the 149 CDL careers the two orderings correlate at
**rho = 0.998**. The top ten is the same ten players in the same order. The largest rank
move anywhere in the table is nine places, and it happens at rank 144 of 149. The share
of the team term one player carries is a quarter, which is the only division the record
supports, and a quarter of a team-season effect is small next to the spread of the player
deviations it is added to.

That settles a question the plan expected to leave open. Both columns still ship, because
the agreement is a finding and a reader should be able to check it.

#### What the totals establish

| Table | Careers | Totals clearing two standard deviations |
|---|---|---|
| Composite, all ten seasons | 321 | 241 (75.1%) |
| CDL plus-minus, deviation | 149 | 64 (43.0%) |
| CDL plus-minus, with team share | 149 | 63 (42.3%) |
| CWL plus-minus, deviation | 198 | 92 (46.5%) |

The plus-minus half of that table follows from a season coefficient whose spread is
indistinguishable from zero given its own standard error. Adding seven of them
narrows the interval. It does not manufacture a separation the seasons never contained.
Every total is published with its own standard deviation, so this stays visible on the
player page.

**The two eras are never summed together.** The CWL years store one pooled coefficient per
player per era, filed against each of the three seasons it covers. Adding those three would
count one estimate three times. A CWL contribution is stored as its own row and read beside
a CDL total. The composite axis has no such constraint, and its single row spans 2017 to
2026; the 2013-2016 seasons are excluded from it as well, on the same rule that withholds
their season score.

### Career rank: a second all-time axis, over the metric basket instead of VALUE

Career value sums a rating fitted against map outcome. Career rank sums a different
quantity, a **breadth score**: the coverage-weighted mean percentile across every
gold-tier stat a player's own page shows that season, weighted by each mode's share of
that season's maps. The metric table also carries a pooled row per player-season,
aggregated over the same maps as the mode rows beside it. Beside a mode row it is the
season counted twice and is ignored; where no single mode clears the surviving-stat
floor it is the only reading of the season there is, and the season scores on it at its
own map count. A season spread thinly across four modes can clear that floor pooled and
clear it in none of the four, and such a season is thin coverage rather than weak play. The basket is exactly `buildMetricCards`'s gold-tier set, the same
metrics a reader sees on the player page, minus the kill-feed categories (IW/WWII only)
and the round-card keys. The per-map twin of any per-10 pair is dropped so a rate and
its timed form never double count the same signal. A season needs at least two surviving
stats to score at all, the same floor the player page itself uses to decide whether a
card renders. Award status (First/Second Team, MVPs, Rookie of the Year) adds a fixed
number of percentile points on top of the season score, capped at 100.

**A career needs at least three qualified seasons for an overall row.** Below that
floor, season scores still compute and no total, peak or best-three is published.

**The board starts in 2013, and the shrinkage is what lets it.** Every season the
archive holds is scored and ranked. A career that started in 2013 is ranked on the
seasons it played.

The floor is this model's own constant, not the site's. `maprows.PUBLISHED_FROM_YEAR`
holds the floor for the season ratings a player page shows and for the evaluation
harness, and it stays at 2017. A board that admitted no season before 2017 did not rank
that era low. It left the era out, and the era-balance test failed on an unrepresented
era for exactly that reason.

**Each season is pulled toward its own season's mean by its map count.** A score sits at
`maps / (maps + 15.48)` of its distance from the mean of the players who played that
season. A 40-map season is a noisier reading of a player than a 124-map one, so left
alone it reaches further from the middle in both directions.

The constant comes from a measurement. Season scores centred inside their own season
have a variance that falls as one over the map count, so binning the season deviations
by map count and regressing each bin's variance on the reciprocal of its mean map count
splits the spread into a true part and a sampling part. The fit that set the constant
read 144.11 and 2,231.4, putting their ratio at 15.48 maps, at a weighted R-squared of
0.837, over 1,196 deviations. The same estimator against the surviving stat count reads
0.798, which is why the basis is maps. The number is frozen in the module and refitted
beside every run, so a drift away from it shows on this page.

**That fit was reading 1,196 of 1,458 seasons, and the missing 262 were not random.** A
season scored only on the pooled all-modes row carried no map count at all, because the
map query grouped by mode and the pooled row is not a mode. A season with no maps cannot
be binned by map count, so it fell out of the regression silently — and the seasons it
dropped were exactly the thin ones the regression is trying to characterise. With the
pooled row answered for by its own grouping set, the refit runs on all 1,458 and reads
141.22 and 2,813.5, a ratio of **19.92 maps** at a weighted R-squared of **0.989**, up
from 0.837.

The applied constant stays 15.48. It is frozen by the pre-registration and moving it is
a change to the formula, not a repair to it, so the gap is published here rather than
closed quietly: seasons are currently shrunk slightly less than the best current
estimate of the sampling noise would justify.

What it does to the width of a season score:

| Era | Seasons | Median stats | Median maps | SD before | SD after |
|---|---|---|---|---|---|
| 2013-2016 | 504 | 14 | 40 | 18.53 | 11.78 |
| CWL | 497 | 26 | 35 | 15.21 | 10.44 |
| CDL | 457 | 25 | 124 | 14.82 | 11.41 |

The spread across eras falls from 3.71 to 1.34, and the three eras end up within 1.34
points of each other where they began 3.71 apart. The CDL is no longer the widest after
the correction; 2013-2016 is, by 0.37 points, which is what 40 maps against 124 should
produce once the shrinkage has done its work.

**The era gate does not rest on that correction.** This was measured. Admitting the era and applying the shrinkage were run as four separate
configurations. With the 2017 floor the era-balance test fails either way, at a worst
skew of 2.15 unshrunk and 2.28 shrunk, both on an unrepresented era. With the 2013 floor
it passes either way at 1.86, and the top-25 peaks per era are identical with and
without the shrinkage: 7 for 2013-2016, 4 for the CDL, 14 for the CWL. The coverage
change carries the gate on its own.

**One difference the shrinkage does not touch.** A 2013-2016 season is a mean of about
14 percentiles where a league season is a mean of 25 to 26, because the kill-feed and
round-card stats do not exist for those years. That is a difference in what was
recorded. No weighting by maps makes 14 stats say what 26 say.

Peak, best three consecutive and total are the same three columns career value
publishes, computed over the breadth score instead.

**CWL years count at full weight here, unlike the plus-minus axis.** The plus-minus
stores one pooled coefficient per player per CWL era because that axis's season unit is
a fitted coefficient shared across three years, and summing all three would count one
estimate three times. The breadth score is computed fresh per year from that year's own
box scores, so there is no shared estimate to guard against. 2017, 2018 and 2019 each
count as their own season.

**Two team-strength numbers ride beside every season score, never inside it.**
Net-of-teammates is a player's own season VALUE minus the mean VALUE of the modal-team
roster around them: the team a player played the most maps for that season, the same
join `career.py` uses for its own team-share credit rule. That join was chosen over the
event-window `roster_stints` table because a mid-season roster is a measurement, not a
stated availability window. Opponent strength is the mean VALUE of the teams actually
faced that season, weighted by maps. Neither number corrects the score. The
[opponent-adjustment phase](#opponent-adjustment-what-the-box-score-owed-to-who-was-across-from-it)
found that correction a null at the season grain, so both numbers are context, published
the same way a total's standard deviation is published without being folded into the
arithmetic.

The opponent-strength proxy needs its own honesty check. The project has no independent
team rating, so a team's own season strength is approximated as the mean VALUE of its
modal-team players. That proxy was checked against an outside signal before this
shipped: season map win rate, taken from `games.winner_team_id`. It correlates with the
proxy at Pearson r = 0.76 and Spearman r = 0.80 over 327 team-seasons with at least 10
maps, strong enough to trust as a real signal and not a coincidence of the join. Both
numbers are computed on every run and stored in the artifact this page reads, so the check
is repeated rather than remembered.

**Every total carries a standard deviation**, the same convention career value follows.
It comes from how much the gold-tier basket disagreed with itself that season: not a
measurement error on any one stat, but the spread across the stats in the basket. A
season where the metrics mostly agree gets a tight SD; a season where they scatter gets
a wide one. A career total's SD compounds the season SDs as independent variances, the
same simplification career value's own total_sd makes. That understates the true width,
because the underlying metric fits share a cohort across years.

### Aging: three curves, because one curve would be wrong

A curve fitted on the player-seasons we can see is biased upward at the old end. Players
who decline leave the league and stop producing seasons, so the players still measured at
28 are the ones who did not decline. This is the best-documented defect in the baseball
aging literature, and a four-player roster with brutal attrition makes it worse here.

The fix is not a better single curve. Three fits are published together:

1. **Naive.** Every observed player-season, level on level. This is the biased one. It
   ships because the size of the bias is only visible against it.
2. **Delta.** Paired consecutive seasons of the same player, so each observation is a
   within-player change. Between-player differences cancel.
3. **Retention-weighted.** The same pairs, weighted by the inverse of a fitted
   probability of surviving to the next season at that age.

The delta method carries its own version of the bias. Pairing conditions on having a next
season, and a player who declines and gets dropped contributes no pair. That is why it is
not published alone either.

#### The peak age

**Between 19.2 and 24.0.** That is the union of all three intervals on the composite
rating, over 299 players and 1,068 player-seasons. The three point estimates land at 20.36,
20.37 and 21.01, a spread of 0.65 years.

| Fit | Peak | 95% interval | Observations |
|---|---|---|---|
| Naive | 21.01 | 19.33 – 23.96 | 1,068 seasons |
| Delta | 20.36 | 19.16 – 21.55 | 677 pairs |
| Retention-weighted | 20.37 | 19.15 – 21.66 | 677 pairs |

The whole window moved about two years earlier when the 2013-2016 seasons entered, and that
is what a longer record does to this measurement rather than a correction to it: the
pre-2017 field is younger, and a curve fitted on more of a career locates its top sooner.

The naive fit still peaks more than half a year later than the within-player fits. That is
survivorship, in the direction the literature predicts.

**The retention weighting changes the answer by 0.01 years.** The correction is applied
and it does almost nothing. On this record, conditioning on having a next season is not
what was driving the delta estimate. That is a null, and it is a useful one: it says the
gap between the naive and delta fits is a between-player effect, not a survival effect.

Any single-number peak age in this domain is a selection artifact wearing a credible
interval. The interval above is what the site publishes.

#### The two-component test

The plan predicted that slaying and objective contribution peak at different ages. They do,
in the predicted order. The intervals overlap, so the record does not separate them.

| Component | Peak estimates | Published interval |
|---|---|---|
| Slaying (K/D z-score) | 18.36, 18.46 | 18.07 – 20.41 |
| Objective contribution | 20.61, 20.73, 21.17 | 19.23 – 22.63 |

Slaying peaks about two years earlier at the point estimate. Two overlapping intervals
are not a separation, and this is reported as a negative result.

#### The plus-minus locates no peak at all

None of the three fits finds an interior maximum on the season plus-minus, over 424
player-seasons and 263 pairs. Nothing is published for it. This follows from the same
measurement the [SKILL section](#skill-the-box-score-fitted-to-predict-wins-and-what-it-did-not-fix)
reports: if the spread between season coefficients cannot be distinguished from zero, an
age curve through them has nothing to bend around.

#### Coverage and the second x-axis

Birthdates are known for 439 of 815 players. A player without one is fitted on their
career-season index instead, in a separate population that never mixes with the age one.
Averaging an age curve with a career-index curve would produce a curve of neither.

A curve is drawn only over the ages this record supports: the widest run of consecutive
ages that each carry at least 10 player-seasons. That is 18 to 28 on the box score and 19
to 28 on the plus-minus. Every observation still enters the fit, and the window restricts
only the drawn range and the range a peak may be published in. The record holds one season
at 17 and five in total across 29 to 32, so a quadratic drawn to those edges would put its
steepest claim on its thinnest evidence.

No survival library is used. What the retention model needs is the probability that a
player seen at age *a* appears the next season, over at most ten periods, with no
covariates and no censoring past the final season. That is a ratio of counts with a
shrinkage prior, and it is written out in
[`analytics/src/cdlhub_analytics/aging.py`](../analytics/src/cdlhub_analytics/aging.py).

### Not on this list

**Peak and breakout detection.** Changepoint analysis on a rolling rating is still
unwritten.

**Player archetypes.** Attempted, and the answer was that this archive has none. See
[Tier 2c](#tier-2c-player-style-shipped) for the tests and
[`db/migrations/0012_player_style.sql`](../db/migrations/0012_player_style.sql) for why the
table stores a position instead of a label.

## Tier 4: Meta and environment analysis (partly shipped)

- **Loadout meta (shipped).** Usage share and map win rate for every loadout choice the
  archive records, by season and mode: weapons across all three CWL titles, WWII divisions
  and basic training, Infinite Warfare rigs, payloads and traits, and Black Ops 4
  specialists. The CDL-era source carries no loadout column of any kind, so this tier stops
  at 2019 and always will unless a second source supplies one. Choices under 30 player-maps
  are suppressed. Win rates sit near 50% for
  every widely used option, which is the expected result when both teams field the same
  meta. It is reported as that, not dressed up as an edge.
- **Map and mode analysis.** Scoring environments per map, side and streak effects
  where derivable, map-pool comparisons across eras.
- **LAN versus online.** A paired within-player comparison across the 2020-2022 online
  boundary, which is one of the few natural experiments available in esports, reported
  as effect sizes with confidence intervals. The events and their LAN flags are now
  loaded, so this is unwritten, not unavailable.
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
[Map Elo](#map-elo), and published model nulls (the series-level momentum test, and the
carryover null from [Tier 2b](#tier-2b-series-dynamics-shipped)).

The what-wins-maps comparison is stated as the gunfight against everything else, not
as slaying against objective play. The model defines exactly one boundary, which of a
cohort's features are the kills/deaths pair, and what remains on the other side varies
by cohort, mixing objective columns with survival and trade economy. Naming the ratio
after the split the model actually makes keeps the claim true across feature-set
versions; calling it "objective play" would not. A cohort whose bootstrap interval
covers 1.0 emits no finding at all: every reading of that ratio is a claim about which
side of even the truth falls on, and where the interval does not answer that there is
nothing to publish.

Six more read the metric layer, which is where the claims a box score cannot make live:

- **intangible outlier**: a season elite at an intangible while ordinary at K/D, or
  the reverse. This is the argument for having a metric layer, stated one player at a
  time.
- **profile extreme**: the league-best qualified season value of a gold metric.
- **clutch milestone**: 1vN records reconstructed from the kill feed.
- **trade asymmetry**: slaying and trade economy pointing opposite ways: the heavy
  slayer who dies alone, the light slayer whose deaths always get answered.
- **meta shift**: a weapon's usage share swinging 20 points or more between
  consecutive events of a season.
- **team style**: rosters at the extremes of how they divided hill duty, opening duty
  and kills.

There are currently 227. Each carries the numbers backing it and a link into the
evidence view, so any claim on the site can be traced to the data that produced it.
These are generated from model output by fixed rules, not written by hand and not
written by a language model.

Two passes at the end keep that count honest, because the raw rules overcount badly.
Several kinds read a table that carries an all-modes row *plus* one row per mode, so a
player with one strong season produced an all-modes K/D outlier and one more per mode
played: the same finding sliced five ways. One player once held nine of thirty
outliers. So each season collapses to its single most extreme slice, and no subject may
contribute more than two findings of any one kind; league-wide rankings and per-cohort
model summaries are exempt, being one fact each already. Career volume is reported as a
rank among the deepest 25 careers, not as a threshold, because "past 250 maps"
was true of 75 of 273 players and described the threshold, not the player.

Two more details were bugs first. Roughly
half the intangibles are lower-is-better (untraded deaths, first deaths, zero-kill
rounds), so every comparison re-reads a percentile through the catalog's own direction
before calling it good or bad; without that step the generator reported players who were
excellent at *both* K/D and an intangible as contradictions, with a headline claiming
the opposite of the truth. And a "nobody in the league matched this" claim requires
twice the qualifying sample, because clearing a leaderboard minimum is a much weaker
thing than being unmatched.

## Tier 5b: Error control over the findings (shipped)

Every finding in Tier 5 is the extreme of a scan. Scanning a league across sixteen kinds
and printing the extremes produces confident-looking claims out of noise, and until this
layer existed nothing here quantified the rate. Grepping the analytics package for
`bonferroni`, `benjamini`, `hochberg` or `fdr` returned nothing at all.

**A third of the ledger is not a test.** A finding is testable when its sentence claims
something the record only estimates: an ability, an edge, a tendency. It is descriptive
when its sentence is a statement about the record. A season K/D is a noisy read on how good
a player was, and the era model already publishes an error bar for it, so the claim can be
wrong and can be tested. League-wide engagement pace across a season is computed over every
map the season contains. It estimates nothing, and a null for it would have to be invented.
Four classes fall out of that criterion:

| Class | n | What it means |
|---|---|---|
| testable | 103 | A latent quantity, and an error for it in the database. Carries a p-value and both q-values. |
| uncorrected | 58 | A latent quantity, and no error anywhere to test it with. |
| descriptive | 62 | A statement about the record. No latent quantity, no null. |
| self-tested | 4 | A declared test that already publishes its own interval. |

The uncorrected class is the metric layer's five kinds. `player_metric_season` stores a
value, a denominator, a z and a percentile for an arbitrary metric, and no standard error,
so a threshold test on one of them would need an error bar invented for the occasion. They
ship labelled, with the reason on the page. The fix is a phase of its own: a cluster
bootstrap over a player's own maps would give a uniform error for any metric.

**The null is the claim's own boundary.** A finding that says a season sits at least two
standard deviations from its cohort is tested against a true position of two. Testing it
against zero would ask whether the player differs from the league at all, which is known
false before the data is seen. Under that null the screened outliers score a mean |t| of
5.74, every one of them survives any correction, and the exercise controls nothing. The
boundary null tests the sentence the site actually prints.

**The p-value is conditional on the screen that selected the claim.** A selected subject's
statistic is biased upward against its own true value. Conditioning on the selection
removes exactly that bias:

    p = P(statistic >= observed | statistic >= screen, true value = boundary)

Where the screen sits at the null value, which is how every kind here is built, this
reduces to twice the plain tail. The selection correction is a factor of two, written down
instead of absorbed, and it is valid post-selection.

**Both step-up procedures ship.** Benjamini-Hochberg controls the false discovery rate
under independence or positive dependence. These families overlap by construction, since
one player-season can reach several of them, so BH is the optimistic bound.
Benjamini-Yekutieli is valid under arbitrary dependence and costs power. A reader gets both
and can see where they disagree. The declared threshold is q <= 0.10 on the BH column, per
family, and it was fixed before any q-value was computed.

| Family | Tested | Median q | Retracted |
|---|---|---|---|
| Head-to-head | 38 | 1.000 | 38 |
| Outlier | 46 | 0.824 | 46 |
| Trend | 19 | 0.333 | 18 |
| Trade economy | 12 | 0.561 | 11 |
| Clutch record | 9 | 1.000 | 9 |

**Two of the 124 survive.** The sensitivity curve is published with the verdict, so the
threshold reads as a choice: 1 finding survives at q <= 0.05, 2 at the declared 0.10, 15 at
0.20, 20 at 0.33 and 45 at 0.50. The fifteen a 0.20 threshold would keep are the ones a
reader would call real, and 0.10 retracts them. Moving the threshold after seeing that
table is the post-hoc adjustment a pre-registration exists to prevent, so it was not moved.

Three things drive the result and all three are real. A finding sitting at its own screen
boundary earns nothing: a 6-of-8 head-to-head is the least impressive record that can clear
a 70% screen, so conditional on having been selected it carries no evidence. A season K/D
is measured with an error about half the cohort spread, so a season at 2.4 SD is compatible
with a true 2 SD. A monotone move across three seasons happens one time in three by chance,
so every three-season trend starts at p = 1/3 before any correction.

**Two screens are not modelled.** A season is collapsed to its most extreme mode slice and
a subject is capped at two findings per kind. Both select on the same statistic being
tested, so these p-values are optimistic by an amount this run does not measure. The true
picture is slightly harsher.

**Retraction is a published event.** A finding that fails the threshold keeps its row and
its q-value and moves to a retracted list on the site. Nothing disappears from the feed,
and the descriptive and uncorrected findings are untouched by any of this.

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

A key names the thing, not the row that held it: a player's handle, a season's
year and league, a metric's name, an artifact's JSON path, so two snapshots stay
comparable across a refit, a reload that renumbers a table, or a change to how player
identity resolves. Lists inside an artifact are keyed by the identity their elements
carry, not by position, so a leaderboard that reorders reports the moves it
contains instead of reporting every row as changed.

### What counts as a move

A number counts as moved when

    |new - old| > 1e-9 + 1e-6 * |old|

Published numbers are stored as 32-bit reals, which carry about seven significant
digits, so that threshold sits just above the storage floor. It suppresses
representation noise and nothing else: every real change is counted and the largest
are named. Values that are not numbers (a finding's headline, a backtest window, a
verdict such as "the interval excludes zero") are compared for equality, and a change
there is reported as a flip, not a move, because a verdict that reverses is not
a small difference.

The report gives, per family of numbers, how many moved, how many flipped, how many
keys appeared and how many vanished; then the largest moves by name with both values;
then how many it did not name. A truncated list that reads as a complete one is the
failure this instrument exists to end.

**The first thing it caught was a whole class of them.** Every bootstrap and permutation
here draws *positions* in a population, and the populations were being ordered by database
ids: rows in `player_id` order, clusters in the order a `series_id` first appeared, groups
in the order a dictionary happened to fill. Those ids are assigned by the loader. Reloading
a source renumbers them, the population permutes, the same seed lands on different
observations, and a published interval moves while the estimate it brackets does not. The
harness found two instances and the class was then swept: every such population is now
ordered **by its own contents**, and every group that resamples on its own (a player's
maps, a cohort's, an era's) draws from a generator seeded from those contents instead of
from one generator threaded through a loop. Each site carries a test that renumbers the rows
underneath it and requires the interval to come back identical.

Landing it moved numbers once, and not only intervals; the reason is instructive.
Every published interval that resamples moved. So did
14,750 season **ratings**, by up to 0.010 on a scale where the league average is 1.00: the
per-cohort observation-variance calibration is itself estimated by resampling each player's
maps, so reseeding it moves the shrinkage constant and therefore the ratings it shrinks.
Style scores moved with them, by up to 0.068, because the style basis residualizes on the
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

A stored backtest that cannot be reproduced exactly is a claim, not a record. Each
model run therefore stores, beside its hyperparameters and the commit that produced it:

- the fixed seed of every stage that draws random numbers, so a resampled interval can be
  recomputed, not trusted;
- a hash of the resolved dependency lockfile, so the solver stack is part of the record;
- the interpreter, numpy and platform versions;
- the evaluation-set hash above.

Where a model builds a design matrix, the matrix itself is fingerprinted (shape, column
names and contents) and published with the fit, so a refit that produced different
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
