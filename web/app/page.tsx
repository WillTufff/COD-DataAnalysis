import Link from "next/link";
import { EloHistory } from "@/components/charts/EloHistory";
import {
  getArchiveStats,
  getCareerRankLeaderboard,
  getEraSpans,
  getFeed,
  getPlayerLeaderboard,
  getSeasonChampions,
  getTeamHistories,
  queryMetric,
  latestCareerRankRun,
  latestRatingRun,
  latestRun,
  playerSlug,
} from "@/lib/analytics";
import { CareerBoard } from "./_home/CareerBoard";
import { FindingCards } from "./_home/FindingCards";
import {
  getCurrentSeason,
  getSearchIndex,
  getSeasonEvents,
  getSeasonLeaders,
  getSeasonRace,
  getSeasonRatings,
  getSiteCounts,
  getTeamModes,
} from "./_home/queries";
import { Search } from "./_home/Search";
import { SeasonLeaders } from "./_home/SeasonLeaders";
import { SeasonRace } from "./_home/SeasonRace";
import { TeamModes } from "./_home/TeamModes";

export const revalidate = 3600;

function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="lower-third">{children}</h2>;
}

function Part({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 border-b border-hairline pb-4">
      <div>
        <h2 className="font-display text-4xl font-bold uppercase leading-none sm:text-5xl">{title}</h2>
      </div>
      {children}
    </div>
  );
}

