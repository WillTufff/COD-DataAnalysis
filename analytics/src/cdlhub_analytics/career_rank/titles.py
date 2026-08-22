"""What counts as a title, declared once, read by everything that counts one.

Two words, and the scene keeps them apart. A **chip** is a tournament win: any
title event, from a CWL open to a Major. A **ring** is a world championship —
Call of Duty Championship, CWL Championship, CDL Championship — and nothing
else. A career carries many chips and few rings, so a number that mixes them
answers neither question.

`event_placements` holds a first place for things that are not tournaments. A
relegation bracket, a qualifier standings page, a regional final into a
championship and the CDL's regular-season table all publish a winner, and
counting them produces a ring number no reader recognises: under a raw count
the 2019 Pro League Qualifier alone hands out eight of them.

The rule, and why each part of it is here:

- `tier_type` never counts a `Qualifier` or a `Showmatch`. That is Liquipedia's
  own word for the event, and it removes the CDL Major qualifiers and the
  All-Star weekends.
- A name that says qualifier, relegation, play-in, regional final or regular
  season does not count. The wiki stamps a route into a championship with the
  same tier as the championship itself: `Call of Duty Championship 2015/Europe
  Regional Final` is Major, the same word `MLG Pro League 2015 Season 1
  Playoffs` carries, so only the name separates the two.
- Where a tier is known, only tiers 1 and 2 count, and where none is known the
  event is not a title. An unknown tier used to default to the top one, which
  read six 2014-2016 tournaments as titles on the strength of a missing field.
  The wiki publishes Premier and Major for the ones that are, and
  `codwiki.results` stamps those onto `tier` as 1 and 2, so the default has
  nothing left to carry.

What is out of scope never reaches the database: Challengers, Call of Duty King
and the Esports World Cup are dropped at the pull by the pipeline's
`SKIP_PREFIXES`, and the third-party invitationals have no local event. So this
rule sorts the events the project models; it does not decide the circuit.
"""

from __future__ import annotations

# A SQL predicate over an `events` row aliased `e`.
TITLE_EVENT = """(
    coalesce(e.tier_type, '') NOT IN ('Qualifier', 'Showmatch')
    AND e.tier IN ('1', '2')
    AND e.name !~* '(qualif|relegation|play-in|regional final|regular season)'
)"""

# A championship, within the title set: the one event a season ends on. Always
# read with `TITLE_EVENT` beside it, which is what drops the regional finals
# and the last-chance qualifiers that carry the same words in their names.
CHAMPIONSHIP_EVENT = """(
    e.name ~* '(call of duty|world league|cwl|cdl) championship'
)"""

RULE = (
    "a known tier of 1 or 2, tier_type not Qualifier or Showmatch, and a name "
    "that does not say qualifier, relegation, play-in, regional final or "
    "regular season"
)

RING_RULE = "a title event whose name is a Call of Duty, CWL or CDL championship"
