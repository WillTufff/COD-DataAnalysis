import Link from "next/link";
import { notFound } from "next/navigation";
import { EloExplorer } from "@/components/charts/EloExplorer";
import { ModeSplitBars } from "@/components/charts/ModeSplitBars";
import {
  ModeStrengthBars,
  ModeStrengthNull,
} from "@/components/charts/ModeStrengthBars";
import { PlacementTimeline } from "@/components/charts/PlacementTimeline";
import { StintTimeline } from "@/components/charts/StintTimeline";
import { PctlBar } from "@/components/PctlBar";
import {
  formatLeagueSpans,
  getAllTeamSlugs,
  getEloTimelines,
  getEraSpans,
  getEventMarkers,
  getSeriesRecords,
  getTeamBySlug,
  getTeamH2H,
  getTeamModeSplits,
  getTeamModeStrength,
  getTeamPlacements,
  getTeamSpans,
  getTeamStints,
  getTeamStandings,
  getMetricCatalog,
  getTeamMetrics,
  latestRun,
  type MetricCatalog,
  type TeamMetricValue,
  type TeamStint,
  getModeCatalog,
} from "@/lib/analytics";
import { modeLabel } from "@/lib/modes";

type StyleRow = {
  key: string;
  year: number;
  label: string;
  mode: string | null;
  value: string;
  pctl: number | null;
  maps: number;
  note: string | null;
};

// Team style metrics, newest season first, dropping slices below the sample
// minimum so a two-map showing cannot read as a playstyle.
function buildStyleRows(
  values: TeamMetricValue[],
  catalog: MetricCatalog | null,
): StyleRow[] {
  const teamCatalog = catalog?.team_metrics;
  if (!teamCatalog) return [];
  const byKey = new Map(teamCatalog.map((m) => [m.key, m]));
  return values
    .filter((v) => v.qualified && byKey.has(v.metric))
    .map((v) => {
      const entry = byKey.get(v.metric)!;
      const value =
        entry.unit.startsWith("share")
          ? `${(v.value * 100).toFixed(1)}%`
          : v.value.toFixed(entry.unit === "points" ? 1 : 3);
      return {
        key: `${v.year}-${v.metric}-${v.mode ?? "all"}`,
        year: v.year,
        label: entry.label,
        mode: v.mode,
        value,
        // Flip so a full bar always means the better end of the metric.
        pctl:
          v.pctl === null ? null : entry.higher_is_better ? v.pctl : 1 - v.pctl,
        maps: Math.round(v.denom),
        note: entry.note,
      };
    })
    .sort((a, b) => b.year - a.year || a.label.localeCompare(b.label));
}

// What the timeline below it was actually built from: Liquipedia carries dated
// roster moves, the CWL archive carries none, so its stints are the span of a
// player's appearances for the team.
function stintNote(stints: TeamStint[]): string {
  const dated = stints.some((s) => s.source === "lpdb");
  const inferred = stints.some((s) => s.source !== "lpdb");
  if (dated && inferred) return "dated roster moves, plus stints inferred from play";
  if (dated) return "dated roster moves";
  return "inferred from archive appearances";
}

// The archive is frozen and the models only change on a rerun, so this page is
// prerendered and revalidated on a timer rather than queried per request.
export const revalidate = 3600;