export default async function Home() {
  const [eloRun, glickoRun, eraRun, insightsRun, careerRun, ratingRun, season] = await Promise.all([
    latestRun("elo"),
    latestRun("glicko2"),
    latestRun("era_adjust"),
    latestRun("insights"),
    latestCareerRankRun(),
    latestRatingRun(),
    getCurrentSeason(),
  ]);
  if (!eloRun || !eraRun) return <main className="p-10">No model runs.</main>;

  const kinds = ["profile_extreme", "intangible_outlier", "what_wins", "h2h_edge"];
  const [
    stats,
    counts,
    events,
    race,
    leaders,
    teamModes,
    ratings,
    search,
    career,
    seasons,
    eras,
    eloHistory,
    glickoHistory,
    ringTimes,
    findings,
  ] = await Promise.all([
    getArchiveStats(),
    getSiteCounts(insightsRun?.id ?? null),
    getSeasonEvents(season.id),
    getSeasonRace(eloRun.id, season.id),
    getSeasonLeaders(season.id),
    getTeamModes(season.id),
    ratingRun ? getSeasonRatings(ratingRun.id, season.id) : Promise.resolve(new Map<string, number>()),
    getSearchIndex(),
    careerRun ? getCareerRankLeaderboard(careerRun.id, 12) : Promise.resolve([]),
    getPlayerLeaderboard(eraRun.id),
    getEraSpans(),
    getTeamHistories(eloRun.id),
    glickoRun ? getTeamHistories(glickoRun.id) : Promise.resolve([]),
    getSeasonChampions(),
    insightsRun
      ? Promise.all(kinds.map((k) => getFeed(insightsRun.id, 40, k, 0, false))).then((x) =>
          x.flatMap((list) => {
            if (list[0]?.detail.year === undefined) return list.slice(0, 1);
            const year = Math.max(...list.map((f) => Number(f.detail.year)));
            const latest = list.filter((f) => Number(f.detail.year) === year);
            return list[0].kind === "what_wins" ? latest : latest.slice(0, 2);
          }),
        )
      : Promise.resolve([]),
  ]);

  const hero = findings.find((f) => f.kind === "profile_extreme");
  const cohort = hero
    ? await queryMetric(
        Number(hero.detail.metric_run_id),
        {
          metric: String(hero.detail.metric),
          year: Number(hero.detail.year),
          modeSlug: String(hero.detail.mode).toLowerCase().replace(/&/g, "and").replace(/\s+/g, "-"),
          qualifiedOnly: true,
          dir: "desc",
        },
        { offset: 0, limit: 500 },
      )
    : [];

  const fmt = (n: number) => n.toLocaleString("en-US");
  for (const l of leaders) l.rating = ratings.get(l.handle) ?? null;
  const champ = events.find((e) => /championship/i.test(e.name))?.winner ?? null;
  const topSeasons = [...seasons].sort((a, b) => (b.kdZ ?? 0) - (a.kdZ ?? 0)).slice(0, 8);
  const zMax = topSeasons[0]?.kdZ ?? 3;

  const sections: [string, string, string, string][] = [
    ["/teams", "Teams", fmt(counts.teams), "records, rosters and head-to-head"],
    ["/players", "Players", fmt(stats.players), "career and season ratings"],
    ["/stats", "Stats", fmt(stats.statRows), "box-score lines, one per player per map"],
    ["/maps", "Maps", fmt(stats.maps), "maps played, win rates by map and mode"],
    ["/rounds", "Rounds", fmt(counts.rounds), "Search & Destroy rounds, round by round"],
    ["/meta", "Loadouts", fmt(counts.weapons), "weapons, with usage by event"],
  ];

  return (
    <main className="mx-auto max-w-6xl px-4 py-10 sm:px-6 sm:py-12">
      {/* What this site is */}
      <header className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_360px] lg:items-end">
        <div>
          <h1 className="font-display text-4xl font-bold uppercase leading-[0.95] tracking-tight sm:text-6xl">
            Competitive Call of Duty
            <br />
            <span className="text-ink-muted">{stats.span}</span>
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-ink-secondary">
            Results, box scores and ratings from pro Call of Duty: {fmt(stats.seriesCount)} series, {fmt(counts.events)} events and{" "}
            {fmt(stats.players)} players.
          </p>
        </div>
        <Search index={search} />
      </header>

      <section className="mt-10">
        <H2>Team ratings</H2>
        <div className="mt-4">
          <EloHistory elo={eloHistory} glicko={glickoHistory} eras={eras} champions={ringTimes} />
        </div>
      </section>

      <nav className="mt-10 grid grid-cols-2 gap-px border border-hairline bg-hairline md:grid-cols-3 lg:grid-cols-6">
        {sections.map(([href, label, n, sub]) => (
          <Link key={href} href={href} className="group bg-background p-4 transition-colors hover:bg-surface">
            <span className="eyebrow text-[10px] text-ink-muted group-hover:text-accent">{label} →</span>
            <span className="mt-1 block font-display text-3xl font-bold tabular-nums">{n}</span>
            <span className="block text-xs leading-snug text-ink-muted">{sub}</span>
          </Link>
        ))}
      </nav>

      {/* Current season */}
      <section className="mt-20">
        <Part title={`${season.year} · ${season.titleName}`}>
          {champ && (
            <p className="text-sm text-ink-secondary">
              <span className="text-accent">{champ}</span> won the Championship
              {race[0] && (
                <>
                  {" "}
                  · <span className="text-ink">{race[0].team}</span> had the best Elo
                </>
              )}
            </p>
          )}
        </Part>

        <div className="mt-8">
          <H2>The title race</H2>
          <div className="mt-4">
            <SeasonRace teams={race} events={events} />
          </div>
        </div>

        <div className="mt-12 grid grid-cols-1 gap-10 lg:grid-cols-2">
          <div>
            <H2>Player leaders</H2>
            <div className="mt-4">
              <SeasonLeaders rows={leaders} />
            </div>
          </div>
          <div>
            <H2>Teams by mode</H2>
            <div className="mt-4">
              <TeamModes modes={teamModes.modes} rows={teamModes.rows} />
            </div>
          </div>
        </div>
      </section>

      {/* All time */}
      <section className="mt-20">
        <Part title="All time" />

        {career.length > 0 && (
          <div className="mt-8">
            <H2>Career ranking</H2>
            <div className="mt-4">
              <CareerBoard
                rows={career.map((r) => ({
                  handle: r.handle,
                  slug: playerSlug(r.handle),
                  total: r.total,
                  nSeasons: r.nSeasons,
                  peakYear: r.peakSeasonYear,
                  parts: r.careerComponents,
                }))}
              />
            </div>
            <p className="mt-3 text-sm">
              <Link href="/players" className="text-accent hover:text-ink">
                Full ranking →
              </Link>
            </p>
          </div>
        )}

        <div className="mt-14">
          <H2>Best single seasons</H2>
          <p className="mt-3 max-w-2xl text-sm text-ink-secondary">
            K/D in standard deviations above that season&rsquo;s mean (right column). Raw K/D in
            grey.
          </p>
          <ol className="mt-4 grid grid-cols-1 gap-x-10 gap-y-1 md:grid-cols-2">
            {topSeasons.map((r, i) => (
              <li
                key={`${r.playerId}-${r.year}`}
                className="flex items-center gap-3 border-b border-hairline/60 py-2 text-sm"
              >
                <span className="w-4 font-mono text-[11px] text-ink-muted">{i + 1}</span>
                <Link href={`/players/${r.slug}`} className="w-24 font-medium hover:text-accent">
                  {r.handle}
                </Link>
                <span className="w-20 font-mono text-[11px] text-ink-muted">
                  {r.year} {r.title}
                </span>
                <span className="relative h-1.5 flex-1 bg-surface">
                  <span
                    className="absolute inset-y-0 left-0 bg-accent-dim"
                    style={{ width: `${((r.kdZ ?? 0) / zMax) * 100}%` }}
                  />
                </span>
                <span className="w-10 text-right font-mono text-xs tabular-nums text-ink-muted">
                  {r.kdRaw?.toFixed(2)}
                </span>
                <span className="w-12 text-right font-mono tabular-nums">+{r.kdZ?.toFixed(2)}</span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {findings.length > 0 && (
        <section className="mt-20">
          <Part title="Findings">
            <Link href="/findings" className="text-sm text-accent hover:text-ink">
              All {fmt(counts.findings)} findings →
            </Link>
          </Part>
          <div className="mt-8">
            <FindingCards items={findings} cohort={cohort} />
          </div>
        </section>
      )}
    </main>
  );
}
