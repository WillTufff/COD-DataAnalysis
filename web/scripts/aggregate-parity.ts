// Release gate: the request-time aggregation path must reproduce the published
// season rows. For every season and mode the metric layer scored (and each
// season's all-modes rows), it re-aggregates every metric with published
// arithmetic from map rows and compares each cell's value, denominator, z,
// percentile and qualification, and which cells exist at all. Exit 0 on
// parity, 1 on any difference, 3 when there is no run to check.
import { sql } from "drizzle-orm";
import { db } from "../lib/db";
import {
  type MetricCatalogEntry,
  getMetricCatalog,
  getTeamMetricCatalog,
  latestRun,
} from "../lib/analytics";
import { queryAggregateReport } from "../lib/reports/aggregate";

// Published cells are stored as float4, so agreement is to its precision.
const RTOL = 1e-6;
const ATOL = 1e-9;

function close(a: number | null, b: number | null): boolean {
  if (a === null || b === null) return a === b;
  return Math.abs(a - b) <= ATOL + RTOL * Math.max(Math.abs(a), Math.abs(b));
}

type Published = {
  id: number;
  metric: string;
  value: number;
  denom: number;
  z: number | null;
  pctl: number | null;
  qualified: boolean;
};

async function published(
  table: "player_metric_season" | "team_metric_season",
  runId: number,
  year: number,
  mode: string | null,
  metrics: string[],
): Promise<Published[]> {
  const idCol = sql.raw(table === "team_metric_season" ? "team_id" : "player_id");
  const t = sql.raw(table);
  const modeCond =
    mode === null ? sql`p.mode_id IS NULL` : sql`gm.slug = ${mode}`;
  return (await db.execute(sql`
    SELECT p.${idCol} AS id, p.metric, p.value, p.denom, p.z, p.pctl, p.qualified
    FROM ${t} p
    JOIN seasons se ON se.id = p.season_id
    LEFT JOIN game_modes gm ON gm.id = p.mode_id
    WHERE p.run_id = ${runId} AND se.year = ${year} AND ${modeCond}
      AND p.metric IN (${sql.join(metrics.map((m) => sql`${m}`), sql`, `)})
  `)) as unknown as Published[];
}

async function main() {
  const run = await latestRun("metric_layer");
  const catalog = run ? await getMetricCatalog(run.id) : null;
  if (!run || !catalog) {
    console.log("no metric_layer run to check");
    process.exit(3);
  }
  if (!catalog.map_keys || !catalog.metrics.some((m) => m.agg)) {
    console.log(`run ${run.id} publishes no metric arithmetic; refit the metric layer`);
    process.exit(3);
  }
  const sources = catalog.map_keys;
  const entities: {
    entity: "players" | "teams";
    table: "player_metric_season" | "team_metric_season";
    entries: MetricCatalogEntry[];
  }[] = [
    {
      entity: "players",
      table: "player_metric_season",
      entries: catalog.metrics.filter((m) => m.agg),
    },
    {
      entity: "teams",
      table: "team_metric_season",
      entries: (await getTeamMetricCatalog(run.id)).filter((m) => m.agg),
    },
  ];

  let failures = 0;
  let cells = 0;
  const started = Date.now();
  for (const { entity, table, entries } of entities) {
    const keys = entries.map((m) => m.key);
    const t = sql.raw(table);
    const cohorts = (await db.execute(sql`
      SELECT DISTINCT se.year, gm.slug AS mode
      FROM ${t} p
      JOIN seasons se ON se.id = p.season_id
      LEFT JOIN game_modes gm ON gm.id = p.mode_id
      WHERE p.run_id = ${run.id}
      ORDER BY se.year, gm.slug
    `)) as unknown as { year: number; mode: string | null }[];

    for (const { year, mode } of cohorts) {
      const want = await published(table, run.id, year, mode, keys);
      const { rows } = await queryAggregateReport(
        { metrics: keys, years: [year], modes: mode ? [mode] : [], span: false },
        entity,
        entries,
        sources,
        (s) => s,
      );
      const got = new Map<string, (typeof rows)[number]["cells"][string]>();
      for (const r of rows) {
        for (const [metric, cell] of Object.entries(r.cells)) {
          got.set(`${r.playerId}:${metric}`, cell);
        }
      }
      const label = `${entity} ${year} ${mode ?? "all modes"}`;
      const report = (msg: string) => {
        failures += 1;
        if (failures <= 40) console.log(`FAIL ${label}: ${msg}`);
      };
      for (const p of want) {
        cells += 1;
        const id = `${p.id}:${p.metric}`;
        const c = got.get(id);
        got.delete(id);
        if (!c) {
          report(`${id} published, not re-aggregated`);
          continue;
        }
        if (!close(c.value, p.value)) report(`${id} value ${c.value} ≠ ${p.value}`);
        if (!close(c.denom, p.denom)) report(`${id} denom ${c.denom} ≠ ${p.denom}`);
        if (!close(c.z, p.z)) report(`${id} z ${c.z} ≠ ${p.z}`);
        if (!close(c.pctl, p.pctl)) report(`${id} pctl ${c.pctl} ≠ ${p.pctl}`);
        if (c.qualified !== p.qualified) report(`${id} qualified ${c.qualified} ≠ ${p.qualified}`);
      }
      for (const id of got.keys()) report(`${id} re-aggregated, not published`);
    }
  }

  const secs = ((Date.now() - started) / 1000).toFixed(1);
  if (failures > 0) {
    console.log(`${failures} difference(s) over ${cells} published cells (${secs}s)`);
    process.exit(1);
  }
  console.log(`parity: ${cells} published cells reproduced (${secs}s)`);
  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
