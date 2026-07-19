import Link from "next/link";
import { notFound } from "next/navigation";
import { CareerArc, type ArcPoint } from "@/components/charts/CareerArc";
import {
  PercentileProfile,
  type ProfileStat,
} from "@/components/charts/PercentileProfile";
import { PctlBar } from "@/components/PctlBar";
import { Tabs } from "@/components/Tabs";
import {
  getPlayerAdjusted,
  getPlayerBySlug,
  getPlayerInsights,
  getPlayerStints,
  latestRun,
  teamSlug,
  type SeasonAdjusted,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

function fmtZ(z: number | null): string {
  if (z === null) return "—";
  return `${z >= 0 ? "+" : ""}${z.toFixed(2)}σ`;
}

// Standard normal CDF (Abramowitz & Stegun 7.1.26), used to place a cohort
// z-score on the same 0-100 track as the exact K/D percentile.
function zToPctl(z: number): number {
  const t = 1 / (1 + 0.3275911 * Math.abs(z));
  const erf =
    1 -
    (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) *
      t +
      0.254829592) *
      t *
      Math.exp(-z * z);
  const p = 0.5 * (1 + (z < 0 ? -erf : erf));
  return Math.max(0, Math.min(1, p));
}

function seasonProfile(a: SeasonAdjusted): ProfileStat[] {
  const stats: ProfileStat[] = [];
  if (a.kdPctl !== null && a.kdRaw !== null) {
    stats.push({ label: "K/D", pctl: a.kdPctl, value: a.kdRaw.toFixed(2) });
  }
  if (a.engagementZ !== null) {
    stats.push({
      label: "Engagement",
      pctl: zToPctl(a.engagementZ),
      value: fmtZ(a.engagementZ),
    });
  }
  if (a.objZ !== null) {
    stats.push({
      label: "Objective",
      pctl: zToPctl(a.objZ),
      value: fmtZ(a.objZ),
    });
  }
  return stats;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const player = await getPlayerBySlug(slug.toLowerCase());
  return { title: player?.handle ?? "Player" };
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
  const profileSeasons = allModes.filter((a) => seasonProfile(a).length > 0);

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <p className="eyebrow text-accent">Player · CWL 2017–2019 archive</p>
      <h1 className="mt-1 font-display text-5xl font-bold uppercase tracking-tight">
        {player.handle}
      </h1>
      <p className="mt-2 text-sm text-ink-secondary">
        {careerMaps} archived maps
        {teamsPlayed.length > 0 && (
          <>
            {" · "}
            {teamsPlayed.map((t, i) => (
              <span key={t}>
                {i > 0 && " · "}
                <Link
                  href={`/teams/${teamSlug(t)}`}
                  className="hover:text-accent hover:underline"
                >
                  {t}
                </Link>
              </span>
            ))}
          </>
        )}
      </p>

      {profileSeasons.length > 0 && (
        <section className="mt-10">
          <h2 className="lower-third">
            Season profile
            <span className="lt-note">percentile within season-and-title cohort</span>
          </h2>
          <div className="mt-4 grid grid-cols-1 gap-x-10 gap-y-6 md:grid-cols-2">
            {profileSeasons.map((a) => (
              <div key={a.seasonId}>
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="font-display text-lg font-semibold uppercase">
                    {a.year} {a.title}
                  </span>
                  <span className="font-mono text-xs text-ink-muted">
                    {a.mapsPlayed} maps
                  </span>
                </div>
                <PercentileProfile stats={seasonProfile(a)} />
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-ink-muted">
            K/D percentile is exact within the cohort. Engagement and objective
            are cohort z-scores placed on the percentile track through a normal
            approximation.
          </p>
        </section>
      )}

      <section className="mt-10">
        <h2 className="lower-third">Career arc</h2>
        <div className="mt-3 border border-hairline bg-surface p-4">
          {arcPoints.length > 0 ? (
            <CareerArc points={arcPoints} />
          ) : (
            <p className="py-10 text-center text-sm text-ink-muted">
              Not enough qualified maps in any season for a cohort comparison.
              The era model requires at least 8 maps in a season-mode cohort.
            </p>
          )}
        </div>
      </section>

      <section className="mt-10">
        <h2 className="lower-third mb-3">Season detail</h2>
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
                    &ldquo;vs cohort&rdquo; is standard deviations from the
                    qualified-player mean of that season and title. Coverage is
                    the share of this player&rsquo;s map rows with complete
                    kill and death data. Stats the archive lacks are shown as
                    &ldquo;—&rdquo;.
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
                    Objective is the mode&rsquo;s own metric (hill time, S&D
                    opening plays, captures) as a cohort z-score. A &ldquo;—&rdquo;
                    means the archive lacks that stat for the season, or the
                    player didn&rsquo;t qualify.
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
                        className="border border-hairline bg-surface p-3 text-sm"
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
