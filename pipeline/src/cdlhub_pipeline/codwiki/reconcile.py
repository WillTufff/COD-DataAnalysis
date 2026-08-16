"""Check the wiki's 2017-2026 claims against the rows we already hold.

The wiki covers the modern era as well as the pre-2017 era, and those years are
already held from `cito` and `cwl_archive`. None of it loads. It is pulled so
that the source can be measured before its pre-2017 half is trusted, because
2013-2016 has nothing to check against.

Nothing here joins on an identifier the two sources share, because they share
none. Pairing runs on whole maps, never on one player's line: a wiki map pairs
with ours when the map name, the mode and the day agree and the two lobbies name
the same players. Pairing one line at a time looks easier and is wrong — a team
plays the same map and mode twice in a day often enough that line-level pairing
crosses two real games and reports the difference as a transcription error.

The win flag is compared without ever mapping a team name: the wiki row says
whether the player's own team won, and our row says whether the player's team id
is the winner. Team names are only folded for the placement check, which has no
other key.

A map the wiki carries and we do not is coverage, not error. The error rate is
measured on paired lines alone and reported as a rate, never as a pass.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast

import psycopg

from . import identity as ident
from .client import SNAPSHOT_ROOT
from .transform import rows_path

MODEL = "codwiki_overlap_reconciliation"
VERSION = "1.0.0"
WINDOW = "overlap"
MAX_DAY_GAP = 1
MIN_LOBBY_OVERLAP = 4
EXAMPLES = 25

# Modes the modern era uses. The wiki's Blitz and the pre-2017 modes never
# appear in this window.
MODE_SLUGS = {
    "Hardpoint": "hardpoint",
    "Search and Destroy": "search-and-destroy",
    "Search & Destroy": "search-and-destroy",
    "Capture the Flag": "capture-the-flag",
    "Uplink": "uplink",
    "Domination": "domination",
    "Control": "control",
    "Overload": "overload",
}

_DB_SOURCES = ("cito", "cwl_archive")


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _map_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _team_key(name: str) -> str:
    """Fold a team display name to something two sources can agree on."""
    folded = (name or "").lower()
    folded = re.sub(r"\b(esports?|gaming|team|the|gg|academy|club)\b", " ", folded)
    return re.sub(r"[^a-z0-9]", "", folded)


@dataclass
class Line:
    player_id: int
    kills: int
    deaths: int
    won: bool
    name: str = ""


@dataclass
class Map:
    """One map: its identity, its lobby, and every line on it."""

    day: date
    map_name: str
    mode: str
    lines: dict[int, Line] = field(default_factory=dict)
    ref: dict[str, Any] = field(default_factory=dict)

    @property
    def lobby(self) -> set[int]:
        return set(self.lines)


def _wiki_maps(
    rows: list[dict[str, Any]], player_ids: dict[str, int]
) -> tuple[list[Map], dict[str, int]]:
    maps: dict[tuple[str, int], Map] = {}
    skipped: Counter[str] = Counter()
    for row in rows:
        link = row.get("PlayerLink") or ""
        kills, deaths = _int(row.get("Kills")), _int(row.get("Deaths"))
        if kills is None or deaths is None:
            skipped["no box score"] += 1
            continue
        if link not in player_ids:
            skipped["unresolved player"] += 1
            continue
        slug = MODE_SLUGS.get(row.get("Gamemode") or "")
        if slug is None:
            skipped["unmodelled mode"] += 1
            continue
        if not (row.get("Map") or "").strip():
            skipped["no map"] += 1
            continue
        if row.get("Win") not in ("0", "1"):
            skipped["no win flag"] += 1
            continue
        if not (row.get("SeriesId") or ""):
            skipped["no series"] += 1
            continue
        try:
            day = date.fromisoformat((row.get("Date") or "")[:10])
        except ValueError:
            skipped["no date"] += 1
            continue
        key = (str(row["SeriesId"]), _int(row.get("GameNumber")) or 1)
        entry = maps.get(key)
        if entry is None:
            entry = maps[key] = Map(
                day=day,
                map_name=_map_key(row["Map"]),
                mode=slug,
                ref={
                    "series": key[0],
                    "game": key[1],
                    "event": row.get("TournamentPage") or "",
                    "title": row.get("GameTitle") or "",
                },
            )
        entry.lines[player_ids[link]] = Line(
            player_id=player_ids[link],
            kills=kills,
            deaths=deaths,
            won=row["Win"] == "1",
            name=row.get("PlayerName") or link,
        )
    return list(maps.values()), dict(skipped)


_DB_QUERY = """
SELECT g.id, s.played_at::date, m.name, gm.slug, gps.player_id, gps.kills, gps.deaths,
       gps.team_id = g.winner_team_id, e.id, e.name, se.year, s.id, gps.data_source
