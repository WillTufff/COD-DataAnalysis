import { sql } from "drizzle-orm";
import { db } from "@/lib/db";
import { playerSlug, teamSlug } from "@/lib/slug";

type Row = Record<string, unknown>;
const rows = async (q: ReturnType<typeof sql>) =>
  (await db.execute(q)) as unknown as Row[];

export type CurrentSeason = { id: number; year: number; title: string; titleName: string; league: string };

export async function getCurrentSeason(): Promise<CurrentSeason> {
  const [r] = await rows(sql`
    SELECT s.id, s.year, t.short_name, t.name, s.league
    FROM seasons s JOIN titles t ON t.id = s.title_id
    ORDER BY s.year DESC LIMIT 1`);
  return {
    id: Number(r.id),
    year: Number(r.year),
    title: String(r.short_name),
    titleName: String(r.name),
    league: String(r.league),
  };
}

export type SeasonSeries = {
  id: number;
  t: number;
  team1: string;
  team2: string;
  s1: number;
  s2: number;
  round: string | null;
};

export type SeasonEvent = {
  id: number;
  name: string;
  start: string;
  end: string;
  lan: boolean;
  prize: number | null;
  winner: string | null;
  runnerUp: string | null;
  series: SeasonSeries[];
};

export async function getSeasonEvents(seasonId: number): Promise<SeasonEvent[]> {
  const evs = await rows(sql`
    SELECT e.id, e.name, e.start_date, e.end_date, coalesce(e.is_lan, false) AS lan,
           e.prize_pool,
           (SELECT t.name FROM event_placements ep JOIN teams t ON t.id = ep.team_id
             WHERE ep.event_id = e.id AND ep.placement_min = 1 LIMIT 1) AS winner,
           (SELECT t.name FROM event_placements ep JOIN teams t ON t.id = ep.team_id
             WHERE ep.event_id = e.id AND ep.placement_min = 2 LIMIT 1) AS runner_up
    FROM events e
    WHERE e.season_id = ${seasonId} AND e.tier IN ('1', '2')
      AND e.name !~* '(qualif|regular season)'
    ORDER BY e.start_date`);
  const ser = await rows(sql`
    SELECT s.id, s.event_id, s.played_at, t1.name AS team1, t2.name AS team2,
           s.team1_score, s.team2_score, s.round_label
    FROM series s
    JOIN events e ON e.id = s.event_id
    JOIN teams t1 ON t1.id = s.team1_id
    JOIN teams t2 ON t2.id = s.team2_id
    WHERE e.season_id = ${seasonId} AND s.played_at IS NOT NULL
    ORDER BY s.played_at DESC, s.id DESC`);
  const byEvent = new Map<number, SeasonSeries[]>();
  for (const r of ser) {
    const list = byEvent.get(Number(r.event_id)) ?? [];
    list.push({
      id: Number(r.id),
      t: new Date(String(r.played_at)).getTime(),
      team1: String(r.team1),
      team2: String(r.team2),
      s1: Number(r.team1_score),
      s2: Number(r.team2_score),
      round: r.round_label ? String(r.round_label) : null,
    });
    byEvent.set(Number(r.event_id), list);
  }
  return evs.map((r) => ({
    id: Number(r.id),
    name: String(r.name).replace(/^CDL /, ""),
    start: String(r.start_date),
    end: String(r.end_date),
    lan: Boolean(r.lan),
    prize: r.prize_pool === null ? null : Number(r.prize_pool),
    winner: r.winner ? String(r.winner) : null,
    runnerUp: r.runner_up ? String(r.runner_up) : null,
    series: byEvent.get(Number(r.id)) ?? [],
  }));
}

export async function getRecentSeries(seasonId: number, limit = 8): Promise<(SeasonSeries & { event: string })[]> {
  const r = await rows(sql`
    SELECT s.id, s.played_at, t1.name AS team1, t2.name AS team2,
           s.team1_score, s.team2_score, s.round_label, e.name AS event
    FROM series s
    JOIN events e ON e.id = s.event_id
    JOIN teams t1 ON t1.id = s.team1_id
    JOIN teams t2 ON t2.id = s.team2_id
    WHERE e.season_id = ${seasonId} AND s.played_at IS NOT NULL
    ORDER BY s.played_at DESC, s.id DESC
    LIMIT ${limit}`);
  return r.map((x) => ({
    id: Number(x.id),
    t: new Date(String(x.played_at)).getTime(),
    team1: String(x.team1),
    team2: String(x.team2),
    s1: Number(x.team1_score),
    s2: Number(x.team2_score),
    round: x.round_label ? String(x.round_label) : null,
    event: String(x.event).replace(/^CDL /, ""),
  }));
}

export type RaceTeam = {
  teamId: number;
  team: string;
  slug: string;
  start: number;
  end: number;
  wins: number;
  losses: number;
  points: { t: number; r: number }[];
};

