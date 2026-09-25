// The second way a stats number is computed: re-aggregated from map rows at
// request time, for a set of maps the published season rows do not cover (a
// mix of modes, several seasons as one span). Only metrics the catalog
// publishes arithmetic for (`agg`) qualify; everything else shows as absent
// with the reason on its column. `summed.ts` holds the arithmetic and scoring;
// this file fetches the totals it runs on.

import { type SQL, sql } from "drizzle-orm";
import { db } from "@/lib/db";
import {
  type MetricCatalogEntry,
  type ReportColumn,
  type ReportRow,
  type ScopeViewPlayer,
  reportColumn,
  teamMemberSeasons,
} from "@/lib/analytics";
import { playerSlug, teamSlug } from "@/lib/slug";
import { type ContentFilters, NO_CONTENT } from "./content";
import {
  type MapKeySource,
  type SeasonTotals,
  type SummedColumn,
  coversModes,
  specKeys,
  summedRows,
} from "./summed";

export type AggregateQuery = {
  metrics: string[];
  years: number[]; // empty = every season
  modes: string[]; // empty = every mode
  span: boolean;
  players?: string[];
  teams?: string[];
  /** Tier, map and date filters on the maps summed; absent = none. */
  content?: ContentFilters;
};

/** Why a column cannot be re-aggregated for this pick, or null when it can. */
export function unavailableReason(
  m: MetricCatalogEntry,
  modes: string[],
  modeName: (slug: string) => string,
): string | null {
  if (!m.agg) {
    return "Published per season and mode only: it is not a sum over maps, so it cannot be recombined.";
  }
  if (!coversModes(m.modes, modes)) {
    const own = m.modes.map(modeName).join(", ");
    return `${own} only, so it has no value over a mix of modes.`;
  }
  return null;
}

const IDENT = /^[a-z][a-z0-9_]*$/;

function ident(name: string): SQL {
  if (!IDENT.test(name)) throw new Error(`bad column name: ${name}`);
  return sql.raw(`gps.${name}`);
}

/** A key's per-map value: typed column, then extras number, then fallbacks. */
function keyExpr(
  key: string,
  sources: Record<string, MapKeySource>,
  depth = 0,
): SQL {
  const src = sources[key];
  if (!src || depth > 2) return sql`NULL::float8`;
  const parts: SQL[] = [];
  if (src.column) parts.push(sql`${ident(src.column)}::float8`);
  if (src.extra) {
    parts.push(
      sql`CASE WHEN jsonb_typeof(gps.extras -> ${src.extra}::text) = 'number' THEN (gps.extras ->> ${src.extra}::text)::float8 END`,
    );
  }
  if (src.fallback.length > 0) {
    const fb = src.fallback.map((k) => keyExpr(k, sources, depth + 1));
    const anyPresent = sql.join(
      fb.map((e) => sql`(${e}) IS NOT NULL`),
      sql` OR `,
    );
    const total = sql.join(
      fb.map((e) => sql`COALESCE(${e}, 0)`),
      sql` + `,
    );
    parts.push(sql`CASE WHEN ${anyPresent} THEN ${total} END`);
  }
  if (parts.length === 0) return sql`NULL::float8`;
  return parts.length === 1 ? parts[0] : sql`COALESCE(${sql.join(parts, sql`, `)})`;
}

/** A map name's slug in SQL, as `mapSlug` builds it in the page. */
const MAP_SLUG = sql`regexp_replace(regexp_replace(lower(mp.name), '[^a-z0-9]+', '-', 'g'), '^-+|-+$', '', 'g')`;

/** The series date as a UTC day, which is what `from` and `to` name. */
const SERIES_DAY = sql`(s.played_at AT TIME ZONE 'UTC')::date`;

/**
 * The map filter every aggregate shares: seasons, modes, and the content
 * filters. Each is one predicate over MAP_JOINS, so a filter narrows the maps
 * and leaves the arithmetic alone.
 */
