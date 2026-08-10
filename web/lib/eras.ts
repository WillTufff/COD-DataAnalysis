// Season identity for the era-coloured charts: what a season is called and
// which palette step it gets. Both are read from the archive rather than listed
// here, so a season ingested after a chart was written still names itself.
//
// Kept free of server-only imports: the charts are client components and take
// the season list as a prop.

export type SeasonEra = {
  year: number;
  title: string; // short_name, e.g. "BO7"
  league: string; // "CWL" / "CDL"
};

// The validated categorical palette, in its fixed order. Colour follows the
// season and is assigned by position in the archive, never by position in a
// chart, so the same season is the same colour on every page.
export const SERIES_STEPS = 8;

export function seasonInk(seasons: SeasonEra[], year: number): string {
  const i = seasons.findIndex((s) => s.year === year);
  return `var(--series-${((i === -1 ? 0 : i) % SERIES_STEPS) + 1})`;
}

/** e.g. `BO7 ’26`. */
export function seasonTag(seasons: SeasonEra[], year: number): string {
  const s = seasons.find((x) => x.year === year);
  const yy = String(year % 100).padStart(2, "0");
  return s ? `${s.title} ’${yy}` : `’${yy}`;
}

/** Seasons grouped by league, each league in the order it first appears. */
export function byLeague(seasons: SeasonEra[]): { league: string; seasons: SeasonEra[] }[] {
  const groups: { league: string; seasons: SeasonEra[] }[] = [];
  for (const s of seasons) {
    const group = groups.find((g) => g.league === s.league);
    if (group) group.seasons.push(s);
    else groups.push({ league: s.league, seasons: [s] });
  }
  return groups;
}

/**
 * Seasons that share a palette step with an earlier one, as `["BO6 repeats
 * IW"]`. The archive has more seasons than the palette has steps, so a repeat
 * is unavoidable; naming it is what keeps it from being a silent collision.
 */
export function inkRepeats(seasons: SeasonEra[], shownYears: number[]): string[] {
  const shown = seasons.filter((s) => shownYears.includes(s.year));
  const out: string[] = [];
  const byInk = new Map<string, SeasonEra>();
  for (const s of shown) {
    const ink = seasonInk(seasons, s.year);
    const first = byInk.get(ink);
    if (first) out.push(`${s.title} repeats ${first.title}`);
    else byInk.set(ink, s);
  }
  return out;
}