FROM game_player_stats gps
JOIN games g       ON g.id = gps.game_id
JOIN series s      ON s.id = g.series_id
JOIN events e      ON e.id = s.event_id
JOIN seasons se    ON se.id = e.season_id
JOIN game_modes gm ON gm.id = g.mode_id
JOIN maps m        ON m.id = g.map_id
WHERE gps.data_source = ANY(%s) AND g.winner_team_id IS NOT NULL
"""


def _db_maps(conn: psycopg.Connection[tuple[object, ...]]) -> list[Map]:
    maps: dict[int, Map] = {}
    for row in conn.execute(_DB_QUERY, (list(_DB_SOURCES),)).fetchall():
        game_id = cast(int, row[0])
        entry = maps.get(game_id)
        if entry is None:
            entry = maps[game_id] = Map(
                day=cast(date, row[1]),
                map_name=_map_key(cast(str, row[2])),
                mode=cast(str, row[3]),
                ref={
                    "game_id": game_id,
                    "event_id": row[8],
                    "event": row[9],
                    "year": row[10],
                    "series_id": row[11],
                    "source": row[12],
                },
            )
        entry.lines[cast(int, row[4])] = Line(
            player_id=cast(int, row[4]),
            kills=cast(int, row[5]),
            deaths=cast(int, row[6]),
            won=bool(row[7]),
        )
    return list(maps.values())


@dataclass
class Pair:
    wiki: Map
    ours: Map
    ambiguous: bool


def _pair_maps(wiki: list[Map], ours: list[Map]) -> tuple[list[Pair], list[Map], list[Map]]:
    """Pair whole maps on map name, mode and day, ranked by shared lobby.

    Two teams can meet twice on one day and play the same map and mode in both
    meetings, and then the same eight players sit in both lobbies. Nothing in
    either source separates those two games, so a map whose best candidate is
    tied by another is marked ambiguous and kept out of the error rate: picking
    the wrong twin would report two real games as one bad transcription.
    """
    by_key: dict[tuple[str, str, date], list[int]] = defaultdict(list)
    for index, entry in enumerate(ours):
        by_key[(entry.map_name, entry.mode, entry.day)].append(index)

    candidates: list[tuple[int, int, int, int]] = []
    for wi, wmap in enumerate(wiki):
        seen: set[int] = set()
        for offset in range(-MAX_DAY_GAP, MAX_DAY_GAP + 1):
            day = date.fromordinal(wmap.day.toordinal() + offset)
            for di in by_key.get((wmap.map_name, wmap.mode, day), ()):
                if di in seen:
                    continue
                seen.add(di)
                overlap = len(wmap.lobby & ours[di].lobby)
                if overlap >= MIN_LOBBY_OVERLAP:
                    candidates.append((-overlap, abs(offset), wi, di))
    candidates.sort()

    best_w: dict[int, int] = {}
    best_d: dict[int, int] = {}
    for overlap, _gap, wi, di in candidates:
        best_w.setdefault(wi, overlap)
        best_d.setdefault(di, overlap)
    tied_w: Counter[int] = Counter()
    tied_d: Counter[int] = Counter()
    for overlap, _gap, wi, di in candidates:
        if overlap == best_w[wi]:
            tied_w[wi] += 1
        if overlap == best_d[di]:
            tied_d[di] += 1

    pairs: list[Pair] = []
    taken_w: set[int] = set()
    taken_d: set[int] = set()
    for _overlap, _gap, wi, di in candidates:
        if wi in taken_w or di in taken_d:
            continue
        taken_w.add(wi)
        taken_d.add(di)
        ambiguous = tied_w[wi] > 1 or tied_d[di] > 1
        pairs.append(Pair(wiki=wiki[wi], ours=ours[di], ambiguous=ambiguous))
    wiki_only = [entry for i, entry in enumerate(wiki) if i not in taken_w]
    ours_only = [entry for i, entry in enumerate(ours) if i not in taken_d]
    return pairs, wiki_only, ours_only


def _line_pairs(pairs: list[Pair]) -> list[tuple[Map, Map, Line, Line]]:
    """Every player line present on both sides of a confidently paired map."""
    out = []
    for pair in pairs:
        if pair.ambiguous:
            continue
        for player_id, wline in pair.wiki.lines.items():
            dline = pair.ours.lines.get(player_id)
            if dline is not None:
                out.append((pair.wiki, pair.ours, wline, dline))
    return out


def _rate(good: int, total: int) -> float | None:
    return round(good / total, 6) if total else None


def _breakdown(lines: list[tuple[Map, Map, Line, Line]], dim: str) -> list[dict[str, Any]]:
    buckets: dict[Any, Counter[str]] = defaultdict(Counter)
    for wmap, dmap, wline, dline in lines:
        label = wmap.ref["title"] if dim == "title" else dmap.ref["year"]
        counter = buckets[label]
        counter["lines"] += 1
        counter["kills_agree"] += int(wline.kills == dline.kills)
        counter["deaths_agree"] += int(wline.deaths == dline.deaths)
        counter["both_agree"] += int(wline.kills == dline.kills and wline.deaths == dline.deaths)
        counter["win_agree"] += int(wline.won == dline.won)
    out = []
    for label in sorted(buckets, key=str):
        counter = buckets[label]
        total = counter["lines"]
        out.append(
            {
                dim: label,
                "lines": total,
                "kills_rate": _rate(counter["kills_agree"], total),
                "deaths_rate": _rate(counter["deaths_agree"], total),
                "both_rate": _rate(counter["both_agree"], total),
                "win_rate": _rate(counter["win_agree"], total),
                "error_rate": _rate(total - counter["both_agree"], total),
            }
        )
    return out


def _map_agreement(pairs: list[Pair]) -> dict[str, Any]:
    """Sort each confidently paired map by how much of its lobby disagrees.

    A typo hits one line. A map where every shared line differs is not a typo:
    the two sources are describing different games, most often a restart or a
    same-day rematch that neither source numbers the same way. Both counts are
    published, and the second is reported as its own rate rather than folded
    into the first.
    """
    clean = partial = whole = 0
    whole_lines = 0
    examples: list[dict[str, Any]] = []
    for pair in pairs:
        if pair.ambiguous:
            continue
        common = set(pair.wiki.lines) & set(pair.ours.lines)
        if not common:
            continue
        bad = [
            pid
            for pid in common
            if (pair.wiki.lines[pid].kills, pair.wiki.lines[pid].deaths)
            != (pair.ours.lines[pid].kills, pair.ours.lines[pid].deaths)
        ]
        if not bad:
            clean += 1
        elif len(bad) < len(common):
            partial += 1
        else:
            whole += 1
            whole_lines += len(common)
            if len(examples) < EXAMPLES:
                examples.append(
                    {
                        "event": pair.wiki.ref["event"],
                        "our_event": pair.ours.ref["event"],
                        "day": str(pair.wiki.day),
                        "map": pair.wiki.map_name,
                        "mode": pair.wiki.mode,
                        "lines": len(common),
                        "wiki_kills": sum(pair.wiki.lines[p].kills for p in common),
                        "our_kills": sum(pair.ours.lines[p].kills for p in common),
                    }
                )
    total = clean + partial + whole
    return {
        "rule": "a map is whole-lobby when every line the two sources share disagrees",
        "maps": total,
        "clean": clean,
        "one_or_more_lines": partial,
        "whole_lobby": whole,
        "whole_lobby_lines": whole_lines,
        "examples": examples,
    }


def _deltas(lines: list[tuple[Map, Map, Line, Line]]) -> list[dict[str, Any]]:
    """Signed differences per year, which tell a rule apart from a typo.

    A typo scatters around zero and is mostly off by one. A counting rule the two
    sources do not share pushes the mean off zero in one direction.
    """
    buckets: dict[Any, list[tuple[int, int]]] = defaultdict(list)
    for _wmap, dmap, wline, dline in lines:
        buckets[dmap.ref["year"]].append((wline.kills - dline.kills, wline.deaths - dline.deaths))
    out = []
    for year in sorted(buckets):
        pairs = buckets[year]
        kill_d = [d[0] for d in pairs if d[0]]
        death_d = [d[1] for d in pairs if d[1]]
        wrong = [d for d in pairs if d[0] or d[1]]
        out.append(
            {
                "year": year,
                "lines": len(pairs),
                "kill_diffs": len(kill_d),
                "mean_kill_diff": round(sum(kill_d) / len(kill_d), 3) if kill_d else None,
                "death_diffs": len(death_d),
                "mean_death_diff": round(sum(death_d) / len(death_d), 3) if death_d else None,
                "off_by_one_rate": _rate(
                    sum(1 for d in wrong if abs(d[0]) <= 1 and abs(d[1]) <= 1), len(wrong)
                ),
            }
        )
    return out


def _event_map(pairs: list[Pair]) -> dict[str, dict[str, Any]]:
    """Wiki event page to our event, taken from where the paired maps landed."""
    votes: dict[str, Counter[tuple[int, str]]] = defaultdict(Counter)
    for pair in pairs:
        votes[pair.wiki.ref["event"]][(pair.ours.ref["event_id"], pair.ours.ref["event"])] += 1
    mapping = {}
    for page, counter in votes.items():
        (event_id, name), hits = counter.most_common(1)[0]
        mapping[page] = {
            "event_id": event_id,
            "event": name,
            "paired_maps": hits,
            "split": len(counter),
        }
    return mapping


def _map_counts(
    conn: psycopg.Connection[tuple[object, ...]],
    wiki: list[Map],
    mapping: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Per-event map counts on both sides, for the events the pairing identified.

    The two event trees do not have the same shape: the wiki splits one of our
    events into a group stage page and a playoffs page often enough that a
    page-to-event comparison measures the split and not the coverage. Every wiki
    page that pairs into one of our events is therefore counted against that
    event together.
    """
    wiki_counts: Counter[int] = Counter()
    pages: dict[int, list[str]] = defaultdict(list)
    for page, target in mapping.items():
        pages[target["event_id"]].append(page)
    for entry in wiki:
        paired = mapping.get(entry.ref["event"])
        if paired is not None:
            wiki_counts[paired["event_id"]] += 1

    ours: dict[int, int] = {}
    for row in conn.execute(
        "SELECT e.id, count(DISTINCT g.id) FROM games g "
        "JOIN series s ON s.id = g.series_id JOIN events e ON e.id = s.event_id "
        "WHERE g.data_source = ANY(%s) GROUP BY e.id",
        (list(_DB_SOURCES),),
    ).fetchall():
        ours[cast(int, row[0])] = cast(int, row[1])

    names = {target["event_id"]: target["event"] for target in mapping.values()}
    entries = []
    within = 0
    for event_id, wiki_n in sorted(wiki_counts.items()):
        our_n = ours.get(event_id, 0)
        gap = abs(wiki_n - our_n) / wiki_n if wiki_n else None
        if gap is not None and gap <= 0.02:
            within += 1
        entries.append(
            {
                "event": names[event_id],
                "wiki_pages": sorted(pages[event_id]),
                "wiki_maps": wiki_n,
                "our_maps": our_n,
                "gap": round(gap, 4) if gap is not None else None,
            }
        )
    entries.sort(key=lambda e: -(e["gap"] or 0))
    return {
        "rule": "an event pairs when a map inside it paired; 2% is the plan's bound",
        "events": len(entries),
        "within_2pct": within,
        "within_2pct_rate": _rate(within, len(entries)),
        "wiki_holds_more": sum(1 for e in entries if e["wiki_maps"] > e["our_maps"]),
        "we_hold_more": sum(1 for e in entries if e["wiki_maps"] < e["our_maps"]),
        "by_event": entries,
    }