function mapFilter(q: Pick<AggregateQuery, "years" | "modes" | "content">): SQL {
  const conditions: SQL[] = [sql`TRUE`];
  if (q.years.length > 0) {
    conditions.push(sql`se.year IN (${sql.join(q.years.map((y) => sql`${y}`), sql`, `)})`);
  }
  if (q.modes.length > 0) {
    conditions.push(sql`gm.slug IN (${sql.join(q.modes.map((m) => sql`${m}`), sql`, `)})`);
  }
  const c = q.content ?? NO_CONTENT;
  if (c.tier) conditions.push(sql`ev.tier = ${c.tier}`);
  if (c.maps.length > 0) {
    conditions.push(sql`${MAP_SLUG} IN (${sql.join(c.maps.map((m) => sql`${m}`), sql`, `)})`);
  }
  if (c.from) conditions.push(sql`${SERIES_DAY} >= ${c.from}::date`);
  if (c.to) conditions.push(sql`${SERIES_DAY} <= ${c.to}::date`);
  return sql.join(conditions, sql` AND `);
}

const MAP_JOINS = sql`
  FROM game_player_stats gps
  JOIN games g       ON g.id = gps.game_id
  JOIN series s      ON s.id = g.series_id
  JOIN events ev     ON ev.id = s.event_id
  JOIN seasons se    ON se.id = ev.season_id
  JOIN titles t      ON t.id = se.title_id
  JOIN game_modes gm ON gm.id = g.mode_id
  LEFT JOIN maps mp  ON mp.id = g.map_id
`;

/** Alias for a summed key in the result set. */
const alias = (i: number) => sql.raw(`k${i}`);

/**
 * Each player's totals per season over the picked maps, for exactly the keys
 * the columns read. The per-map team totals are only joined when a share
 * metric asks for them.
 */
async function playerSeasonTotals(
  q: AggregateQuery,
  keys: string[],
  sources: Record<string, MapKeySource>,
): Promise<SeasonTotals[]> {
  const kills = keyExpr("kills", sources);
  const dist = keyExpr("avg_kill_dist_m", sources);
  const perMap: Record<string, SQL> = {
    "@maps": sql`1`,
    "@duration_s": sql`COALESCE(g.duration_s, 0)::float8`,
    "@damage_duration_s": sql`CASE WHEN gps.damage IS NOT NULL THEN COALESCE(g.duration_s, 0)::float8 END`,
    "@damage_maps": sql`CASE WHEN gps.damage IS NOT NULL THEN 1 END`,
    "@team_kills": sql`tt.kills`,
    "@team_hill_time": sql`tt.hill_time`,
    "@kill_dist_weighted": sql`CASE WHEN (${dist}) IS NOT NULL AND COALESCE(${kills}, 0) > 0 THEN (${dist}) * (${kills}) END`,
    "@kill_dist_kills": sql`CASE WHEN (${dist}) IS NOT NULL AND COALESCE(${kills}, 0) > 0 THEN (${kills}) END`,
  };
  const exprs = keys.map((k) => perMap[k] ?? keyExpr(k, sources));
  // The team's totals on each map, joined in narrow rather than windowed over
  // the wide per-player rows, which costs several times more.
  const teamJoin = keys.some((k) => k === "@team_kills" || k === "@team_hill_time")
    ? sql`JOIN (
        SELECT game_id, team_id,
               sum(COALESCE(kills, 0))::float8 AS kills,
               sum(COALESCE(hill_time, 0))::float8 AS hill_time
        FROM game_player_stats GROUP BY game_id, team_id
      ) tt ON tt.game_id = gps.game_id AND tt.team_id = gps.team_id`
    : sql``;
  const rows = (await db.execute(sql`
    WITH m AS (
      SELECT gps.player_id AS id, se.year, t.short_name AS title,
             ${sql.join(exprs.map((e, i) => sql`${e} AS ${alias(i)}`), sql`, `)}
      ${MAP_JOINS}
      ${teamJoin}
      WHERE ${mapFilter(q)}
    )
    SELECT id, year, title,
           ${sql.join(keys.map((_, i) => sql`sum(${alias(i)})::float8 AS ${alias(i)}`), sql`, `)}
    FROM m GROUP BY id, year, title
  `)) as unknown as Record<string, unknown>[];
  return rows.map((r) => seasonTotals(r, keys));
}

