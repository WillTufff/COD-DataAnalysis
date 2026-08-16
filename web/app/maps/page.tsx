import type { Metadata } from "next";
import Link from "next/link";
import {
  type MapModeGroup,
  type MapSeason,
  getMapMeta,
  getModeCatalog,
} from "@/lib/analytics";
import { type ModeCatalog, modeLabel } from "@/lib/modes";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Maps" };

function one(sp: Record<string, string | string[] | undefined>, k: string): string {
  const v = sp[k];
  return (Array.isArray(v) ? v[0] : v) ?? "";
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

/** Share of the mode's games, as a bar that reads at a glance next to the number. */
function ShareBar({ share }: { share: number }) {
  const W = 120;
  const w = Math.max(share > 0 ? 2 : 0, share * W);
  return (
    <svg
      width={W}
      height={10}
      viewBox={`0 0 ${W} 10`}
      role="img"
      aria-label={`${pct(share)} of the mode's games`}
    >
      <rect x={0} y={2} width={W} height={6} rx={2} fill="var(--baseline)" />
      <rect x={0} y={2} width={w} height={6} rx={2} fill="var(--series-1)" />
    </svg>
  );
}

// A new title replaces the map pool wholesale, so "new" would mark every row
// and say nothing. Across a title change the informative mark is the opposite
// one: which maps survived it.
type Churn = { marked: Set<string>; label: string } | null;

function ModeTable({
  group,
  churn,
  scoreLabel,
  modes,
}: {
  group: MapModeGroup;
  churn: Churn;
  scoreLabel: string;
  modes: ModeCatalog;
}) {
  // A decider column of all zeros says nothing; modes that never close a series
  // simply don't get one.
  const showDeciders = group.maps.some((m) => m.decidersShare > 0);
  return (
    <div className="mt-6">
      <h3 className="lower-third">
        {modeLabel(modes, group.mode)}
        <span className="lt-note">
          {group.games.toLocaleString()} maps · {group.maps.length} in the pool
        </span>
      </h3>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[34rem] text-left text-sm">
          <thead>
            <tr className="border-b border-hairline text-xs text-ink-muted">
              <th className="py-2 pr-4 font-normal">Map</th>
              <th className="py-2 pr-4 text-right font-normal">Games</th>
              <th className="py-2 pr-4 font-normal">Share of mode</th>
              <th className="py-2 pr-4 text-right font-normal">{scoreLabel}</th>
              {showDeciders && (
                <th className="py-2 text-right font-normal">Closed a series</th>
              )}
            </tr>
          </thead>
          <tbody>
            {group.maps.map((m) => (
              <tr key={m.map} className="border-b border-hairline/60">
                <td className="py-2 pr-4">
                  {m.map}
                  {churn?.marked.has(m.map) && (
                    <span className="ml-2 font-mono text-[0.6rem] uppercase text-accent">
                      {churn.label}
                    </span>
                  )}
                </td>
                <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                  {m.games}
                </td>
                <td className="py-2 pr-4">
                  <div className="flex items-center gap-3">
                    <ShareBar share={m.share} />
                    <span className="font-mono text-xs tabular-nums text-ink-secondary">
                      {pct(m.share)}
                    </span>
                  </div>
                </td>
                <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums">
                  {m.avgLoseScore === null ? "—" : m.avgLoseScore.toFixed(1)}
                </td>
                {showDeciders && (
                  <td className="py-2 text-right font-mono text-xs tabular-nums">
                    {m.decidersShare > 0 ? pct(m.decidersShare) : "—"}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default async function MapsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const [seasons, modeCatalog] = await Promise.all([
    getMapMeta(),
    getModeCatalog(),
  ]);

  if (seasons.length === 0) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-10">
        <h1 className="font-display text-3xl font-semibold uppercase">Maps</h1>
        <p className="mt-3 text-sm text-ink-secondary">
          No decided maps are loaded yet.
        </p>
      </main>
    );
  }

  const wanted = Number(one(sp, "year"));
  const season: MapSeason =
    seasons.find((s) => s.year === wanted) ?? seasons[0];

  const index = seasons.indexOf(season);
  const previous = seasons[index + 1];
  const before = new Set(
    (previous?.modes ?? []).flatMap((m) => m.maps.map((x) => x.map)),
  );
  const names = season.modes.flatMap((m) => m.maps.map((x) => x.map));
  const pool = new Set(names);
  const churn: Churn =
    previous === undefined
      ? null
      : previous.title === season.title
        ? { marked: new Set(names.filter((n) => !before.has(n))), label: "new" }
        : { marked: new Set(names.filter((n) => before.has(n))), label: "held over" };

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <h1 className="font-display text-3xl font-semibold uppercase">Maps</h1>
      <p className="mt-3 max-w-3xl text-sm leading-relaxed text-ink-secondary">
        Which maps were actually played, season by season. The share column is
        the map pool as the league used it rather than as it was announced — a
        map in the rotation that teams kept vetoing shows up small here. Counted
        over decided maps only.
      </p>
      <p className="mt-2 max-w-3xl text-sm leading-relaxed text-ink-secondary">
        The score column is the <em>losing</em> score, which is the one that
        carries information: Hardpoint and Control are races to a fixed cap, so
        every winning score is the same number, and how far the loser got is
        what says whether a map plays close.{" "}
        <Link className="underline" href="/methodology/attribution">
          Sources and provenance
        </Link>.
      </p>

      <nav className="mt-6 flex flex-wrap gap-2" aria-label="Season">
        {seasons.map((s) => (
          <Link
            key={s.year}
            href={`/maps?year=${s.year}`}
            className={
              s.year === season.year
                ? "border border-ink px-2 py-1 font-mono text-xs"
                : "border border-hairline px-2 py-1 font-mono text-xs text-ink-secondary hover:border-ink-secondary"
            }
          >
            {s.year} {s.title}
          </Link>
        ))}
      </nav>

      <section className="mt-8">
        <h2 className="font-display text-2xl font-semibold uppercase">
          {season.year} {season.title}
          <span className="ml-3 font-body text-sm font-normal normal-case text-ink-muted">
            {season.league} · {season.games.toLocaleString()} decided maps
          </span>
        </h2>
        {previous !== undefined && (
          <p className="mt-2 text-sm text-ink-secondary">
            {churn?.label === "new"
              ? `${churn.marked.size} of ${pool.size} maps new since ${previous.year} ${previous.title}.`
              : `${churn?.marked.size ?? 0} of ${pool.size} maps carried over from ${previous.year} ${previous.title} — a new title replaces the pool.`}
          </p>
        )}
        {season.modes.map((group) => (
          <ModeTable
            key={group.mode}
            group={group}
            churn={churn}
            modes={modeCatalog}
            scoreLabel={
              group.mode === "search-and-destroy" || group.mode === "overload"
                ? "Avg losing rounds"
                : "Avg losing score"
            }
          />
        ))}
      </section>
    </main>
  );
}
