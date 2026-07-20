// Shared paging state for the server-rendered tables. Page and rows-per-page
// travel in the query string like every other filter on the site, so a paged
// view is addressable and the pages stay `force-dynamic` with no client state.

export type SearchParams = Record<string, string | string[] | undefined>;

export const PER_OPTIONS = [25, 50, 100, 250] as const;
export const DEFAULT_PER = 50;

export type Paging = {
  page: number; // 1-based, clamped once the total is known
  per: number;
  offset: number;
  limit: number;
};

/** First value for a key, ignoring repeats. */
export function one(sp: SearchParams, key: string): string {
  const v = sp[key];
  return (Array.isArray(v) ? v[0] : v) ?? "";
}

/**
 * Read `page` and `per` off the query string. The page is only lower-bounded
 * here — the upper bound needs the row count, which the caller gets from the
 * query this paging drives, so `clampPage` finishes the job afterwards.
 */
export function parsePaging(
  sp: SearchParams,
  defaultPer = DEFAULT_PER,
): Paging {
  const perRaw = Number(one(sp, "per"));
  const per = (PER_OPTIONS as readonly number[]).includes(perRaw)
    ? perRaw
    : defaultPer;
  const pageRaw = Number(one(sp, "page"));
  const page =
    Number.isFinite(pageRaw) && pageRaw >= 1 ? Math.floor(pageRaw) : 1;
  return { page, per, offset: (page - 1) * per, limit: per };
}

export function pageCount(total: number, per: number): number {
  return Math.max(1, Math.ceil(total / per));
}

/**
 * Re-clamp against the real row count. A `?page=99` deep link on a filter that
 * only has three pages lands on the last one rather than an empty table.
 */
export function clampPage(p: Paging, total: number): Paging {
  const page = Math.min(p.page, pageCount(total, p.per));
  return { ...p, page, offset: (page - 1) * p.per };
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
