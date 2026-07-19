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
  getMetricCatalog,
  getPlayerAdjusted,
  getPlayerBySlug,
  getPlayerInsights,
  getPlayerMetrics,
  getPlayerStints,
  latestRun,
  teamSlug,
  type MetricCatalog,
  type PlayerMetricValue,
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

const MODE_LABELS: Record<string, string> = {
  hardpoint: "Hardpoint",
  "search-and-destroy": "Search & Destroy",
  control: "Control",
  "capture-the-flag": "Capture the Flag",
  uplink: "Uplink",
};

type MetricCard = {
  key: string;
  heading: string;
  sample: string;
  qualified: boolean;
  stats: ProfileStat[];
};

function formatMetric(value: number, unit: string): string {
  if (unit.startsWith("share")) return `${(value * 100).toFixed(1)}%`;
  if (Math.abs(value) >= 100) return value.toFixed(0);
  if (Math.abs(value) >= 10) return value.toFixed(2);
  return value.toFixed(3);
}

// One card per (season, mode) the player appeared in, carrying that slice's
// gold-tier metrics as percentiles. Metrics where low is good are flipped so a
// full track always reads as "good".
function buildMetricCards(
  values: PlayerMetricValue[],
  catalog: MetricCatalog | null,
): MetricCard[] {
  if (!catalog) return [];
  // Phase B (kill-feed) metrics get their own dedicated cards below, so keep
  // them out of the general mode profiles.
  const gold = new Map(
    catalog.metrics
      .filter((m) => m.tier.startsWith("gold") && !FEED_CATEGORIES.has(m.category))
      .map((m) => [m.key, m]),
  );
  const cards = new Map<string, MetricCard>();
  for (const v of values) {
    const entry = gold.get(v.metric);
    if (!entry || v.pctl === null) continue;
    const key = `${v.year}-${v.mode ?? "all"}`;
    let card = cards.get(key);
    if (!card) {
      card = {
        key,
        heading: `${v.year} ${v.title} · ${v.mode ? (MODE_LABELS[v.mode] ?? v.mode) : "All modes"}`,
        sample: "",
        qualified: v.qualified,
        stats: [],
      };
      cards.set(key, card);
    }
    card.qualified = card.qualified || v.qualified;
    // Label the sample in maps wherever a maps-based metric exists, so cards
    // stay comparable regardless of which metric was read first.
    if (entry.denom_kind === "maps") {
      card.sample = `${Math.round(v.denom)} maps`;
    } else if (card.sample === "") {
      card.sample = `${Math.round(v.denom)} ${entry.denom_kind}`;
    }
    card.stats.push({
      label: entry.label,
      pctl: entry.higher_is_better ? v.pctl : 1 - v.pctl,
      value: formatMetric(v.value, entry.unit),
    });
  }
  return [...cards.values()]
    .filter((c) => c.stats.length >= 2)
    .map((c) => ({ ...c, stats: c.stats.sort((a, b) => a.label.localeCompare(b.label)) }))
    .sort((a, b) => a.heading.localeCompare(b.heading));
}

// ---------- Phase B (kill-feed) cards ----------

const FEED_CATEGORIES = new Set(["trades", "clutch", "advantage"]);

// Trades read the all-modes slice; clutch and advantage are Search & Destroy.
const TRADE_KEYS = ["untraded_death_rate", "trade_kills_p10", "kill_answered_rate"];
const ADV_KEYS = ["snd_adv_conversion", "snd_adv_rounds_lost", "snd_disadv_steal_rate"];
const CLUTCH_N_KEYS = [
  "clutch_1v1_win_rate",
  "clutch_1v2_win_rate",
  "clutch_1v3_win_rate",
  "clutch_1v4_win_rate",
];

type ClutchLine = { n: number; wins: number; losses: number };

type FeedCard = {
  stats: ProfileStat[];
  qualified: boolean;
  sample: string;
} | null;

type FeedSeason = {
  key: string;
  year: number;
  title: string;
  trades: FeedCard;
  advantage: FeedCard;
  clutch: {
    lines: ClutchLine[];
    rate: ProfileStat | null;
    qualified: boolean;
    sample: string;
  } | null;
};

