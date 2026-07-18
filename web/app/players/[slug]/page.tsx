import { notFound } from "next/navigation";
import { CareerArc, type ArcPoint } from "@/components/charts/CareerArc";
import { PctlBar } from "@/components/PctlBar";
import { Tabs } from "@/components/Tabs";
import {
  getPlayerAdjusted,
  getPlayerBySlug,
  getPlayerInsights,
  getPlayerStints,
  latestRun,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

function fmtZ(z: number | null): string {
  if (z === null) return "—";
  return `${z >= 0 ? "+" : ""}${z.toFixed(2)}σ`;
}

export default async function PlayerPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const player = await getPlayerBySlug(slug.toLowerCase());
  if (!player) notFound();

  const [eraRun, insightsRun] = await Promise.all([
    latestRun("era_adjust"),
    latestRun("insights"),
  ]);
  const [adjusted, stints, playerInsights] = await Promise.all([
    eraRun ? getPlayerAdjusted(player.id, eraRun.id) : Promise.resolve([]),
    getPlayerStints(player.id),
    insightsRun ? getPlayerInsights(player.id, insightsRun.id) : Promise.resolve([]),
  ]);

  const allModes = adjusted.filter((a) => a.modeId === null);
  const byMode = adjusted.filter((a) => a.modeId !== null);
  const careerMaps = allModes.reduce((s, a) => s + a.mapsPlayed, 0);

  const arcPoints: ArcPoint[] = allModes
    .filter((a) => a.kdZ !== null && a.kdPctl !== null)
    .map((a) => ({
      year: a.year,
      title: a.title,
      kdZ: a.kdZ as number,
      kdPctl: a.kdPctl as number,
      maps: a.mapsPlayed,
    }));

  const teamsPlayed = [...new Map(stints.map((s) => [s.teamId, s.team])).values()];

  return (
    <main className="mx-auto max-w-5xl px-6 py-10">
      <p className="eyebrow text-accent">Player · CWL 2017–2019 archive</p>
      <h1 className="mt-1 font-display text-5xl font-bold uppercase tracking-tight">
        {player.handle}
      </h1>
      <p className="mt-2 text-sm text-ink-secondary">
        {careerMaps} archived maps
        {teamsPlayed.length > 0 && <> · {teamsPlayed.join(" · ")}</>}
      </p>

      <section className="mt-10">
        <h2 className="eyebrow text-ink-secondary">Era-adjusted career arc</h2>
        <div className="mt-3 rounded border border-hairline bg-surface p-4">
          {arcPoints.length > 0 ? (
            <CareerArc points={arcPoints} />
          ) : (
            <p className="py-10 text-center text-sm text-ink-muted">
              Not enough qualified maps in any season for a cohort comparison
              (the era model requires at least 8 maps in a season×mode cohort).
            </p>
          )}
        </div>
      </section>

      <section className="mt-10">
        <h2 className="eyebrow mb-3 text-ink-secondary">Evidence</h2>
        <Tabs
          tabs={[
            {
              label: "Seasons",
              content: (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-hairline text-xs text-ink-muted">
                        <th className="py-2 pr-4 font-normal">Season</th>
                        <th className="py-2 pr-4 text-right font-normal">Maps</th>
                        <th className="py-2 pr-4 text-right font-normal">K/D (raw)</th>
                        <th className="py-2 pr-4 text-right font-normal">vs cohort</th>
                        <th className="py-2 pr-4 font-normal">Percentile</th>
                        <th className="py-2 text-right font-normal">Coverage</th>
                      </tr>
                    </thead>
                    <tbody>
                      {allModes.map((a) => (
                        <tr key={a.seasonId} className="border-b border-hairline/60">
                          <td className="py-2 pr-4">
                            {a.year} <span className="text-ink-muted">{a.title}</span>
                          </td>
                          <td className="py-2 pr-4 text-right font-mono tabular-nums">
                            {a.mapsPlayed}
                          </td>
                          <td className="py-2 pr-4 text-right font-mono tabular-nums">
                            {a.kdRaw?.toFixed(2) ?? "—"}
                          </td>
                          <td className="py-2 pr-4 text-right font-mono tabular-nums">
                            {fmtZ(a.kdZ)}
                          </td>
                          <td className="py-2 pr-4">
                            {a.kdPctl !== null ? <PctlBar pctl={a.kdPctl} /> : "—"}
                          </td>
                          <td className="py-2 text-right font-mono tabular-nums text-ink-muted">
                            {Math.round(a.completeness * 100)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-2 text-xs text-ink-muted">
                    “vs cohort” is standard deviations from the qualified-player mean
                    of that season and title. Coverage is the share of this player’s
                    map rows with complete kill/death data — missing stats stay
                    missing, never imputed.
                  </p>
                </div>
              ),
            },
            {
              label: "By mode",
              content: (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b border-hairline text-xs text-ink-muted">
                        <th className="py-2 pr-4 font-normal">Season</th>
                        <th className="py-2 pr-4 font-normal">Mode</th>
                        <th className="py-2 pr-4 text-right font-normal">Maps</th>
                        <th className="py-2 pr-4 text-right font-normal">K/D</th>
                        <th className="py-2 pr-4 font-normal">Percentile</th>
                        <th className="py-2 text-right font-normal">Objective</th>
                      </tr>
                    </thead>
                    <tbody>
                      {byMode.map((a) => (
                        <tr
                          key={`${a.seasonId}-${a.modeId}`}
                          className="border-b border-hairline/60"
                        >
                          <td className="py-2 pr-4">
                            {a.year} <span className="text-ink-muted">{a.title}</span>
                          </td>
                          <td className="py-2 pr-4">{a.mode}</td>
                          <td className="py-2 pr-4 text-right font-mono tabular-nums">
                            {a.mapsPlayed}
                          </td>
                          <td className="py-2 pr-4 text-right font-mono tabular-nums">
                            {a.kdRaw?.toFixed(2) ?? "—"}
                          </td>
                          <td className="py-2 pr-4">
                            {a.kdPctl !== null ? <PctlBar pctl={a.kdPctl} /> : "—"}
                          </td>
                          <td className="py-2 text-right font-mono tabular-nums">
                            {fmtZ(a.objZ)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-2 text-xs text-ink-muted">
                    Objective is the mode’s own metric (hill time, S&D opening plays,
                    captures…) as a cohort z-score. “—” means the archive doesn’t
                    record that stat for the season, or the player didn’t qualify.
                  </p>
                </div>
              ),
            },
            {
              label: `Insights (${playerInsights.length})`,
              content:
                playerInsights.length > 0 ? (
                  <ul className="space-y-3">
                    {playerInsights.map((i) => (
                      <li
                        key={i.id}
                        className="rounded border border-hairline bg-surface p-3 text-sm"
                      >
                        <span className="eyebrow mr-2 text-[10px] text-accent">
                          {i.kind.replace("_", " ")}
                        </span>
                        {i.headline}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-ink-muted">
                    No model-generated insights for this player in the current run.
                  </p>
                ),
            },
          ]}
        />
      </section>
    </main>
  );
}
