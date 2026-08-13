// What the era-coverage pass expects to see, read from the same database the
// site renders from. Nothing here is a fixture: a hardcoded player or year
// would go stale the first time a run changed, and a stale expectation is the
// failure this pass exists to catch.
import { sql } from "drizzle-orm";
import { db } from "@/lib/db";
import { getSkillSeasons, latestSkillRun } from "@/lib/analytics";
import { playerSlug } from "@/lib/slug";

export type EraSample = {
  league: string;
  year: number;
  slug: string;
  handle: string;
};

/** One rated player per league, picked by map count so the page is populated. */
export async function eraSamples(): Promise<EraSample[]> {
  const rows = (await db.execute(sql`
    SELECT DISTINCT ON (s.league)
      s.league, s.year, p.handle
    FROM player_season_adjusted a
    JOIN seasons s ON s.id = a.season_id
    JOIN players p ON p.id = a.player_id
    WHERE a.mode_id IS NULL
      AND a.run_id = (SELECT max(run_id) FROM player_season_adjusted)
    ORDER BY s.league, a.maps_played DESC
  `)) as unknown as { league: string; year: number; handle: string }[];
  return rows.map((r) => ({
    league: r.league,
    year: r.year,
    handle: r.handle,
    slug: playerSlug(r.handle),
  }));
}

/** The seasons SKILL is published for, which is what the primacy rule turns on. */
export async function skillYears(): Promise<number[]> {
  const run = await latestSkillRun();
  if (!run) return [];
  return (await getSkillSeasons(run.id)).map((s) => s.year);
}

/** Whether there is a fitted model to check at all. */
export async function hasRatings(): Promise<boolean> {
  const rows = (await db.execute(sql`
    SELECT count(*)::int AS n FROM player_season_adjusted
  `)) as unknown as { n: number }[];
  return (rows[0]?.n ?? 0) > 0;
}