export async function getSeasonRace(eloRunId: number, seasonId: number): Promise<RaceTeam[]> {
  const r = await rows(sql`
    SELECT tr.team_id, t.name, s.played_at, tr.rating_pre, tr.rating_post,
           (CASE WHEN (s.team1_id = tr.team_id AND s.team1_score > s.team2_score)
                   OR (s.team2_id = tr.team_id AND s.team2_score > s.team1_score)
                 THEN 1 ELSE 0 END) AS won
    FROM team_ratings tr
    JOIN series s ON s.id = tr.series_id
    JOIN events e ON e.id = s.event_id
    JOIN teams t ON t.id = tr.team_id
    WHERE tr.run_id = ${eloRunId} AND e.season_id = ${seasonId} AND s.played_at IS NOT NULL
    ORDER BY s.played_at, s.id`);
  const by = new Map<number, RaceTeam>();
  for (const x of r) {
    const id = Number(x.team_id);
    let tm = by.get(id);
    const t = new Date(String(x.played_at)).getTime();
    if (!tm) {
      tm = {
        teamId: id,
        team: String(x.name),
        slug: teamSlug(String(x.name)),
        start: Number(x.rating_pre),
        end: 0,
        wins: 0,
        losses: 0,
        points: [{ t: t - 86400_000, r: Number(x.rating_pre) }],
      };
      by.set(id, tm);
    }
    tm.points.push({ t, r: Math.round(Number(x.rating_post)) });
    tm.end = Number(x.rating_post);
    if (Number(x.won)) tm.wins++;
    else tm.losses++;
  }
  return [...by.values()]
    .filter((tm) => tm.points.length >= 15)
    .sort((a, b) => b.end - a.end);
}

export type SeasonLeader = {
  handle: string;
  rating: number | null;
  slug: string;
  team: string;
  maps: number;
  kd: number;
  dmg: number;
  hpKd: number | null;
  hill: number | null;
  sndFb: number | null;
  sndKd: number | null;
};

export async function getSeasonLeaders(seasonId: number, minMaps = 60): Promise<SeasonLeader[]> {
  const r = await rows(sql`
    WITH g AS (
      SELECT g.player_id, g.team_id, g.kills, g.deaths, g.damage, g.hill_time,
             g.first_bloods, gm.mode_id
      FROM game_player_stats g
      JOIN games gm ON gm.id = g.game_id
      JOIN series s ON s.id = gm.series_id
      JOIN events e ON e.id = s.event_id
      WHERE e.season_id = ${seasonId}
    ), team AS (
      SELECT DISTINCT ON (player_id) player_id, team_id
      FROM (SELECT player_id, team_id, count(*) n FROM g GROUP BY 1, 2) x
      ORDER BY player_id, n DESC
    )
    SELECT p.handle, t.name AS team, count(*) AS maps,
           sum(g.kills)::float / nullif(sum(g.deaths), 0) AS kd,
           avg(g.damage) AS dmg,
           sum(g.kills) FILTER (WHERE mode_id = 1)::float
             / nullif(sum(g.deaths) FILTER (WHERE mode_id = 1), 0) AS hp_kd,
           avg(g.hill_time) FILTER (WHERE mode_id = 1) AS hill,
           avg(g.first_bloods) FILTER (WHERE mode_id = 2) AS snd_fb,
           sum(g.kills) FILTER (WHERE mode_id = 2)::float
             / nullif(sum(g.deaths) FILTER (WHERE mode_id = 2), 0) AS snd_kd
    FROM g
    JOIN team tm ON tm.player_id = g.player_id
    JOIN players p ON p.id = g.player_id
    JOIN teams t ON t.id = tm.team_id
    GROUP BY p.handle, t.name
    HAVING count(*) >= ${minMaps}`);
  const n = (v: unknown) => (v === null ? null : Number(v));
  return r.map((x) => ({
    handle: String(x.handle),
    rating: null,
    slug: playerSlug(String(x.handle)),
    team: String(x.team),
    maps: Number(x.maps),
    kd: Number(x.kd),
    dmg: Number(x.dmg),
    hpKd: n(x.hp_kd),
    hill: n(x.hill),
    sndFb: n(x.snd_fb),
    sndKd: n(x.snd_kd),
  }));
}

export type ChampionYear = {
  year: number;
  title: string;
  league: string;
  team: string | null;
};

export async function getChampionsByYear(): Promise<ChampionYear[]> {
  const r = await rows(sql`
    SELECT se.year, t.short_name, se.league,
      (SELECT tm.name FROM event_placements ep
         JOIN events e ON e.id = ep.event_id
         JOIN teams tm ON tm.id = ep.team_id
        WHERE e.season_id = se.id AND ep.placement_min = 1
          AND e.tier IN ('1', '2')
          AND coalesce(e.tier_type, '') NOT IN ('Qualifier', 'Showmatch')
          AND e.name !~* '(qualif|relegation|play-in|regional final|regular season)'
          AND e.name ~* '(call of duty|world league|cwl|cdl) championship'
        LIMIT 1) AS team
    FROM seasons se JOIN titles t ON t.id = se.title_id
    ORDER BY se.year`);
  return r.map((x) => ({
    year: Number(x.year),
    title: String(x.short_name),
    league: String(x.league),
    team: x.team ? String(x.team) : null,
  }));
}