/**
 * Each team's totals per season over the picked maps. One row per team per
 * map first, then the TEAM_MAP_KEYS quantities from it, as the metric layer
 * builds a team's map.
 */
async function teamSeasonTotals(
  q: AggregateQuery,
  keys: string[],
): Promise<SeasonTotals[]> {
  const perMap: Record<string, SQL> = {
    "@maps": sql`1`,
    won: sql`CASE WHEN winner_team_id IS NULL THEN NULL WHEN winner_team_id = team_id THEN 1 ELSE 0 END`,
    decided: sql`CASE WHEN winner_team_id IS NOT NULL THEN 1 END`,
    kill_diff: sql`CASE WHEN sides = 2 THEN kills - opp_kills END`,
    kill_diff_maps: sql`CASE WHEN sides = 2 THEN 1 END`,
    margin: sql`score - opp_score`,
    margin_maps: sql`CASE WHEN score IS NOT NULL AND opp_score IS NOT NULL THEN 1 END`,
    rounds_won: sql`score`,
    rounds_played: sql`score + opp_score`,
  };
  const exprs = keys.map((k) => perMap[k] ?? sql`NULL::float8`);
  const rows = (await db.execute(sql`
    WITH tm AS (
      SELECT gps.game_id, gps.team_id, se.year, t.short_name AS title,
             sum(COALESCE(gps.kills, 0))::float8 AS kills,
             g.winner_team_id,
             CASE WHEN gps.team_id = s.team1_id THEN g.team1_score
                  WHEN gps.team_id = s.team2_id THEN g.team2_score END::float8 AS score,
             CASE WHEN gps.team_id = s.team1_id THEN g.team2_score
                  WHEN gps.team_id = s.team2_id THEN g.team1_score END::float8 AS opp_score
      ${MAP_JOINS}
      WHERE ${mapFilter(q)}
      GROUP BY gps.game_id, gps.team_id, se.year, t.short_name, g.winner_team_id,
               g.team1_score, g.team2_score, s.team1_id, s.team2_id
    ), paired AS (
      SELECT tm.*,
             count(*) OVER (PARTITION BY game_id) AS sides,
             sum(kills) OVER (PARTITION BY game_id) - kills AS opp_kills
      FROM tm
    ), m AS (
      SELECT team_id AS id, year, title,
             ${sql.join(exprs.map((e, i) => sql`(${e})::float8 AS ${alias(i)}`), sql`, `)}
      FROM paired
    )
    SELECT id, year, title,
           ${sql.join(keys.map((_, i) => sql`sum(${alias(i)})::float8 AS ${alias(i)}`), sql`, `)}
    FROM m GROUP BY id, year, title
  `)) as unknown as Record<string, unknown>[];
  return rows.map((r) => seasonTotals(r, keys));
}

function seasonTotals(r: Record<string, unknown>, keys: string[]): SeasonTotals {
  const totals: Record<string, number> = {};
  keys.forEach((k, i) => {
    const v = r[`k${i}`];
    totals[k] = v === null || v === undefined ? 0 : Number(v);
  });
  return {
    id: Number(r.id),
    year: Number(r.year),
    title: String(r.title),
    totals,
  };
}

