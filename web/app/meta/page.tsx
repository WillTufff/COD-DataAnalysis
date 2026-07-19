import type { Metadata } from "next";
import { getMetaArtifacts, latestRun } from "@/lib/analytics";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Loadout meta" };

const SECTION_LABELS: Record<string, { title: string; blurb: string }> = {
  meta_weapons: {
    title: "Weapons",
    blurb: "Share of player-maps on which each weapon was the player's most used.",
  },
  meta_specialists: {
    title: "Specialists",
    blurb: "Black Ops 4 specialists. Recorded on a small share of maps, so read as indicative only.",
  },
  meta_divisions: {
    title: "Divisions",
    blurb: "WWII divisions. Recorded on roughly half of maps.",
  },
  meta_training: { title: "Basic training", blurb: "WWII basic training picks." },
  meta_scorestreaks: { title: "Scorestreaks", blurb: "Most used scorestreak per player-map." },
  meta_rigs: { title: "Rigs", blurb: "Infinite Warfare combat rigs." },
  meta_payloads: { title: "Payloads", blurb: "Infinite Warfare rig payloads." },
  meta_traits: { title: "Traits", blurb: "Infinite Warfare rig traits." },
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

export default async function MetaPage() {
  const run = await latestRun("metric_layer");
  const artifacts = run ? await getMetaArtifacts(run.id) : [];

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <p className="font-mono text-xs text-ink-muted">
        What the field actually ran · metric_layer v{run?.version ?? "—"}
      </p>
      <h1 className="mt-2 font-display text-5xl font-bold uppercase tracking-tight">
        Loadout meta
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
        Every loadout choice the archive records, by season and mode. The bar is
        usage share; the vertical mark is that choice&rsquo;s map win rate, against a
        centre line at 50%. Choices under 30 player-maps are left out.
      </p>

      {artifacts.length === 0 && (
        <p className="mt-8 text-sm text-ink-secondary">No loadout data published yet.</p>
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
                    {group.title} · {MODE_LABELS[group.mode] ?? group.mode}
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
        Usage share is measured in player-maps, not matches, and a win rate near 50%
        is the expected result for a widely used choice: both teams field the same
        meta. Differences here describe what was popular, not what was strongest.
      </p>
    </main>
  );
}