def _series_winners(pairs: list[Pair]) -> dict[str, Any]:
    """Roll the paired maps up to a series and compare who took it."""
    wiki_wins: dict[tuple[str, int], list[bool]] = defaultdict(list)
    our_wins: dict[tuple[str, int], list[bool]] = defaultdict(list)
    for pair in pairs:
        if pair.ambiguous:
            continue
        for player_id, wline in pair.wiki.lines.items():
            dline = pair.ours.lines.get(player_id)
            if dline is None:
                continue
            key = (pair.wiki.ref["series"], player_id)
            wiki_wins[key].append(wline.won)
            our_wins[key].append(dline.won)
    agree = disagree = undecided = 0
    for key, wins in wiki_wins.items():
        ours = our_wins[key]
        if sum(wins) * 2 == len(wins) or sum(ours) * 2 == len(ours):
            undecided += 1  # an even split names no winner on that side
            continue
        if (sum(wins) * 2 > len(wins)) == (sum(ours) * 2 > len(ours)):
            agree += 1
        else:
            disagree += 1
    total = agree + disagree
    return {
        "rule": "a player wins the series when the player wins most of the paired maps in it",
        "series_player_pairs": total,
        "undecided": undecided,
        "agree": agree,
        "disagree": disagree,
        "rate": _rate(agree, total),
    }