/** The columns a query can re-aggregate, with every key they read. */
function summedColumns(
  entries: MetricCatalogEntry[],
  modes: string[],
  entity: "players" | "teams",
): { columns: SummedColumn[]; keys: string[] } {
  const columns: SummedColumn[] = [];
  for (const m of entries) {
    if (!m.agg || !coversModes(m.modes, modes)) continue;
    columns.push({
      key: m.key,
      spec: m.agg,
      titles: entity === "teams" ? null : m.titles,
      minDenom: m.min_denom,
    });
  }
  const keys = [
    ...new Set(["@maps", ...columns.flatMap((c) => specKeys(c.spec))]),
  ];
  return { columns, keys };
}

/** "2020–2026", or the one year. */
function spanLabel(years: number[]): string {
  const lo = years[0];
  const hi = years[years.length - 1];
  return lo === hi ? String(lo) : `${lo}–${hi}`;
}

/**
 * `queryReport` for a pick of maps the published rows do not cover. Rows
 * carry the same shape; cells for columns that cannot be re-aggregated are
 * absent, and each such column says why in `unavailable`.
 */
export async function queryAggregateReport(
  q: AggregateQuery,
  entity: "players" | "teams",
  entries: MetricCatalogEntry[],
  sources: Record<string, MapKeySource>,
  modeName: (slug: string) => string,
): Promise<{ columns: ReportColumn[]; rows: ReportRow[] }> {
  const byKey = new Map(entries.map((m) => [m.key, m]));
  const ordered = q.metrics
    .map((k) => byKey.get(k))
    .filter((m): m is MetricCatalogEntry => m !== undefined);
  const columns: ReportColumn[] = ordered.map((m) => ({
    ...reportColumn(m),
    unavailable: unavailableReason(m, q.modes, modeName),
  }));
  const { columns: summed, keys } = summedColumns(ordered, q.modes, entity);

  const seasons =
    entity === "teams"
      ? await teamSeasonTotals(q, keys)
      : await playerSeasonTotals(q, keys, sources);
  const aggregated = summedRows(seasons, summed, q.span);
  const names = await entityNames(entity, [...new Set(aggregated.map((r) => r.id))]);

  // A column the filtered maps never feed says so, rather than sitting empty
  // beside columns that have numbers.
  if (q.content) {
    const fed = new Set(aggregated.flatMap((r) => Object.keys(r.cells)));
    for (const c of columns) {
      if (!c.unavailable && !fed.has(c.key)) {
        c.unavailable =
          "None of the maps these filters keep come from a title that tracks it.";
      }
    }
  }

  let rows: ReportRow[] = aggregated.map((r) => {
    const name = names.get(r.id) ?? String(r.id);
    const year = r.years[r.years.length - 1];
    return {
      playerId: r.id,
      handle: name,
      slug: entity === "teams" ? teamSlug(name) : playerSlug(name),
      year,
      title: r.titles.join(" + "),
      mode: null,
      maps: r.maps,
      cells: r.cells,
      ...(q.span ? { years: r.years, seasonLabel: spanLabel(r.years) } : {}),
    };
  });

  if (entity === "teams") {
    const picked = q.teams && q.teams.length > 0 ? new Set(q.teams) : null;
    if (picked) rows = rows.filter((row) => picked.has(row.slug));
    return { columns, rows };
  }
  const picked = q.players && q.players.length > 0 ? new Set(q.players) : null;
  if (picked) rows = rows.filter((row) => picked.has(row.slug));
  if (q.teams && q.teams.length > 0) {
    // A span row keeps a player who played for the team in any of its seasons.
    const members = await teamMemberSeasons(q.teams);
    rows = rows.filter((row) =>
      (row.years ?? [row.year]).some((y) => members.has(`${row.playerId}-${y}`)),
    );
  }
  return { columns, rows };
}

async function entityNames(
  entity: "players" | "teams",
  ids: number[],
): Promise<Map<number, string>> {
  if (ids.length === 0) return new Map();
  const list = sql.join(ids.map((id) => sql`${id}`), sql`, `);
  const rows = (await db.execute(
    entity === "teams"
      ? sql`SELECT id, name FROM teams WHERE id IN (${list})`
      : sql`SELECT id, handle AS name FROM players WHERE id IN (${list})`,
  )) as unknown as { id: number; name: string }[];
  return new Map(rows.map((r) => [Number(r.id), r.name]));
}