export type SearchEntry = { label: string; href: string; kind: "player" | "team"; sub: string };

export async function getSearchIndex(): Promise<SearchEntry[]> {
  const ps = await rows(sql`
    SELECT p.handle, min(se.year) AS y0, max(se.year) AS y1
    FROM player_season_adjusted a
    JOIN players p ON p.id = a.player_id
    JOIN seasons se ON se.id = a.season_id
    WHERE a.run_id = (SELECT max(id) FROM model_runs WHERE model = 'era_adjust')
      AND a.mode_id IS NULL
    GROUP BY p.handle`);
  const ts = await rows(sql`
    SELECT t.name, count(*) AS n
    FROM teams t JOIN series s ON t.id IN (s.team1_id, s.team2_id)
    GROUP BY t.name HAVING count(*) >= 10`);
  return [
    ...ps.map((x) => ({
      label: String(x.handle),
      href: `/players/${playerSlug(String(x.handle))}`,
      kind: "player" as const,
      sub: x.y0 === x.y1 ? String(x.y0) : `${x.y0}–${x.y1}`,
    })),
    ...ts.map((x) => ({
      label: String(x.name),
      href: `/teams/${teamSlug(String(x.name))}`,
      kind: "team" as const,
      sub: `${x.n} series`,
    })),
  ];
}

export type TeamModeRow = {
  team: string;
  slug: string;
  prize: number;
  modes: Record<string, { maps: number; wins: number }>;
};

export async function getTeamModes(seasonId: number): Promise<{ modes: string[]; rows: TeamModeRow[] }> {
  const r = await rows(sql`
    SELECT t.name AS team, gm.name AS mode, count(*) AS maps,
           count(*) FILTER (WHERE g.winner_team_id = t.id) AS wins
    FROM games g
    JOIN series s ON s.id = g.series_id
    JOIN events e ON e.id = s.event_id
    JOIN game_modes gm ON gm.id = g.mode_id
    JOIN teams t ON t.id IN (s.team1_id, s.team2_id)
    WHERE e.season_id = ${seasonId}
    GROUP BY t.name, gm.name`);
  const prize = await rows(sql`
    SELECT t.name AS team, sum(ep.prize) AS prize
    FROM event_placements ep
    JOIN events e ON e.id = ep.event_id
    JOIN teams t ON t.id = ep.team_id
    WHERE e.season_id = ${seasonId}
    GROUP BY t.name`);
  const prizeBy = new Map(prize.map((x) => [String(x.team), Number(x.prize ?? 0)]));
  const modeMaps = new Map<string, number>();
  const by = new Map<string, TeamModeRow>();
  for (const x of r) {
    const team = String(x.team);
    const mode = String(x.mode);
    modeMaps.set(mode, (modeMaps.get(mode) ?? 0) + Number(x.maps));
    const row = by.get(team) ?? { team, slug: teamSlug(team), prize: prizeBy.get(team) ?? 0, modes: {} };
    row.modes[mode] = { maps: Number(x.maps), wins: Number(x.wins) };
    by.set(team, row);
  }
  const modes = [...modeMaps.entries()].sort((a, b) => b[1] - a[1]).map(([m]) => m);
  const total = (row: TeamModeRow) => Object.values(row.modes).reduce((a, m) => a + m.maps, 0);
  return {
    modes,
    rows: [...by.values()].filter((row) => total(row) >= 40),
  };
}

export async function getSeasonRatings(ratingRunId: number, seasonId: number): Promise<Map<string, number>> {
  const r = await rows(sql`
    SELECT p.handle, pr.rating
    FROM player_season_adjusted pr JOIN players p ON p.id = pr.player_id
    WHERE pr.run_id = ${ratingRunId} AND pr.season_id = ${seasonId}
      AND pr.mode_id IS NULL AND pr.rating IS NOT NULL`);
  return new Map(r.map((x) => [String(x.handle), Number(x.rating)]));
}

export type SiteCounts = { rounds: number; teams: number; events: number; findings: number; weapons: number };

export async function getSiteCounts(insightsRunId: number | null): Promise<SiteCounts> {
  const [r] = await rows(sql`
    SELECT (SELECT count(*) FROM game_rounds) AS rounds,
           (SELECT count(*) FROM teams t
             WHERE EXISTS (SELECT 1 FROM series s WHERE t.id IN (s.team1_id, s.team2_id))) AS teams,
           (SELECT count(*) FROM events) AS events,
           (SELECT count(*) FROM insights WHERE run_id = ${insightsRunId ?? -1} AND NOT retracted) AS findings,
           (SELECT count(DISTINCT fave_weapon) FROM game_player_stats) AS weapons`);
  return {
    rounds: Number(r.rounds),
    teams: Number(r.teams),
    events: Number(r.events),
    findings: Number(r.findings),
    weapons: Number(r.weapons),
  };
}
