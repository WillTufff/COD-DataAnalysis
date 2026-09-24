// Pure earnings helpers, free of the database layer so they can be unit tested
// and imported by client components.

export type EarningsYear = { year: number; amount: number };

/**
 * Liquipedia's `earnings_by_year` jsonb as one row per calendar year, oldest
 * first. Years between the first and last with no entry are filled with zero so
 * the chart axis has no holes.
 */
export function parseEarningsByYear(raw: unknown): EarningsYear[] {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return [];
  const byYear = new Map<number, number>();
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    const year = Number(k);
    const amount = Number(v);
    if (!Number.isInteger(year) || !Number.isFinite(amount)) continue;
    byYear.set(year, amount);
  }
  if (byYear.size === 0) return [];
  const years = [...byYear.keys()];
  const first = Math.min(...years);
  const last = Math.max(...years);
  const out: EarningsYear[] = [];
  for (let y = first; y <= last; y++) {
    out.push({ year: y, amount: byYear.get(y) ?? 0 });
  }
  return out;
}

export type PrizePlacement = {
  seasonId: number;
  year: number;
  title: string;
  league: string;
  prize: number | null;
};

export type SeasonPrize = {
  seasonId: number;
  year: number;
  title: string;
  league: string;
  prize: number;
  /** Events in the season this team placed at, paid or not. */
  events: number;
  /** Events in the season that paid this team anything. */
  paidEvents: number;
};

/** Prize money summed per season, newest first. Seasons with no paid event
 *  are kept, at zero, so the table shows where the team played for nothing. */
export function sumPrizeBySeason(rows: PrizePlacement[]): SeasonPrize[] {
  const bySeason = new Map<number, SeasonPrize>();
  for (const r of rows) {
    const s = bySeason.get(r.seasonId) ?? {
      seasonId: r.seasonId,
      year: r.year,
      title: r.title,
      league: r.league,
      prize: 0,
      events: 0,
      paidEvents: 0,
    };
    s.events += 1;
    if (r.prize !== null && r.prize > 0) {
      s.prize += r.prize;
      s.paidEvents += 1;
    }
    bySeason.set(r.seasonId, s);
  }
  return [...bySeason.values()].sort(
    (a, b) => b.year - a.year || a.title.localeCompare(b.title),
  );
}

/** $2.19M, $438K, $950. */
export function formatMoney(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 1_000_000) return `$${(amount / 1_000_000).toFixed(2)}M`;
  if (abs >= 10_000) return `$${Math.round(amount / 1_000)}K`;
  if (abs >= 1_000) return `$${(amount / 1_000).toFixed(1)}K`;
  return `$${Math.round(amount).toLocaleString("en-US")}`;
}

/** $2,186,194 — the exact figure, for tooltips and tables. */
export function formatMoneyExact(amount: number): string {
  return `$${Math.round(amount).toLocaleString("en-US")}`;
}