/** The view a lookup below describes: the maps the report sums over. */
type MapView = Pick<AggregateQuery, "years" | "modes" | "content">;

/** The seasons that have maps under these filters, with how many. */
export async function contentSeasons(q: MapView): Promise<Map<number, number>> {
  const rows = (await db.execute(sql`
    SELECT se.year, count(DISTINCT g.id)::int AS maps
    ${MAP_JOINS}
    WHERE ${mapFilter(q)}
    GROUP BY se.year
  `)) as unknown as { year: number; maps: number }[];
  return new Map(rows.map((r) => [Number(r.year), Number(r.maps)]));
}

export type MapOption = {
  slug: string;
  name: string;
  /** Title codes the name was played in. */
  titles: string[];
  maps: number;
};

/**
 * Every map name with maps in view, busiest first. The map pick itself is left
 * out of the filter, so the menu can offer a second map beside the first.
 */
export async function contentMapOptions(q: MapView): Promise<MapOption[]> {
  const rows = (await db.execute(sql`
    SELECT mp.name, ${MAP_SLUG} AS slug,
           array_agg(DISTINCT t.short_name) AS titles,
           count(DISTINCT g.id)::int AS maps
    ${MAP_JOINS}
    WHERE mp.name IS NOT NULL
      AND ${mapFilter({ ...q, content: { ...(q.content ?? NO_CONTENT), maps: [] } })}
    GROUP BY mp.name
    ORDER BY maps DESC, mp.name
  `)) as unknown as {
    name: string;
    slug: string;
    titles: string[];
    maps: number;
  }[];
  // Two names folding to one slug share an entry, as the URL knows them.
  const bySlug = new Map<string, MapOption>();
  for (const r of rows) {
    // A name with no letters or digits ("?") has no slug to pick it by.
    if (!r.slug) continue;
    const have = bySlug.get(r.slug);
    if (have) {
      have.maps += Number(r.maps);
      have.titles = [...new Set([...have.titles, ...r.titles])];
    } else {
      bySlug.set(r.slug, {
        slug: r.slug,
        name: r.name,
        titles: [...r.titles],
        maps: Number(r.maps),
      });
    }
  }
  return [...bySlug.values()];
}

/**
 * The players with maps under these filters, with the teams they played those
 * maps for: `getReportViewPlayers` for a view the published rows do not hold.
 */
export async function contentViewPlayers(q: MapView): Promise<ScopeViewPlayer[]> {
  const rows = (await db.execute(sql`
    SELECT p.handle, se.year, tm.name AS team
    ${MAP_JOINS}
    JOIN players p ON p.id = gps.player_id
    JOIN teams tm  ON tm.id = gps.team_id
    WHERE ${mapFilter(q)}
    GROUP BY p.handle, se.year, tm.name
  `)) as unknown as { handle: string; year: number; team: string }[];
  const bySlug = new Map<string, ScopeViewPlayer>();
  for (const r of rows) {
    const slug = playerSlug(r.handle);
    let entry = bySlug.get(slug);
    if (!entry) bySlug.set(slug, (entry = { handle: r.handle, slug, stints: [] }));
    const year = Number(r.year);
    const have = entry.stints.find((st) => st.team === r.team);
    if (have) {
      if (!have.years.includes(year)) have.years.push(year);
    } else {
      entry.stints.push({ team: r.team, years: [year] });
    }
  }
  const latest = (years: number[]) => Math.max(...years);
  for (const e of bySlug.values()) {
    for (const st of e.stints) st.years.sort((a, b) => a - b);
    e.stints.sort(
      (a, b) => latest(b.years) - latest(a.years) || a.team.localeCompare(b.team),
    );
  }
  return [...bySlug.values()].sort((a, b) => a.handle.localeCompare(b.handle));
}
