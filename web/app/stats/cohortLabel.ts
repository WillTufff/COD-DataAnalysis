import type { ScopeSeason } from "@/lib/analytics";

export {
  type ModeCatalog,
  ALL_MODES_LABEL,
  EMPTY_MODE_CATALOG,
  modeLabel,
} from "@/lib/reports/labels";

/**
 * How a player or team pick names itself, on the same scale as seasons: none
 * is "All <noun>", up to three fit by name, beyond that only the count reads.
 * A slug the list doesn't know (a stale link) still counts and shows as its
 * slug, so the token never claims a narrower filter than the one applied.
 */
export function pickLabel(
  slugs: string[],
  nameBySlug: Map<string, string>,
  noun: "players" | "teams",
): string {
  if (slugs.length === 0) return `All ${noun}`;
  const names = slugs.map((s) => nameBySlug.get(s) ?? s);
  if (names.length <= 3) return names.join(" + ");
  return `${names.length} ${noun}`;
}

/**
 * How a season pick names itself, scaled to how much room the name needs:
 * everything (or nothing, which is the same cohort) is "All seasons"; a single
 * season earns its full title; two or three fit as title codes; beyond that
 * only the count is worth reading.
 */
export function seasonLabel(
  seasons: ScopeSeason[],
  years: number[],
): string {
  const picked = seasons.filter((s) => years.includes(s.year));
  if (picked.length === 0 || picked.length === seasons.length) {
    return "All seasons";
  }
  if (picked.length === 1) return picked[0].name;
  if (picked.length <= 3) return picked.map((s) => s.code).join(" + ");
  return `${picked.length} seasons`;
}