// Every team in the archive is known at build time, so all of them prerender
// rather than each waiting for its first visitor.
export async function generateStaticParams() {
  const slugs = await getAllTeamSlugs();
  return slugs.map((slug) => ({ slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const team = await getTeamBySlug(slug.toLowerCase());
  return { title: team?.name ?? "Team" };
}

export default async function TeamPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const team = await getTeamBySlug(slug.toLowerCase());
  if (!team) notFound();

  const [eloRun, glickoRun, metricRun] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
    latestRun("metric_layer"),
  ]);
  if (!eloRun) notFound();

  const [
    standings,
    records,
    timelines,
    eras,
    events,
    placements,
    spans,
    stints,
    modeSplits,
    modeStrength,
    h2h,
    teamMetrics,
    metricCatalog,
    modeCatalog,
  ] = await Promise.all([
      getTeamStandings(eloRun.id, glickoRun?.id ?? eloRun.id),
      getSeriesRecords(),
      getEloTimelines(eloRun.id, [team.id]),
      getEraSpans(),
      getEventMarkers(),
      getTeamPlacements(team.id),
      getTeamSpans(team.id),
      getTeamStints(team.id),
      getTeamModeSplits(team.id),
      getTeamModeStrength(team.id),
      getTeamH2H(team.id, 12),
      metricRun ? getTeamMetrics(metricRun.id, team.id) : Promise.resolve([]),
      metricRun ? getMetricCatalog(metricRun.id) : Promise.resolve(null),
      getModeCatalog(),
    ]);
  // The same team under Glicko-2, so the trajectory chart can shade its RD.
  const glickoTimelines = glickoRun
    ? await getEloTimelines(glickoRun.id, [team.id])
    : [];
  const styleRows = buildStyleRows(teamMetrics, metricCatalog);
  // mode_ratings keys modes by slug; the win-rate chart above reads game_modes
  // .name. Same labels on both, or the two charts look like different modes.
  const strength = modeStrength && {
    ...modeStrength,
    rows: modeStrength.rows.map((r) => ({
      ...r,
      mode: modeLabel(modeCatalog, r.mode),
    })),
  };

  const standing = standings.find((s) => s.teamId === team.id);
  const rank = standings.findIndex((s) => s.teamId === team.id) + 1;
  const record = records.get(team.id);
  const wins = placements.filter((p) => p.placementMin === 1).length;
  const podiums = placements.filter(
    (p) => p.placementMin !== null && p.placementMin <= 3,
  ).length;

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <p className="eyebrow text-accent">Team · {formatLeagueSpans(spans)}</p>
      <h1 className="mt-1 font-display text-5xl font-bold uppercase tracking-tight">
        {team.name}
      </h1>
      <p className="mt-2 text-sm text-ink-secondary">
        {team.region && <>{team.region} · </>}
        {record && (
          <>
            {record.wins}–{record.losses} in decided series
          </>
        )}
        {placements.length > 0 && (
          <>
            {record && " · "}
            {placements.length} events · {wins} event{" "}
            {wins === 1 ? "win" : "wins"} · {podiums} top-3 finishes
          </>
        )}
      </p>

      <section className="mt-8 grid grid-cols-2 gap-6 border-y border-hairline py-4 sm:grid-cols-4">
        <div>
          <div className="font-mono text-2xl tabular-nums">
            {standing ? standing.finalElo.toFixed(0) : "—"}
          </div>
          <div className="mt-0.5 text-xs text-ink-muted">
            Final Elo{rank > 0 && <> (#{rank} of {standings.length})</>}
          </div>
        </div>
        <div>
          <div className="font-mono text-2xl tabular-nums">
            {standing ? standing.peakElo.toFixed(0) : "—"}
          </div>
          <div className="mt-0.5 text-xs text-ink-muted">Peak Elo</div>
        </div>
        <div>
          <div className="font-mono text-2xl tabular-nums">
            {standing?.glicko != null ? (
              <>
                {standing.glicko.toFixed(0)}
                <span className="text-base text-ink-muted">
                  {" "}
                  ±{standing.glickoRd?.toFixed(0)}
                </span>
              </>
            ) : (
              "—"
            )}
          </div>
          <div className="mt-0.5 text-xs text-ink-muted">Glicko-2 ± RD</div>
        </div>
        <div>
          <div className="font-mono text-2xl tabular-nums">
            {standing?.nSeries ?? "—"}
          </div>
          <div className="mt-0.5 text-xs text-ink-muted">Rated series</div>
        </div>
      </section>

      <section className="mt-12">
        <h2 className="lower-third">
          Rating trajectory
          <span className="lt-note">after every rated series</span>
        </h2>
        <div className="mt-4">
          <EloExplorer
            timelines={timelines}
            glicko={glickoTimelines}
            eras={eras}
            events={events}
            height={300}
          />
        </div>
      </section>

      {placements.length > 0 && (
        <section className="mt-12">
          <h2 className="lower-third">
            Event placements
            <span className="lt-note">{placements.length} events on record</span>
          </h2>
          <div className="mt-4 border border-hairline bg-surface p-4">
            <PlacementTimeline placements={placements} />
          </div>
        </section>
      )}

      <section className="mt-12 grid grid-cols-1 gap-10 lg:grid-cols-2">
        <div>
          <h2 className="lower-third">
            Map win rate by mode
            <span className="lt-note">whole archive</span>
          </h2>
          <div className="mt-4 border border-hairline bg-surface p-4">
            <ModeSplitBars splits={modeSplits} />
          </div>

          {strength && (
            <>
              <h2 className="lower-third mt-10">
                Strength by mode
                <span className="lt-note">opposition-adjusted</span>
              </h2>
              <div className="mt-4 border border-hairline bg-surface p-4">
                <ModeStrengthBars strength={strength} />
                <ModeStrengthNull strength={strength} />
              </div>
            </>
          )}
        </div>
        <div>
          <h2 className="lower-third">
            Head to head
            <span className="lt-note">most-played opponents</span>
          </h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Opponent</th>
                  <th className="py-2 pr-4 text-right font-normal">W–L</th>
                  <th className="py-2 font-normal">Share</th>
                </tr>
              </thead>
              <tbody>
                {h2h.map((r) => {
                  const total = r.wins + r.losses;
                  const share = total > 0 ? r.wins / total : 0;
                  return (
                    <tr key={r.opponentId} className="border-b border-hairline/60">
                      <td className="py-1.5 pr-4">
                        <Link
                          href={`/teams/${r.opponentSlug}`}
                          className="hover:text-accent hover:underline"
                        >
                          {r.opponent}
                        </Link>
                      </td>
                      <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                        {r.wins}–{r.losses}
                      </td>
                      <td className="py-1.5">
                        <svg width={90} height={10} viewBox="0 0 90 10" role="img" aria-label={`won ${Math.round(share * 100)}% of series`}>
                          <rect x={0} y={3.5} width={90} height={3} fill="var(--baseline)" />
                          <rect x={0} y={3.5} width={Math.max(1.5, share * 90)} height={3} fill="var(--series-1)" />
                          <rect x={44.5} y={1.5} width={1} height={7} fill="var(--ink-muted)" />
                        </svg>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {styleRows.length > 0 && (
        <section className="mt-12">
          <h2 className="lower-third">
            Style
            <span className="lt-note">percentile among qualified teams that season</span>
          </h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Season</th>
                  <th className="py-2 pr-4 font-normal">Measure</th>
                  <th className="py-2 pr-4 font-normal">Mode</th>
                  <th className="py-2 pr-4 text-right font-normal">Value</th>
                  <th className="py-2 pr-4 font-normal">Percentile</th>
                  <th className="py-2 text-right font-normal">Maps</th>
                </tr>
              </thead>
              <tbody>
                {styleRows.map((r) => (
                  <tr key={r.key} className="border-b border-hairline/60">
                    <td className="py-1.5 pr-4 text-ink-secondary">{r.year}</td>
                    <td className="py-1.5 pr-4" title={r.note ?? undefined}>
                      {r.label}
                    </td>
                    <td className="py-1.5 pr-4 text-ink-secondary">
                      {modeLabel(modeCatalog, r.mode, "All")}
                    </td>
                    <td className="py-1.5 pr-4 text-right font-mono tabular-nums">
                      {r.value}
                    </td>
                    <td className="py-1.5 pr-4">
                      {r.pctl !== null ? <PctlBar pctl={r.pctl} /> : "—"}
                    </td>
                    <td className="py-1.5 text-right font-mono tabular-nums text-ink-secondary">
                      {r.maps}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-xs text-ink-muted">
            Hill duty concentration and opening concentration describe how a roster
            spreads its work, not how well it plays: a low value means the load was
            shared. Percentiles are flipped where a lower raw number is the better
            result, so a fuller bar always reads the same way.
          </p>
        </section>
      )}

      <section className="mt-12">
        <h2 className="lower-third">
          Roster history
          <span className="lt-note">{stintNote(stints)}</span>
        </h2>
        <div className="mt-4 border border-hairline bg-surface p-4">
          <StintTimeline
            stints={stints}
            rangeEnd={eloRun.dataThrough ?? undefined}
          />
        </div>
      </section>
    </main>
  );
}
