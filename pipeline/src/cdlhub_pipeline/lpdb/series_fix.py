"""Reconcile local series against LPDB /match (match2) data, both eras.

Three fixes for the Cito era, all driven by the Cito load report and idempotent
per run, plus one link for the CWL era (`link_cwl_series`) that repairs nothing
and exists to disagree:

1. Backfill the four quarantined 2020 OGLA-vs-RØKKR series (Cito files both
   sides under the same slug, so orientation is unrecoverable there). LPDB
   supplies teams, orientation, scores, and map results; the series rows load
   with data_source='lpdb', keeping the Cito matchId as source_uid so a future
   Cito fix converges onto the same row.
2. Cross-check the winner-nulled series (per-map results contradicted the
   series score at Cito load) and fill map winners/scores from LPDB where the
   LPDB map set is consistent with the authoritative series score. Filled game
   rows are retagged data_source='lpdb' — the tag names where the RESULT came
   from; their stat lines stay Cito-tagged.
3. Cross-check the score-only cdl-catalog fixtures (numeric source_uid, no
   games) and create their game rows from LPDB map data.

4. Link 2017-2019 series to their LPDB match rows, backfilling `best_of` —
   null on every CWL series here, populated on all 1,230 of LPDB's — and
   publishing a score reconciliation. Until this ran, every CWL number the
   project publishes rested on the Activision archive alone.

A cito reload nulls/loses these fixes, so rerun `lpdb load` after any
`cito load`. Disagreements between the local series score and LPDB are
reported, never resolved silently, in either era.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from typing import Any, cast

import psycopg

from ..cito.client import REPO_ROOT
from ..cito.transform import event_name
from .load import SOURCE, LpdbLoader
from .pull import GAME_SEASONS

CITO_REPORT_PATH = REPO_ROOT / "pipeline" / "snapshots" / "cito" / "load-report.json"
CITO_MATCH_LISTS = REPO_ROOT / "pipeline" / "snapshots" / "cito" / "match-lists"

# LPDB match2game mode strings -> local game_modes slug ('team'/'Solo'/'uplink'
# are pre-CDL or non-map records and stay unmapped on purpose)
MODE_SLUGS = {
    "hardpoint": "hardpoint",
    "hp": "hardpoint",
    "search and destroy": "search-and-destroy",
    "search  and destroy": "search-and-destroy",
    "snd": "search-and-destroy",
    "control": "control",
    "con": "control",
    "domination": "domination",
    "dom": "domination",
    "overload": "overload",
    "ovl": "overload",
}


def _utc_date(row: dict[str, Any]) -> date | None:
    extradata = row.get("extradata") or {}
    ts = extradata.get("timestamp") if isinstance(extradata, dict) else None
    if ts and int(ts) > 0:
        return datetime.fromtimestamp(int(ts), tz=UTC).date()
    raw = cast(str, row.get("date") or "")
    if not raw or raw.startswith(("0000", "1970")):
        return None
    return datetime.fromisoformat(raw).date()


def _disagreement_kind(archive: tuple[int, int], lpdb: list[int]) -> str:
    """What kind of disagreement this is, which is most of the diagnosis.

    The archive reporting fewer maps than LPDB is the shape already known from
    the raw CSVs — a series whose deciding map was never written. A different
    winner is the one that matters, and 2-2 against 3-2 is the same missing map
    showing up as an undecided series rather than as a short one.
    """
    if (archive[0] > archive[1]) != (lpdb[0] > lpdb[1]):
        return "different winner"
    total_archive, total_lpdb = sum(archive), sum(lpdb)
    if total_archive < total_lpdb:
        return "archive has fewer maps"
    if total_archive > total_lpdb:
        return "archive has more maps"
    return "same total, different split"


class SeriesFixer:
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]], loader: LpdbLoader):
        self.conn = conn
        self.loader = loader
        self.counts: dict[str, int] = defaultdict(int)
        self.report: dict[str, Any] = {
            "backfilled": [],
            "nulled_filled": [],
            "scoreonly_games_added": [],
            "score_agreements": 0,
            "score_disagreements": [],
            "phantom_fixtures_deleted": [],
            "dates_corrected": [],
            "unmatched": [],
            "inconsistent_lpdb_maps": [],
            # The CWL era's second source (link_cwl_series), reported the same
            # way the modern era's reconciliation already is.
            "cwl_score_agreements": 0,
            "cwl_score_disagreements": [],
            "cwl_score_unscored": 0,
            "cwl_unmatched_by_event": {},
            "cwl_disagreement_kinds": {},
        }
        # (frozenset of lowercased canonical team names) -> [match records]
        self._index: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
        self._mode_ids: dict[str, int] = {
            cast(str, slug): cast(int, mid)
            for mid, slug in self.conn.execute("SELECT id, slug FROM game_modes").fetchall()
        }

    # ----- LPDB match index -----

    def build_index(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            opponents = row.get("match2opponents") or []
            if len(opponents) != 2 or not row.get("finished"):
                continue
            season = GAME_SEASONS[row["game"]]
            names = []
            scores = []
            for op in opponents:
                name = op.get("name") or ""
                score = op.get("score")
                if not name or not isinstance(score, int) or score < 0:
                    break
                names.append(self.loader.canonical_team(name, season))
                scores.append(score)
            when = _utc_date(row)
            if len(names) != 2 or when is None:
                continue
            extradata = row.get("extradata") or {}
            ts = extradata.get("timestamp") if isinstance(extradata, dict) else None
            self._index[frozenset(n.lower() for n in names)].append(
                {
                    "match2id": row["match2id"],
                    "date": when,
                    "dt": (datetime.fromtimestamp(int(ts), tz=UTC) if ts and int(ts) > 0 else None),
                    "season": season,
                    "pagename": row["pagename"],
                    "names": names,
                    "scores": scores,
                    "winner": row.get("winner"),
                    "bestof": row.get("bestof"),
                    "games": row.get("match2games") or [],
                }
            )

    def find(self, name1: str, name2: str, when: date, window: int = 1) -> list[dict[str, Any]]:
        pair = frozenset((name1.lower(), name2.lower()))
        return [m for m in self._index.get(pair, []) if abs((m["date"] - when).days) <= window]

    # ----- map helpers -----

    def _game_result(self, g: dict[str, Any]) -> tuple[list[int], int] | None:
        """(scores oriented to LPDB opponent order, winner index 1/2), or None
        for unplayed/degenerate map records."""
        winner = str(g.get("winner") or "")
        if winner not in ("1", "2"):
            return None
        scores = g.get("scores")
        if isinstance(scores, dict):
            scores = [scores.get("1"), scores.get("2")]
        if not (
            isinstance(scores, list)
            and len(scores) == 2
            and all(isinstance(s, int) for s in scores)
        ):
            scores = [None, None]
        return cast(list[int], scores), int(winner)

    def _map_mode(self, g: dict[str, Any]) -> tuple[str | None, int | None]:
        """(map name, mode_id) from an LPDB game record, best-effort."""
        map_name = str(g.get("map") or "") or None
        slug = MODE_SLUGS.get(str(g.get("mode") or "").lower())
        return map_name, self._mode_ids.get(slug) if slug else None

    def _title_id(self, season: int) -> int | None:
        row = self.conn.execute(
            "SELECT t.id FROM seasons se JOIN titles t ON t.id = se.title_id "
            "WHERE se.year = %s AND se.league = 'CDL'",
            (season,),
        ).fetchone()
        return cast(int, row[0]) if row else None

    def _map_id(self, name: str | None, season: int) -> int | None:
        if not name:
            return None
        title_id = self._title_id(season)
        if title_id is None:
            return None
        self.conn.execute(
            "INSERT INTO maps (name, title_id) VALUES (%s, %s) "
            "ON CONFLICT (name, title_id) DO NOTHING",
            (name, title_id),
        )
        row = self.conn.execute(
            "SELECT id FROM maps WHERE name = %s AND title_id = %s", (name, title_id)
        ).fetchone()
        return cast(int, row[0]) if row else None

    def _set_match_id(self, series_id: int, match2id: str) -> None:
        self.conn.execute(
            "UPDATE series SET liquipedia_match_id = %s WHERE id = %s "
            "AND liquipedia_match_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM series WHERE liquipedia_match_id = %s)",
            (match2id, series_id, match2id),
        )

    def _insert_games(
        self,
        series_id: int,
        match: dict[str, Any],
        local_team_ids: tuple[int, int],
        order: list[int],
        expected: tuple[int, int],
    ) -> bool:
        """Create game rows for a series that has none, oriented to the local
        series' team order via `order` (LPDB opponent index of local team1,
        team2). Only writes a map set whose win tally reproduces the local
        series score exactly."""
        results = [self._game_result(g) for g in match["games"]]
        played = [(g, r) for g, r in zip(match["games"], results, strict=True) if r is not None]
        tally = [0, 0]
        for _, (_, winner) in played:
            tally[order.index(winner - 1)] += 1
        if (tally[0], tally[1]) != expected:
            self.report["inconsistent_lpdb_maps"].append(
                {"match2id": match["match2id"], "lpdb_tally": tally, "expected": list(expected)}
            )
            return False
        for ordinal, (g, (scores, winner)) in enumerate(played, start=1):
            map_name, mode_id = self._map_mode(g)
            self.conn.execute(
                """
                INSERT INTO games (series_id, ordinal, map_id, mode_id, team1_score,
                                   team2_score, winner_team_id, source_uid, data_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (series_id, ordinal) DO UPDATE SET
                  map_id = EXCLUDED.map_id, mode_id = EXCLUDED.mode_id,
                  team1_score = EXCLUDED.team1_score,
                  team2_score = EXCLUDED.team2_score,
                  winner_team_id = EXCLUDED.winner_team_id,
                  source_uid = EXCLUDED.source_uid, data_source = EXCLUDED.data_source
                """,
                (
                    series_id,
                    ordinal,
                    self._map_id(map_name, match["season"]),
                    mode_id,
                    scores[order[0]],
                    scores[order[1]],
                    local_team_ids[order.index(winner - 1)],
                    f"lpdb:{match['match2id']}:{ordinal}",
                    SOURCE,
                ),
            )
            self.counts["games"] += 1
        return True

    # ----- fix 1: quarantined 2020 backfill -----

    def _quarantined_fixtures(self) -> list[dict[str, Any]]:
        report = json.loads(CITO_REPORT_PATH.read_text())
        wanted = {
            q["match_id"]
            for q in report.get("quarantined", [])
            if "same team slug" in q.get("reason", "")
        }
        fixtures = []
        for path in sorted(CITO_MATCH_LISTS.glob("season-*.json")):
            for m in json.loads(path.read_text()).get("data", []):
                if m.get("matchId") in wanted:
                    fixtures.append(m)
                    wanted.discard(m["matchId"])
        for miss in sorted(wanted):
            self.report["unmatched"].append({"cito": miss, "reason": "fixture not in match-lists"})
        return fixtures

    def backfill_quarantined(self) -> None:
        # the split spec names the two teams the shared slug hides
        specs = self.loader.aliases.cito_slug_rosters
        for fixture in self._quarantined_fixtures():
            match_id = fixture["matchId"]
            slug = (fixture.get("teams") or {}).get("team1", {}).get("slug", "")
            spec = specs.get(slug)
            if spec is None:
                self.report["unmatched"].append(
                    {"cito": match_id, "reason": f"no split spec for {slug}"}
                )
                continue
            pair = list(spec["teams"])
            when = datetime.fromisoformat(fixture["matchDate"].replace("Z", "+00:00"))
            candidates = self.find(pair[0], pair[1], when.date())
            cito_scores = sorted(
                (fixture.get("score") or {}).get(k, -1) for k in ("team1", "team2")
            )
            candidates = [c for c in candidates if sorted(c["scores"]) == cito_scores]
            if len(candidates) != 1:
                self.report["unmatched"].append(
                    {"cito": match_id, "reason": f"{len(candidates)} LPDB candidates"}
                )
                continue
            m = candidates[0]
            season = m["season"]
            event_row = self.conn.execute(
                "SELECT e.id FROM events e JOIN seasons se ON se.id = e.season_id "
                "WHERE se.year = %s AND se.league = 'CDL' AND e.name = %s",
                (season, event_name((fixture.get("tournament") or {}).get("name") or "")),
            ).fetchone()
            if event_row is None:
                self.report["unmatched"].append({"cito": match_id, "reason": "local event missing"})
                continue
            team_ids = [cast(int, self.loader.team_id(n, season, create=True)) for n in m["names"]]
            row = self.conn.execute(
                """
                INSERT INTO series (event_id, team1_id, team2_id, team1_score, team2_score,
                                    best_of, played_at, round_label, source_uid,
                                    liquipedia_match_id, data_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_uid) DO UPDATE SET
                  team1_id = EXCLUDED.team1_id, team2_id = EXCLUDED.team2_id,
                  team1_score = EXCLUDED.team1_score, team2_score = EXCLUDED.team2_score,
                  liquipedia_match_id = EXCLUDED.liquipedia_match_id
                RETURNING id
                """,
                (
                    cast(int, event_row[0]),
                    team_ids[0],
                    team_ids[1],
                    m["scores"][0],
                    m["scores"][1],
                    fixture.get("bestOf"),
                    when,
                    fixture.get("round"),
                    match_id,
                    m["match2id"],
                    SOURCE,
                ),
            ).fetchone()
            assert row is not None
            series_id = cast(int, row[0])
            self.counts["series"] += 1
            games_ok = self._insert_games(
                series_id,
                m,
                (team_ids[0], team_ids[1]),
                [0, 1],
                (m["scores"][0], m["scores"][1]),
            )
            self.report["backfilled"].append(
                {
                    "cito": match_id,
                    "match2id": m["match2id"],
                    "teams": m["names"],
                    "score": m["scores"],
                    "games": games_ok,
                }
            )

    # ----- fixes 2+3: cross-checks -----

    def _local_series(self, where: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            f"""
            SELECT s.id, s.source_uid, s.played_at, s.team1_score, s.team2_score,
                   t1.name AS team1, t2.name AS team2, se.year
            FROM series s
            JOIN teams t1 ON t1.id = s.team1_id JOIN teams t2 ON t2.id = s.team2_id
            JOIN events e ON e.id = s.event_id JOIN seasons se ON se.id = e.season_id
            WHERE {where}
            ORDER BY s.played_at
            """,  # noqa: S608 (where is a literal)
            params,
        )
        cols = [d.name for d in cur.description or []]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def _match_local(self, s: dict[str, Any], window: int = 1) -> dict[str, Any] | None:
        """The unique LPDB match for a local series, score-verified; None and
        reported otherwise."""
        when = cast(datetime, s["played_at"]).date()
        candidates = self.find(s["team1"], s["team2"], when, window)
        if len(candidates) > 1:
            candidates = [
                c
                for c in candidates
                if sorted(c["scores"]) == sorted((s["team1_score"], s["team2_score"]))
            ]
        if len(candidates) > 1:
            # a wide window can span a rematch; the oriented score disambiguates
            candidates = [
                c
                for c in candidates
                if (
                    c["scores"][0 if c["names"][0].lower() == s["team1"].lower() else 1],
                    c["scores"][1 if c["names"][0].lower() == s["team1"].lower() else 0],
                )
                == (s["team1_score"], s["team2_score"])
            ]
        if len(candidates) != 1:
            self.report["unmatched"].append(
                {"cito": s["source_uid"], "reason": f"{len(candidates)} LPDB candidates"}
            )
            return None
        m = candidates[0]
        # orient LPDB scores to the local team order
        order = [0, 1] if m["names"][0].lower() == s["team1"].lower() else [1, 0]
        lpdb_scores = (m["scores"][order[0]], m["scores"][order[1]])
        if lpdb_scores != (s["team1_score"], s["team2_score"]):
            self.report["score_disagreements"].append(
                {
                    "cito": s["source_uid"],
                    "match2id": m["match2id"],
                    "cito_score": [s["team1_score"], s["team2_score"]],
                    "lpdb_score": list(lpdb_scores),
                }
            )
            return None
        self.report["score_agreements"] += 1
        m = {**m, "order": order}
        return m

    def check_nulled(self) -> None:
        report = json.loads(CITO_REPORT_PATH.read_text())
        nulled = report.get("inconsistent_map_results", [])
        if not nulled:
            return
        for s in self._local_series("s.source_uid = ANY(%s)", (nulled,)):
            m = self._match_local(s)
            if m is None:
                continue
            self._set_match_id(cast(int, s["id"]), m["match2id"])
            games = self.conn.execute(
                "SELECT id, ordinal, winner_team_id FROM games "
                "WHERE series_id = %s ORDER BY ordinal",
                (s["id"],),
            ).fetchall()
            results = [self._game_result(g) for g in m["games"]]
            played = [r for r in results if r is not None]
            if len(played) != len(games):
                self.report["inconsistent_lpdb_maps"].append(
                    {
                        "cito": s["source_uid"],
                        "match2id": m["match2id"],
                        "lpdb_maps": len(played),
                        "local_maps": len(games),
                    }
                )
                continue
            # LPDB winner tally must reproduce the authoritative series score
            order = m["order"]
            tally = [0, 0]
            for _scores, winner in played:
                tally[order.index(winner - 1)] += 1
            if tuple(tally) != (s["team1_score"], s["team2_score"]):
                self.report["inconsistent_lpdb_maps"].append(
                    {
                        "cito": s["source_uid"],
                        "match2id": m["match2id"],
                        "lpdb_tally": tally,
                        "series_score": [s["team1_score"], s["team2_score"]],
                    }
                )
                continue
            team_ids = self.conn.execute(
                "SELECT team1_id, team2_id FROM series WHERE id = %s", (s["id"],)
            ).fetchone()
            assert team_ids is not None
            filled = 0
            for (gid, _, old_winner), (scores, winner) in zip(games, played, strict=True):
                if old_winner is not None:
                    continue
                local_side = order.index(winner - 1)  # 0 -> team1, 1 -> team2
                oriented = (scores[order[0]], scores[order[1]])
                self.conn.execute(
                    "UPDATE games SET winner_team_id = %s, team1_score = %s, "
                    "team2_score = %s, data_source = %s WHERE id = %s",
                    (
                        cast(int, team_ids[local_side]),
                        oriented[0],
                        oriented[1],
                        SOURCE,
                        gid,
                    ),
                )
                filled += 1
            self.counts["games_winner_filled"] += filled
            self.report["nulled_filled"].append(
                {"cito": s["source_uid"], "match2id": m["match2id"], "maps_filled": filled}
            )

    def check_score_only(self) -> None:
        rows = self._local_series(
            r"s.source_uid ~ '^[0-9]+$' AND s.data_source = 'cito' "
            "AND NOT EXISTS (SELECT 1 FROM games g WHERE g.series_id = s.id)",
            (),
        )
        for s in rows:
            when = cast(datetime, s["played_at"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            # a stale score-only cdl fixture with no LPDB match anywhere near
            # its claimed date is a phantom record — delete it; the staleness
            # guard keeps current-season fixtures alive until LPDB catches up
            stale = (datetime.now(UTC) - when).days > 14
            if stale and not self.find(s["team1"], s["team2"], when.date(), window=7):
                self.conn.execute("DELETE FROM series WHERE id = %s", (s["id"],))
                self.counts["phantom_series_deleted"] += 1
                self.report["phantom_fixtures_deleted"].append(
                    {
                        "cito": s["source_uid"],
                        "teams": [s["team1"], s["team2"]],
                        "date": str(when.date()),
                    }
                )
                continue
            # cdl-catalog fixture dates drift days from reality; the oriented
            # score + team pair pins the real match inside a week-wide window
            m = self._match_local(s, window=7)
            if m is None:
                continue
            self._set_match_id(cast(int, s["id"]), m["match2id"])
            local_date = cast(datetime, s["played_at"]).date()
            if m["dt"] is not None and m["date"] != local_date:
                self.conn.execute(
                    "UPDATE series SET played_at = %s WHERE id = %s", (m["dt"], s["id"])
                )
                self.report["dates_corrected"].append(
                    {
                        "cito": s["source_uid"],
                        "match2id": m["match2id"],
                        "old": str(local_date),
                        "new": str(m["date"]),
                    }
                )
            team_row = self.conn.execute(
                "SELECT team1_id, team2_id FROM series WHERE id = %s", (s["id"],)
            ).fetchone()
            assert team_row is not None
            ok = self._insert_games(
                cast(int, s["id"]),
                m,
                (cast(int, team_row[0]), cast(int, team_row[1])),
                m["order"],
                (cast(int, s["team1_score"]), cast(int, s["team2_score"])),
            )
            if ok:
                self.report["scoreonly_games_added"].append(
                    {"cito": s["source_uid"], "match2id": m["match2id"]}
                )

    def run(self, matches: list[dict[str, Any]]) -> None:
        self.build_index(matches)
        # wipe fixer-owned rows so alias edits converge; winner-fills on cito
        # games are updates and simply re-apply
        self.conn.execute(
            "DELETE FROM games WHERE data_source = %s AND source_uid LIKE 'lpdb:%%'", (SOURCE,)
        )
        # A walk-forward team rating names the series it was computed at, so a
        # fitted run pins the very rows this reload replaces and the delete
        # below fails against the foreign key. The ratings for a reloaded series
        # are stale by definition and `run_all` refits them, so they go first —
        # without this, `lpdb load` can only run on a database no model has been
        # fitted against, which is not a state the ops app ever leaves it in.
        cur = self.conn.execute(
            "DELETE FROM team_ratings WHERE series_id IN "
            "(SELECT id FROM series WHERE data_source = %s)",
            (SOURCE,),
        )
        if cur.rowcount:
            self.counts["stale_team_ratings_cleared"] += cur.rowcount
        self.conn.execute("DELETE FROM series WHERE data_source = %s", (SOURCE,))
        self.backfill_quarantined()
        self.check_nulled()
        self.check_score_only()
        # a score-only cdl fixture with no LPDB counterpart anywhere near its
        # claimed date is a phantom record (verified by hand for the Dec 2025
        # slate: the claimed pairings happened weeks later or not at all) —
        # candidates for deletion, surfaced separately for the owner
        self.report["phantom_fixture_candidates"] = [
            u for u in self.report["unmatched"] if str(u.get("cito", "")).isdigit()
        ]
        self.report["unmatched"] = [
            u for u in self.report["unmatched"] if not str(u.get("cito", "")).isdigit()
        ]
        self.link_cwl_series()

    # ----- CWL era: a second source for a record that has one -----

    CWL_SERIES_SQL = """
    SELECT s.id, e.name, se.year, t1.name, t2.name, s.played_at::date,
           s.team1_score, s.team2_score, s.best_of
    FROM series s
    JOIN events e   ON e.id = s.event_id
    JOIN seasons se ON se.id = e.season_id
    JOIN teams t1   ON t1.id = s.team1_id
    JOIN teams t2   ON t2.id = s.team2_id
    WHERE se.league = 'CWL'
    ORDER BY s.played_at, s.id
    """

    def link_cwl_series(self) -> None:
        """Match 2017-2019 series to LPDB match rows, and say where they disagree.

        Every CWL number the project publishes rests on the Activision archive
        alone. LPDB is an independent record of the same series: it carries
        `bestof` on all 1,230 of its CWL match rows against `best_of` being null
        on every CWL series here, and its scores are a second opinion the
        archive has never had to agree with.

        LPDB's CWL match coverage is Pro League and Championship only — it has
        no rows at all for the open events (Anaheim, Atlanta, Birmingham,
        Dallas, New Orleans, Seattle, Fort Worth, London). Those series are
        reported unmatched by event, so the shape of the gap is visible rather
        than reading as a matching failure.

        Disagreements are reported, never resolved: the archive stays
        authoritative and LPDB stays the check on it.
        """
        rows = self.conn.execute(self.CWL_SERIES_SQL).fetchall()
        if not rows:
            return
        # Cleared first so a run converges after an alias edit rather than
        # keeping whichever match a previous run happened to reach.
        self.conn.execute(
            "UPDATE series SET liquipedia_match_id = NULL WHERE id IN "
            "(SELECT s.id FROM series s JOIN events e ON e.id = s.event_id "
            " JOIN seasons se ON se.id = e.season_id WHERE se.league = 'CWL')"
        )
        unmatched: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        for row in rows:
            series_id = cast(int, row[0])
            event, year = cast(str, row[1]), cast(int, row[2])
            name1, name2 = cast(str, row[3]), cast(str, row[4])
            played = cast(date, row[5])
            local = (cast("int | None", row[6]), cast("int | None", row[7]))
            best_of = cast("int | None", row[8])

            match = self._pick_cwl_match(self.find(name1, name2, played), name1, local)
            if match is None:
                unmatched[f"{year} {event}"] += 1
                continue

            self._set_match_id(series_id, cast(str, match["match2id"]))
            self.counts["cwl_series_linked"] += 1
            if best_of is None and match.get("bestof"):
                self.conn.execute(
                    "UPDATE series SET best_of = %s WHERE id = %s AND best_of IS NULL",
                    (match["bestof"], series_id),
                )
                self.counts["cwl_best_of_filled"] += 1

            theirs = self._cwl_scores(match, name1)
            if None in local or theirs is None:
                self.report["cwl_score_unscored"] += 1
            elif tuple(theirs) == local:
                self.report["cwl_score_agreements"] += 1
            else:
                kind = _disagreement_kind(cast(tuple[int, int], local), theirs)
                kinds[kind] += 1
                self.report["cwl_score_disagreements"].append(
                    {
                        "series_id": series_id,
                        "event": f"{year} {event}",
                        "teams": f"{name1} vs {name2}",
                        "date": str(played),
                        "archive": f"{local[0]}-{local[1]}",
                        "lpdb": f"{theirs[0]}-{theirs[1]}",
                        "kind": kind,
                        "match2id": match["match2id"],
                    }
                )
        self.report["cwl_unmatched_by_event"] = dict(sorted(unmatched.items()))
        self.report["cwl_disagreement_kinds"] = dict(sorted(kinds.items()))

    def _cwl_scores(self, match: dict[str, Any], local_team1: str) -> list[int] | None:
        """The LPDB match's scores, oriented to the local series' team order."""
        names = [str(n).lower() for n in match["names"]]
        scores = cast(list[int], match["scores"])
        try:
            first = names.index(local_team1.lower())
        except ValueError:
            return None
        return scores if first == 0 else [scores[1], scores[0]]

    def _pick_cwl_match(
        self,
        candidates: list[dict[str, Any]],
        local_team1: str,
        local: tuple[int | None, int | None],
    ) -> dict[str, Any] | None:
        """One match, or none. Two teams can meet twice on the same day, so a
        tie is broken by the score and left unmatched if that does not settle
        it — a wrong link is worse than a missing one for a source whose whole
        job is to disagree."""
        if len(candidates) == 1:
            return candidates[0]
        if not candidates or None in local:
            return None
        agreeing = [m for m in candidates if self._cwl_scores(m, local_team1) == list(local)]
        return agreeing[0] if len(agreeing) == 1 else None