def _placements(
    conn: psycopg.Connection[tuple[object, ...]], mapping: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Compare the wiki's first four places against ours, on the paired events."""
    results = json.loads((SNAPSHOT_ROOT / "tournamentresults.json").read_text())
    wiki_top: dict[str, dict[int, str]] = defaultdict(dict)
    for row in results:
        page = row.get("OverviewPage") or ""
        place = _int(row.get("Place Number"))
        if page in mapping and place is not None and 1 <= place <= 4:
            wiki_top[page].setdefault(place, _team_key(row.get("Team") or ""))

    ours: dict[int, dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in conn.execute(
        "SELECT p.event_id, p.placement_min, t.name FROM event_placements p "
        "JOIN teams t ON t.id = p.team_id WHERE p.placement_min BETWEEN 1 AND 4"
    ).fetchall():
        ours[cast(int, row[0])][cast(int, row[1])].add(_team_key(cast(str, row[2])))

    checked = agree = 0
    events_checked = 0
    mismatches: list[dict[str, Any]] = []
    for page, places in sorted(wiki_top.items()):
        target = mapping[page]
        our_places = ours.get(target["event_id"])
        if not our_places:
            continue
        events_checked += 1
        for place, team in sorted(places.items()):
            if place not in our_places or not team:
                continue
            checked += 1
            if team in our_places[place]:
                agree += 1
            elif len(mismatches) < EXAMPLES:
                mismatches.append(
                    {
                        "event": target["event"],
                        "place": place,
                        "wiki": team,
                        "ours": sorted(our_places[place]),
                    }
                )
    return {
        "rule": "team names are folded to letters and digits, then compared per place",
        "events_checked": events_checked,
        "wiki_events_with_a_result": len(wiki_top),
        "places_checked": checked,
        "agree": agree,
        "rate": _rate(agree, checked),
        "mismatches": mismatches,
    }


def payload(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = json.loads(rows_path(WINDOW).read_text())
    usable = [
        r for r in rows if (r.get("PlayerName") or "").strip() and (r.get("Kills") or "") != ""
    ]
    overrides = json.loads(
        (SNAPSHOT_ROOT.parents[1] / "src" / "cdlhub_pipeline" / "aliases.json").read_text()
    ).get("codwiki_players", {})
    player_ids, id_report = ident.resolve(conn, usable, overrides, create=False)

    wiki, skipped = _wiki_maps(rows, player_ids)
    ours = _db_maps(conn)
    pairs, wiki_only, ours_only = _pair_maps(wiki, ours)
    lines = _line_pairs(pairs)

    both = sum(1 for _w, _d, wl, dl in lines if (wl.kills, wl.deaths) == (dl.kills, dl.deaths))
    kills = sum(1 for _w, _d, wl, dl in lines if wl.kills == dl.kills)
    deaths = sum(1 for _w, _d, wl, dl in lines if wl.deaths == dl.deaths)
    wins = sum(1 for _w, _d, wl, dl in lines if wl.won == dl.won)
    examples = [
        {
            "player": wl.name,
            "event": wmap.ref["event"],
            "our_event": dmap.ref["event"],
            "day": str(wmap.day),
            "map": wmap.map_name,
            "mode": wmap.mode,
            "wiki": [wl.kills, wl.deaths],
            "ours": [dl.kills, dl.deaths],
        }
        for wmap, dmap, wl, dl in lines
        if (wl.kills, wl.deaths) != (dl.kills, dl.deaths)
    ][:EXAMPLES]

    mapping = _event_map(pairs)
    agreement = _map_agreement(pairs)
    typo_lines = len(lines) - agreement["whole_lobby_lines"]
    typo_errors = len(lines) - both - agreement["whole_lobby_lines"]
    return {
        "window": "2017-2026, pulled for checking only and never loaded",
        "rule": (
            "a wiki map pairs with ours on map name, mode and day, ranked by how "
            "much of the lobby the two name in common; the error rate counts "
            "paired lines whose kills or deaths disagree"
        ),
        "identity": {k: (len(v) if isinstance(v, list) else v) for k, v in id_report.items()},
        "maps": {
            "wiki_rows": len(rows),
            "wiki_maps": len(wiki),
            "wiki_rows_skipped": skipped,
            "our_maps": len(ours),
            "paired": len(pairs),
            "paired_confidently": sum(1 for p in pairs if not p.ambiguous),
            "paired_ambiguously": sum(1 for p in pairs if p.ambiguous),
            "wiki_only": len(wiki_only),
            "ours_only": len(ours_only),
            "coverage_of_ours": _rate(len(pairs), len(ours)),
        },
        "player_maps": {
            "lines": len(lines),
            "kills_rate": _rate(kills, len(lines)),
            "deaths_rate": _rate(deaths, len(lines)),
            "both_rate": _rate(both, len(lines)),
            "win_rate": _rate(wins, len(lines)),
            "transcription_error_rate": _rate(len(lines) - both, len(lines)),
            "transcription_error_rate_excluding_whole_lobby": _rate(typo_errors, typo_lines),
        },
        "map_agreement": agreement,
        "by_title": _breakdown(lines, "title"),
        "by_year": _breakdown(lines, "year"),
        "deltas_by_year": _deltas(lines),
        "map_counts": _map_counts(conn, wiki, mapping),
        "series_winners": _series_winners(pairs),
        "placements": _placements(conn, mapping),
        "disagreements": examples,
    }


def store(conn: psycopg.Connection[tuple[object, ...]], result: dict[str, Any]) -> int:
    """Persist as a model_artifacts row, replacing any prior run in place."""
    row = conn.execute(
        "SELECT max(s.played_at)::date FROM series s WHERE s.data_source = ANY(%s)",
        (list(_DB_SOURCES),),
    ).fetchone()
    data_through = row[0] if row else None
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = None
    existing = conn.execute(
        "SELECT id FROM model_runs WHERE model = %s AND version = %s AND data_through = %s",
        (MODEL, VERSION, data_through),
    ).fetchone()
    if existing is not None:
        conn.execute("DELETE FROM model_runs WHERE id = %s", (existing[0],))
    run_id = cast(
        int,
        conn.execute(
            "INSERT INTO model_runs (model, version, code_ref, params, data_through) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (MODEL, VERSION, sha, json.dumps({"window": WINDOW}), data_through),
        ).fetchone()[0],  # type: ignore[index]
    )
    conn.execute(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        (run_id, MODEL, json.dumps(result, default=str)),
    )
    return run_id
