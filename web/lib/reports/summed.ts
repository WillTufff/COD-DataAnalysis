// A metric's published arithmetic, evaluated over any set of map totals. The
// metric layer publishes each summable metric as data (`agg` in the catalog):
// weighted sums over map rows and at most one division. This file evaluates
// that data exactly as the metric layer does, and scores the result the way it
// scores a season, so a re-aggregated cell and a published one mean the same.

/** `[key, weight]`. A key starting with @ names a total that is not a column. */
export type AggTerm = [string, number];

export type AggSpec = {
  num: AggTerm[];
  den: AggTerm[] | null;
  denom: AggTerm[];
  per: number;
  den_floor: number;
  complement: boolean;
};

/** How the metric layer reads one key off a game_player_stats row. */
export type MapKeySource = {
  column: string | null;
  extra: string | null;
  fallback: string[];
};

export type Totals = (key: string) => number;

function weighted(totals: Totals, terms: AggTerm[]): number {
  let out = 0;
  for (const [key, weight] of terms) out += totals(key) * weight;
  return out;
}

/** Every key a spec reads. */
export function specKeys(spec: AggSpec): string[] {
  return [
    ...new Set(
      [...spec.num, ...(spec.den ?? []), ...spec.denom].map(([key]) => key),
    ),
  ];
}

/**
 * value = num / (den / per), or num alone with no den; the sample size is the
 * denom sum. Null where the metric layer publishes nothing: a divisor or sample
 * of zero, or a value that is not finite.
 */
export function evalSpec(
  spec: AggSpec,
  totals: Totals,
): { value: number; denom: number } | null {
  let divisor = 1;
  if (spec.den !== null) {
    divisor = weighted(totals, spec.den) / spec.per;
    if (spec.den_floor) divisor = Math.max(divisor, spec.den_floor);
    if (divisor <= 0) return null;
  }
  const denom = weighted(totals, spec.denom);
  if (denom <= 0) return null;
  const numerator = weighted(totals, spec.num);
  let value = spec.den !== null ? numerator / divisor : numerator;
  if (spec.complement) value = 1 - value;
  if (!Number.isFinite(value)) return null;
  return { value, denom };
}

/** Below this many qualified peers there is no spread to score against. */
const MIN_COHORT = 2;
/** Below this many, the percentile publishes and the z-score does not. */
const MIN_Z_COHORT = 15;

/**
 * z-score and percentile for every id in `values`, against the distribution of
 * `cohort` only: sample SD, and the share of the cohort at or below the value.
 * Empty when the cohort is too small or has no spread.
 */
export function zAndPctl<K>(
  values: Map<K, number>,
  cohort: K[],
): Map<K, { z: number | null; pctl: number }> {
  const out = new Map<K, { z: number | null; pctl: number }>();
  const field = cohort
    .map((id) => values.get(id))
    .filter((v): v is number => v !== undefined && Number.isFinite(v));
  if (field.length < MIN_COHORT) return out;
  const mean = field.reduce((a, b) => a + b, 0) / field.length;
  const sd = Math.sqrt(
    field.reduce((a, v) => a + (v - mean) ** 2, 0) / (field.length - 1),
  );
  if (sd === 0 || !Number.isFinite(sd)) return out;
  const sorted = [...field].sort((a, b) => a - b);
  const publishZ = field.length >= MIN_Z_COHORT;
  for (const [id, v] of values) {
    if (!Number.isFinite(v)) continue;
    out.set(id, {
      z: publishZ ? (v - mean) / sd : null,
      pctl: Math.min(Math.max(atOrBelow(sorted, v) / sorted.length, 0), 1),
    });
  }
  return out;
}

/** How many of `sorted` are ≤ v (numpy's searchsorted, side="right"). */
function atOrBelow(sorted: number[], v: number): number {
  let lo = 0;
  let hi = sorted.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (sorted[mid] <= v) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** The catalog's "every mode" marker in a metric's `modes`. */
export const ALL_MODES = "__all__";

/**
 * Whether a metric is defined over maps from these modes. A mode-specific
 * metric is defined only when every picked mode is one of its own, and an
 * empty pick (every mode) admits only the metrics that span all of them, as the
 * published all-modes rows do.
 */
export function coversModes(modes: string[], picked: string[]): boolean {
  if (modes.includes(ALL_MODES)) return true;
  return picked.length > 0 && picked.every((m) => modes.includes(m));
}

/** One entity's map totals in one season: the grain the database returns. */
export type SeasonTotals = {
  id: number;
  year: number;
  title: string;
  totals: Record<string, number>;
};

/** A column to re-aggregate: its arithmetic, where it applies, its floor. */
export type SummedColumn = {
  key: string;
  spec: AggSpec;
  /** Titles that track every source the metric reads; null = no title gate. */
  titles: string[] | null;
  minDenom: number;
};

export type SummedCell = {
  value: number;
  denom: number;
  z: number | null;
  pctl: number | null;
  qualified: boolean;
};

export type SummedRow = {
  id: number;
  years: number[];
  titles: string[];
  maps: number;
  cells: Record<string, SummedCell>;
};

/**
 * Fold season totals into rows (one per entity and season, or one per entity
 * over every season with `span`), evaluate each column over the seasons whose
 * title tracks it, then score each column within its field: per season, or the
 * whole span. Qualified is denom ≥ the column's floor, as published.
 */
export function summedRows(
  seasons: SeasonTotals[],
  columns: SummedColumn[],
  span: boolean,
): SummedRow[] {
  const groups = new Map<string, SeasonTotals[]>();
  for (const s of seasons) {
    const id = span ? `${s.id}` : `${s.id}-${s.year}`;
    const group = groups.get(id);
    if (group) group.push(s);
    else groups.set(id, [s]);
  }

  const rows: SummedRow[] = [];
  for (const group of groups.values()) {
    const row: SummedRow = {
      id: group[0].id,
      years: [...new Set(group.map((s) => s.year))].sort((a, b) => a - b),
      titles: [...new Set(group.map((s) => s.title))],
      maps: group.reduce((n, s) => n + (s.totals["@maps"] ?? 0), 0),
      cells: {},
    };
    for (const col of columns) {
      const covered = group.filter(
        (s) => col.titles === null || col.titles.includes(s.title),
      );
      if (covered.length === 0) continue;
      const totals: Totals = (key) =>
        covered.reduce((n, s) => n + (s.totals[key] ?? 0), 0);
      const result = evalSpec(col.spec, totals);
      if (result === null) continue;
      row.cells[col.key] = {
        ...result,
        z: null,
        pctl: null,
        qualified: result.denom >= col.minDenom,
      };
    }
    rows.push(row);
  }

  const fields = new Map<string, SummedRow[]>();
  for (const row of rows) {
    const field = span ? "span" : String(row.years[0]);
    const members = fields.get(field);
    if (members) members.push(row);
    else fields.set(field, [row]);
  }
  for (const members of fields.values()) {
    for (const col of columns) {
      const values = new Map<SummedRow, number>();
      const cohort: SummedRow[] = [];
      for (const row of members) {
        const cell = row.cells[col.key];
        if (!cell) continue;
        values.set(row, cell.value);
        if (cell.qualified) cohort.push(row);
      }
      for (const [row, scored] of zAndPctl(values, cohort)) {
        row.cells[col.key].z = scored.z;
        row.cells[col.key].pctl = scored.pctl;
      }
    }
  }
  return rows;
}
