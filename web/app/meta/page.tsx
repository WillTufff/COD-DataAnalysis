import type { Metadata } from "next";
import { getMetaArtifacts, latestRun } from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Loadouts" };

const SECTION_LABELS: Record<string, { title: string; blurb: string }> = {
  meta_weapons: {
    title: "Weapons",
    blurb: "Share of player-maps on which each weapon was the player's most used.",
  },
  meta_specialists: {
    title: "Specialists",
    blurb: "Recorded on a small share of maps, so read as indicative only.",
  },
  meta_divisions: {
    title: "Divisions",
    blurb: "Recorded on roughly half of maps.",
  },
  meta_training: { title: "Basic training", blurb: "" },
  meta_scorestreaks: { title: "Scorestreaks", blurb: "Most used scorestreak per player-map." },
  meta_rigs: { title: "Rigs", blurb: "Combat rigs." },
  meta_payloads: { title: "Payloads", blurb: "Rig payloads." },
  meta_traits: { title: "Traits", blurb: "Rig traits." },
};

const MODE_LABELS: Record<string, string> = {
  hardpoint: "Hardpoint",
  "search-and-destroy": "Search & Destroy",
  control: "Control",
  "capture-the-flag": "Capture the Flag",
  uplink: "Uplink",
};

/** Usage share bar with the win-rate mark laid over it. */
function UsageBar({ share, winRate }: { share: number; winRate: number | null }) {
  const W = 220;
  const w = Math.max(1, share * W);
  const mark = winRate === null ? null : winRate * W;
  return (
    <svg
      width={W}
      height={14}
      viewBox={`0 0 ${W} 14`}
      role="img"
      aria-label={`${(share * 100).toFixed(1)}% usage${
        winRate === null ? "" : `, ${(winRate * 100).toFixed(1)}% map win rate`
      }`}
    >
      <rect x={0} y={3} width={W} height={8} fill="var(--baseline)" />
      <rect x={0} y={3} width={w} height={8} fill="var(--series-1)" />
      <line x1={W / 2} x2={W / 2} y1={1} y2={13} stroke="var(--ink-muted)" strokeWidth={1} />
      {mark !== null && (
        <rect x={Math.max(0, mark - 1)} y={0} width={2} height={14} fill="var(--series-3)" />
      )}
    </svg>
  );
}

function one(sp: Record<string, string | string[] | undefined>, k: string): string {
  const v = sp[k];
  return (Array.isArray(v) ? v[0] : v) ?? "";
}

export default async function LoadoutsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const run = await latestRun("metric_layer");
  const all = run ? await getMetaArtifacts(run.id) : [];

  // One title at a time. Most sections are title-exclusive anyway — rigs are
  // Infinite Warfare, divisions are WWII, specialists are Black Ops 4 — so
  // picking a title drops the sections that could never apply to it.
  const titles = [...new Set(all.flatMap((a) => a.groups.map((g) => g.title)))];
  const title = titles.includes(one(sp, "t")) ? one(sp, "t") : titles[0];

  const modes = [
    ...new Set(
      all.flatMap((a) =>
        a.groups.filter((g) => g.title === title).map((g) => g.mode),
      ),
    ),
  ];
  const mode = modes.includes(one(sp, "mode")) ? one(sp, "mode") : undefined;

  const artifacts = all
    .map((a) => ({
      ...a,
      groups: a.groups.filter(
        (g) => g.title === title && (mode === undefined || g.mode === mode),
      ),
    }))
    .filter((a) => a.groups.length > 0);

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        What the field ran · metric_layer v{run?.version ?? "—"}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Loadouts
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        Every loadout choice the archive records, one title at a time. The bar is
        usage share; the vertical mark is that choice&rsquo;s map win rate, against a
        centre line at 50%. Choices under 30 player-maps are left out.
      </p>

      {titles.length > 0 && (
        <form
          method="GET"
          className="mt-8 flex flex-wrap items-end gap-x-5 gap-y-3 border-y border-hairline py-4 text-sm"
        >
          <label className="flex flex-col gap-1">
            <span className="text-xs text-ink-muted">Title</span>
            <select
              name="t"
              defaultValue={title}
              className="border border-hairline bg-surface px-2 py-1.5"
            >
              {titles.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-ink-muted">Mode</span>
            <select
              name="mode"
              defaultValue={mode ?? ""}
              className="border border-hairline bg-surface px-2 py-1.5"
            >
              <option value="">All modes</option>
              {modes.map((m) => (
                <option key={m} value={m}>
                  {MODE_LABELS[m] ?? m}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            className="border border-accent-dim bg-surface-raised px-4 py-1.5 font-display text-sm font-semibold uppercase tracking-wide text-ink hover:border-accent"
          >
            View
          </button>
        </form>
      )}

      {artifacts.length === 0 && (
        <p className="mt-8 text-sm text-ink-secondary">
          {all.length === 0
            ? "No loadout data published yet."
            : "No loadout data recorded for this combination."}
        </p>
      )}

      {artifacts.map((artifact) => {
        const label = SECTION_LABELS[artifact.name] ?? {
          title: artifact.name,
          blurb: "",
        };
        return (
          <section key={artifact.name} className="mt-12">
            <h2 className="lower-third">{label.title}</h2>
            {label.blurb && (
              <p className="mt-2 max-w-2xl text-sm text-ink-secondary">{label.blurb}</p>
            )}
            <div className="mt-4 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
              {artifact.groups.map((group) => (
                <div key={`${group.season_id}-${group.mode}`}>
                  <div className="eyebrow text-[10px] text-ink-secondary">
                    {MODE_LABELS[group.mode] ?? group.mode}
                  </div>
                  <table className="mt-2 w-full text-left text-sm">
                    <tbody>
                      {group.entries.map((e) => (
                        <tr key={e.name} className="border-b border-hairline/60">
                          <td className="py-1 pr-3">{e.name}</td>
                          <td className="py-1 pr-3">
                            <UsageBar share={e.share} winRate={e.map_win_rate} />
                          </td>
                          <td className="py-1 pr-3 text-right font-mono text-xs tabular-nums">
                            {(e.share * 100).toFixed(1)}%
                          </td>
                          <td className="py-1 pr-3 text-right font-mono text-xs tabular-nums text-ink-secondary">
                            {e.map_win_rate === null
                              ? "—"
                              : `${(e.map_win_rate * 100).toFixed(1)}%`}
                          </td>
                          <td className="py-1 text-right font-mono text-xs tabular-nums text-ink-muted">
                            {e.n_player_maps}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          </section>
        );
      })}

      <p className="mt-10 max-w-3xl text-xs text-ink-muted">
        Usage share is measured in player-maps, not matches. A win rate near 50% is
        the expected result for a widely used choice, since both teams field it.
        These figures measure popularity; they are not a strength ranking.
      </p>
    </main>
  );
}
