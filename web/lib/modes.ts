// Mode display names and ordering, read from `game_modes` rather than listed in
// each page. The table already stores the slug and the name; a title that brings
// a new mode (Domination in 2020, Overload in 2026) names it on ingest, and
// every control that groups or labels by mode picks it up from here.
//
// Kept free of server-only imports: the report table and cohort tokens are
// client components and take the catalog as a prop. `getModeCatalog` in
// lib/analytics reads it.

export type ModeCatalog = {
  /** Slugs in `game_modes.id` order — the order modes entered the archive. */
  order: string[];
  /** Slug to display name. */
  names: Record<string, string>;
};

export const EMPTY_MODE_CATALOG: ModeCatalog = { order: [], names: {} };

/**
 * A mode's display name. `undefined` and `null` are the all-modes slice, which
 * every caller names the same way. A slug the catalog does not carry is printed
 * as itself: a mode with no name is a gap in `game_modes`, and reading as a raw
 * slug says so where a blank would hide it.
 */
export function modeLabel(
  catalog: ModeCatalog,
  slug: string | null | undefined,
  allLabel = "All modes",
): string {
  if (slug === undefined || slug === null || slug === "all") return allLabel;
  return catalog.names[slug] ?? slug;
}

/** Sort position for a mode slug; `"all"` leads and an unknown slug sorts last. */
export function modeRank(catalog: ModeCatalog, slug: string): number {
  if (slug === "all") return -1;
  const i = catalog.order.indexOf(slug);
  return i === -1 ? catalog.order.length : i;
}
