// Content filters: which maps feed a re-aggregated number, beyond season and
// mode. Each one narrows the map set the aggregation path sums over, so a
// filtered cell is computed and scored exactly as an unfiltered one is. Pure,
// so the page, the export and the client menus read the URL the same way.

import { type SearchParams, one } from "@/lib/paging";
import { teamSlug } from "@/lib/slug";

/** The numeric event tier the title rule reads. */
export type EventTier = "1" | "2";

/** Where an event was played, as `events.is_lan` records it. */
export type Venue = "lan" | "online";

/**
 * Where in a competition, as `series.stage` records it: league play, or an
 * event's groups, brackets or grand final. `event` is everything but league
 * play; `bracket` includes the grand final and a league's own playoffs.
 */
export type Stage = "league" | "event" | "group" | "bracket" | "final";

export const STAGES: readonly Stage[] = ["league", "event", "group", "bracket", "final"];

/** The `series.stage` values each choice keeps. */
export const STAGE_VALUES: Record<Stage, readonly string[]> = {
  league: ["league"],
  event: ["group", "bracket", "final"],
  group: ["group"],
  bracket: ["bracket", "final"],
  final: ["final"],
};

export type ContentFilters = {
  /** Keep maps from events of this tier; null keeps every event. */
  tier: EventTier | null;
  /** Keep maps from LAN or from online events; null keeps both, and unknown. */
  venue: Venue | null;
  /** Keep maps from one stage; null keeps every map, unstaged included. */
  stage: Stage | null;
  /** Map-name slugs, any title; empty keeps every map. */
  maps: string[];
  /** Inclusive ISO days on the series date; null leaves that end open. */
  from: string | null;
  to: string | null;
};

export const NO_CONTENT: ContentFilters = {
  tier: null,
  venue: null,
  stage: null,
  maps: [],
  from: null,
  to: null,
};

/** A map's URL slug: its name, as a team's name becomes one. */
export const mapSlug = teamSlug;

/** `YYYY-MM-DD` that names a real day, or null. */
export function parseDay(raw: string | undefined): string | null {
  const t = (raw ?? "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(t)) return null;
  const [y, m, d] = t.split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d));
  return date.getUTCFullYear() === y &&
    date.getUTCMonth() === m - 1 &&
    date.getUTCDate() === d
    ? t
    : null;
}

/**
 * `?tier=1|2`, `?venue=lan|online`, `?stage=` (one of STAGES), `?map=` as a
 * CSV of map slugs, `?from=` and `?to=` as ISO days. An unreadable tier,
 * venue, stage or day is dropped. Unknown map slugs are kept: they match
 * no maps, so a stale link shows an empty view, never the unfiltered one. A
 * reversed range is read the right way round.
 */
export function parseContent(sp: SearchParams): ContentFilters {
  const tierRaw = one(sp, "tier");
  const tier: EventTier | null =
    tierRaw === "1" || tierRaw === "2" ? tierRaw : null;
  const venueRaw = one(sp, "venue");
  const venue: Venue | null =
    venueRaw === "lan" || venueRaw === "online" ? venueRaw : null;
  const stageRaw = one(sp, "stage");
  const stage: Stage | null = STAGES.find((st) => st === stageRaw) ?? null;
  const seen = new Set<string>();
  const maps: string[] = [];
  for (const part of (one(sp, "map") ?? "").split(",")) {
    const slug = mapSlug(part);
    if (slug && !seen.has(slug)) {
      seen.add(slug);
      maps.push(slug);
    }
  }
  let from = parseDay(one(sp, "from"));
  let to = parseDay(one(sp, "to"));
  if (from && to && from > to) [from, to] = [to, from];
  return { tier, venue, stage, maps, from, to };
}

export function hasContent(c: ContentFilters): boolean {
  return (
    c.tier !== null ||
    c.venue !== null ||
    c.stage !== null ||
    c.maps.length > 0 ||
    c.from !== null ||
    c.to !== null
  );
}

/** The URL keys content filters own, for links that carry or clear them. */
export const CONTENT_KEYS = ["tier", "venue", "stage", "map", "from", "to"] as const;

export function tierLabel(tier: EventTier): string {
  return `Tier ${tier} events`;
}

export function venueLabel(venue: Venue): string {
  return venue === "lan" ? "LAN only" : "Online only";
}

const STAGE_LABELS: Record<Stage, string> = {
  league: "League play",
  event: "Event play",
  group: "Group stage",
  bracket: "Brackets",
  final: "Grand finals",
};

export function stageLabel(stage: Stage): string {
  return STAGE_LABELS[stage];
}

/** "2024-01-05 to 2024-06-30", "from 2024-01-05", "to 2024-06-30". */
export function dateRangeLabel(from: string | null, to: string | null): string {
  if (from && to) return from === to ? from : `${from} to ${to}`;
  if (from) return `from ${from}`;
  if (to) return `to ${to}`;
  return "any date";
}

/** Map picks by name, on the scale players and teams use. */
export function mapsLabel(slugs: string[], nameBySlug: Map<string, string>): string {
  if (slugs.length === 0) return "All maps";
  const names = slugs.map((s) => nameBySlug.get(s) ?? s);
  return names.length <= 3 ? names.join(" + ") : `${names.length} maps`;
}

/** Each active filter as it names what it keeps, for the print stamp. */
export function contentParts(
  c: ContentFilters,
  mapNames: Map<string, string>,
): string[] {
  const out: string[] = [];
  if (c.tier) out.push(tierLabel(c.tier).toLowerCase());
  if (c.venue) out.push(venueLabel(c.venue).toLowerCase());
  if (c.stage) out.push(stageLabel(c.stage).toLowerCase());
  if (c.maps.length > 0) out.push(mapsLabel(c.maps, mapNames));
  if (c.from || c.to) out.push(dateRangeLabel(c.from, c.to));
  return out;
}

/** The filename part: `-tier1-lan-bracket-raid-2024-01-05-to-2024-06-30`. */
export function contentSlug(c: ContentFilters): string {
  const parts: string[] = [];
  if (c.tier) parts.push(`tier${c.tier}`);
  if (c.venue) parts.push(c.venue);
  if (c.stage) parts.push(c.stage);
  if (c.maps.length > 0) {
    parts.push(c.maps.length <= 3 ? c.maps.join("-") : `${c.maps.length}-maps`);
  }
  if (c.from || c.to) parts.push(`${c.from ?? "start"}-to-${c.to ?? "end"}`);
  return parts.length > 0 ? `-${parts.join("-")}` : "";
}
