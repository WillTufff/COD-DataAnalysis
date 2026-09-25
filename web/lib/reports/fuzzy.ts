// Forgiving name search for the report's pickers: case, accents and punctuation
// fold away, and a handle matches on a prefix, a substring, its letters in
// order, or one or two typos.

/** Lowercase letters and digits only, with accents stripped. */
export function foldName(s: string): string {
  return s
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

/** Levenshtein distance with an adjacent swap counted as one edit. */
function editDistance(a: string, b: string): number {
  const rows: number[][] = [];
  for (let i = 0; i <= a.length; i++) {
    rows.push([i]);
    for (let j = 1; j <= b.length; j++) {
      if (i === 0) {
        rows[0][j] = j;
        continue;
      }
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let d = Math.min(
        rows[i - 1][j] + 1,
        rows[i][j - 1] + 1,
        rows[i - 1][j - 1] + cost,
      );
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        d = Math.min(d, rows[i - 2][j - 2] + 1);
      }
      rows[i][j] = d;
    }
  }
  return rows[a.length][b.length];
}

/** Whether every letter of `q` appears in `s` in order. */
function isSubsequence(q: string, s: string): boolean {
  let i = 0;
  for (const ch of s) if (ch === q[i]) i++;
  return i === q.length;
}

/**
 * How well a folded query matches a folded name: lower is better, null is no
 * match. Exact, then prefix, substring, in-order letters, then typos.
 */
export function matchScore(q: string, name: string): number | null {
  if (q === "") return 0;
  if (name === q) return 0;
  if (name.startsWith(q)) return 1;
  if (name.includes(q)) return 2;
  if (q.length >= 2 && isSubsequence(q, name)) return 3;
  const allowed = q.length >= 6 ? 2 : q.length >= 3 ? 1 : 0;
  if (allowed === 0) return null;
  // A typo in a partial query is judged against the name's same-length start,
  // so "scmp" finds "Scump" and "crimsix" finds "Crimsix" alike.
  const d = Math.min(
    editDistance(q, name),
    editDistance(q, name.slice(0, q.length)),
  );
  return d <= allowed ? 3 + d : null;
}

/**
 * Items ranked by the best match across their fields. The first field is the
 * name; later fields (a team, say) match too, but rank below any name match.
 * Ties keep the input order.
 */
export function fuzzyRank<T>(
  items: T[],
  query: string,
  fields: (item: T) => string[],
): T[] {
  const q = foldName(query);
  if (q === "") return items;
  const scored: { item: T; score: number; index: number }[] = [];
  items.forEach((item, index) => {
    let best: number | null = null;
    fields(item).forEach((field, f) => {
      const s = matchScore(q, foldName(field));
      if (s === null) return;
      const ranked = f === 0 ? s : s + 10;
      if (best === null || ranked < best) best = ranked;
    });
    if (best !== null) scored.push({ item, score: best, index });
  });
  scored.sort((a, b) => a.score - b.score || a.index - b.index);
  return scored.map((s) => s.item);
}