/** A percentile card from a fixed metric list within one (year, mode) slice. */
function feedCard(
  byKey: Map<string, PlayerMetricValue>,
  catalog: MetricCatalog,
  keys: string[],
): FeedCard {
  const entries = new Map(catalog.metrics.map((m) => [m.key, m]));
  const stats: ProfileStat[] = [];
  let qualified = false;
  let denom = 0;
  let denomKind = "";
  for (const key of keys) {
    const v = byKey.get(key);
    const entry = entries.get(key);
    if (!v || !entry || v.pctl === null) continue;
    qualified = qualified || v.qualified;
    denom = Math.max(denom, v.denom);
    denomKind = entry.denom_kind;
    stats.push({
      label: entry.label,
      pctl: entry.higher_is_better ? v.pctl : 1 - v.pctl,
      value: formatMetric(v.value, entry.unit),
    });
  }
  if (stats.length === 0) return null;
  return { stats, qualified, sample: `${Math.round(denom)} ${denomKind}` };
}

function buildFeedSeasons(
  values: PlayerMetricValue[],
  catalog: MetricCatalog | null,
): FeedSeason[] {
  if (!catalog) return [];
  // Index by (year, mode-slug) so each slice's metrics can be looked up by key.
  const bySlice = new Map<string, Map<string, PlayerMetricValue>>();
  const titleOf = new Map<number, string>();
  for (const v of values) {
    if (!FEED_CATEGORIES.has(catalog.metrics.find((m) => m.key === v.metric)?.category ?? "")) {
      continue;
    }
    titleOf.set(v.year, v.title);
    const sliceKey = `${v.year}:${v.mode ?? "all"}`;
    const slice = bySlice.get(sliceKey) ?? new Map<string, PlayerMetricValue>();
    slice.set(v.metric, v);
    bySlice.set(sliceKey, slice);
  }

  const years = [...titleOf.keys()].sort((a, b) => a - b);
  const seasons: FeedSeason[] = [];
  for (const year of years) {
    const allSlice = bySlice.get(`${year}:all`) ?? new Map();
    const sndSlice = bySlice.get(`${year}:search-and-destroy`) ?? new Map();

    const clutchLines: ClutchLine[] = [];
    for (let n = 1; n <= 4; n++) {
      const v = sndSlice.get(CLUTCH_N_KEYS[n - 1]);
      if (!v) continue;
      const wins = Math.round(v.value * v.denom);
      clutchLines.push({ n, wins, losses: Math.round(v.denom) - wins });
    }
    const rateVal = sndSlice.get("clutch_win_rate");
    const clutchCard =
      clutchLines.length > 0
        ? {
            lines: clutchLines,
            rate:
              rateVal && rateVal.pctl !== null
                ? {
                    label: "Clutch win rate",
                    pctl: rateVal.pctl,
                    value: `${(rateVal.value * 100).toFixed(1)}%`,
                  }
                : null,
            qualified: rateVal?.qualified ?? false,
            sample: `${clutchLines.reduce((s, c) => s + c.wins + c.losses, 0)} clutches`,
          }
        : null;

    const trades = feedCard(allSlice, catalog, TRADE_KEYS);
    const advantage = feedCard(sndSlice, catalog, ADV_KEYS);
    if (!trades && !advantage && !clutchCard) continue;
    seasons.push({
      key: String(year),
      year,
      title: titleOf.get(year) ?? "",
      trades,
      advantage,
      clutch: clutchCard,
    });
  }
  return seasons;
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

  const [eraRun, insightsRun, metricRun] = await Promise.all([
    latestRun("era_adjust"),
    latestRun("insights"),
    latestRun("metric_layer"),
  ]);
  const [adjusted, stints, playerInsights, metricValues, metricCatalog] =
    await Promise.all([
      eraRun ? getPlayerAdjusted(player.id, eraRun.id) : Promise.resolve([]),
      getPlayerStints(player.id),
      insightsRun ? getPlayerInsights(player.id, insightsRun.id) : Promise.resolve([]),
      metricRun ? getPlayerMetrics(metricRun.id, player.id) : Promise.resolve([]),
      metricRun ? getMetricCatalog(metricRun.id) : Promise.resolve(null),
    ]);
  const metricCards = buildMetricCards(metricValues, metricCatalog);
  const feedSeasons = buildFeedSeasons(metricValues, metricCatalog);

  const allModes = adjusted.filter((a) => a.modeId === null);
  // BO4 (2019) has box scores but no event feed, so the kill-feed cards can
  // never populate for it. Surface that as a reason, not silent absence.
  const bo4Seasons = allModes.filter((a) => a.title === "BO4").map((a) => a.year);
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

      {metricCards.length > 0 && (
        <section className="mt-10">
          <h2 className="lower-third">
            Mode profiles
            <span className="lt-note">percentile within season-and-mode cohort</span>
          </h2>
          <div className="mt-4 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
            {metricCards.map((card) => (
              <div key={card.key}>
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="font-display text-lg font-semibold uppercase">
                    {card.heading}
                  </span>
                  <span className="font-mono text-xs text-ink-muted">
                    {card.qualified ? card.sample : `${card.sample} · below minimum`}
                  </span>
                </div>
                <div className={card.qualified ? "" : "opacity-50"}>
                  <PercentileProfile stats={card.stats} />
                </div>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-ink-muted">
            Gold-tier metrics only, shown as percentile within the qualified players
            of the same season and mode. Seasons below the sample minimum are dimmed.
            The full definitions are in the{" "}
            <Link href="/methodology#metrics" className="underline">
              metric glossary
            </Link>
            .
          </p>
        </section>
      )}

      {(feedSeasons.length > 0 || bo4Seasons.length > 0) && (
        <section className="mt-10">
          <h2 className="lower-third">
            Kill feed
            <span className="lt-note">trades, clutches and man advantage · IW and WWII</span>
          </h2>
          {feedSeasons.map((s) => (
            <div key={s.key} className="mt-5">
              <div className="mb-2 font-display text-lg font-semibold uppercase">
                {s.year} {s.title}
              </div>
              <div className="grid grid-cols-1 gap-x-10 gap-y-6 md:grid-cols-2">
                {s.trades && (
                  <div>
                    <div className="mb-2 flex items-baseline justify-between">
                      <span className="text-sm font-semibold text-ink-secondary">Trades</span>
                      <span className="font-mono text-xs text-ink-muted">
                        {s.trades.qualified ? s.trades.sample : `${s.trades.sample} · below minimum`}
                      </span>
                    </div>
                    <div className={s.trades.qualified ? "" : "opacity-50"}>
                      <PercentileProfile stats={s.trades.stats} />
                    </div>
                  </div>
                )}
                {s.advantage && (
                  <div>
                    <div className="mb-2 flex items-baseline justify-between">
                      <span className="text-sm font-semibold text-ink-secondary">
                        Man advantage · S&amp;D
                      </span>
                      <span className="font-mono text-xs text-ink-muted">
                        {s.advantage.qualified
                          ? s.advantage.sample
                          : `${s.advantage.sample} · below minimum`}
                      </span>
                    </div>
                    <div className={s.advantage.qualified ? "" : "opacity-50"}>
                      <PercentileProfile stats={s.advantage.stats} />
                    </div>
                  </div>
                )}
                {s.clutch && (
                  <div>
                    <div className="mb-2 flex items-baseline justify-between">
                      <span className="text-sm font-semibold text-ink-secondary">
                        Clutch record · S&amp;D
                      </span>
                      <span className="font-mono text-xs text-ink-muted">{s.clutch.sample}</span>
                    </div>
                    <div className={s.clutch.qualified ? "" : "opacity-50"}>
                      {s.clutch.rate && <PercentileProfile stats={[s.clutch.rate]} />}
                      <table className="mt-2 w-full max-w-xs text-left text-sm">
                        <tbody>
                          {s.clutch.lines.map((c) => (
                            <tr key={c.n} className="border-b border-hairline/60">
                              <td className="py-1 font-mono text-xs text-ink-secondary">1v{c.n}</td>
                              <td className="py-1 text-right font-mono text-xs tabular-nums">
                                {c.wins}
                                <span className="text-ink-muted">–{c.losses}</span>
                              </td>
                              <td className="py-1 pl-3 text-right font-mono text-xs tabular-nums text-ink-muted">
                                {c.wins + c.losses > 0
                                  ? `${((c.wins / (c.wins + c.losses)) * 100).toFixed(0)}%`
                                  : "—"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ))}
          {bo4Seasons.length > 0 && (
            <p className="mt-5 text-xs text-ink-muted">
              {bo4Seasons.join(", ")} Black Ops 4 has box scores but no event feed, so
              trade, clutch and man-advantage cards do not apply to those seasons.
            </p>
          )}
          <p className="mt-3 text-xs text-ink-muted">
            Trade, clutch and advantage percentiles are within the qualified players of
            the same season and mode; low-is-better metrics are flipped so a full track
            reads well. Clutch W–L is raw. Seasons below the sample minimum are dimmed.
            Definitions on the{" "}
            <Link href="/methodology#rounds" className="underline">
              methodology
            </Link>{" "}
            page.
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
