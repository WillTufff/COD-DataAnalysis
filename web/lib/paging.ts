// Shared paging state for the site's tables. Rows-per-page, page, and column
// sort travel in the query string so a view stays addressable and shareable.
// The tables slice and sort themselves in the browser now, so these helpers
// exist mainly to seed that client state from the URL and to keep filter forms
// carrying the current settings through a server round-trip.

export type SearchParams = Record<string, string | string[] | undefined>;

// "all" is a real option: show every row, no page limit. It rides the URL as
// the literal string `all`; every numeric option is itself.
export type Per = number | "all";

export const PER_OPTIONS: readonly Per[] = [10, 20, 50, 100, "all"];
export const DEFAULT_PER: Per = 10;

/** Human label for a rows-per-page option. */
export function perLabel(p: Per): string {
  return p === "all" ? "All" : String(p);
}

/** The concrete slice size for a `Per`, given how many rows exist in total. */
export function perLimit(p: Per, total: number): number {
  return p === "all" ? Math.max(total, 1) : p;
}

/** First value for a key, ignoring repeats. */
export function one(sp: SearchParams, key: string): string {
  const v = sp[key];
  return (Array.isArray(v) ? v[0] : v) ?? "";
}

/** Read `per` off the query string, falling back to the default. */
export function parsePer(sp: SearchParams, defaultPer: Per = DEFAULT_PER): Per {
  const raw = one(sp, "per");
  if (raw === "all") return "all";
  const n = Number(raw);
  return (PER_OPTIONS as readonly Per[]).includes(n) ? n : defaultPer;
}

/** Read `page` off the query string (1-based, lower-bounded only). */
export function parsePage(sp: SearchParams): number {
  const raw = Number(one(sp, "page"));
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;
}

export function pageCount(total: number, per: number): number {
  return Math.max(1, Math.ceil(total / Math.max(per, 1)));
}

/**
 * Copy the current query string with `patch` applied. `null` and `""` drop a
 * key, which is how callers keep a default off the URL. Repeated keys not
 * named in the patch are preserved as-is. Keys are sorted so the same view
 * always produces the same link.
 */
export function buildQuery(
  sp: SearchParams,
  patch: Record<string, string | number | null>,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(sp)) {
    if (k in patch || v === undefined) continue;
    for (const item of Array.isArray(v) ? v : [v]) qs.append(k, item);
  }
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === "") continue;
    qs.set(k, String(v));
  }
  qs.sort();
  const s = qs.toString();
  return s ? `?${s}` : "";
}
