import Link from "next/link";
import { notFound } from "next/navigation";
import { EloExplorer } from "@/components/charts/EloExplorer";
import { ModeSplitBars } from "@/components/charts/ModeSplitBars";
import { PlacementTimeline } from "@/components/charts/PlacementTimeline";
import { StintTimeline } from "@/components/charts/StintTimeline";
import {
  getEloTimelines,
  getEraSpans,
  getEventMarkers,
  getSeriesRecords,
  getTeamBySlug,
  getTeamH2H,
  getTeamModeSplits,
  getTeamPlacements,
  getTeamStints,
  getTeamStandings,
  latestRun,
} from "@/lib/analytics";

export const dynamic = "force-dynamic";

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

  const [eloRun, glickoRun] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
  ]);
  if (!eloRun) notFound();

  const [
    standings,
    records,
    timelines,
    eras,
    events,
    placements,
    stints,
    modeSplits,
    h2h,
  ] = await Promise.all([
      getTeamStandings(eloRun.id, glickoRun?.id ?? eloRun.id),
      getSeriesRecords(),
      getEloTimelines(eloRun.id, [team.id]),
      getEraSpans(),
      getEventMarkers(),
      getTeamPlacements(team.id),
      getTeamStints(team.id),
      getTeamModeSplits(team.id),
      getTeamH2H(team.id, 12),
    ]);

  const standing = standings.find((s) => s.teamId === team.id);
  const rank = standings.findIndex((s) => s.teamId === team.id) + 1;
  const record = records.get(team.id);
  const wins = placements.filter((p) => p.placementMin === 1).length;
  const podiums = placements.filter(
    (p) => p.placementMin !== null && p.placementMin <= 3,
  ).length;

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <p className="eyebrow text-accent">Team · CWL 2017–2019 archive</p>
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
          <span className="lt-note">Elo after every rated series</span>
        </h2>
        <div className="mt-4">
          <EloExplorer
            timelines={timelines}
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

      <section className="mt-12">
        <h2 className="lower-third">
          Roster history
          <span className="lt-note">from Liquipedia roster records</span>
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
