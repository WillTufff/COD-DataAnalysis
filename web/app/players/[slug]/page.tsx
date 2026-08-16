import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { CareerArc, type ArcPoint } from "@/components/charts/CareerArc";
import {
  PercentileProfile,
  type ProfileStat,
} from "@/components/charts/PercentileProfile";
import {
  Fingerprint,
  type FingerprintGroup,
  type FingerprintSeason,
} from "@/components/charts/Fingerprint";
import {
  StyleAxes,
  type StyleAxisMeta,
  type StyleSeasonPoint,
} from "@/components/charts/StyleAxes";
import { RoundShareBar } from "@/components/charts/RoundShareBar";
import { RatingIntervals, overlaps } from "@/components/charts/RatingInterval";
import { SkillBlend } from "@/components/charts/SkillBlend";
import { PctlBar } from "@/components/PctlBar";
import { Tabs } from "@/components/Tabs";
import {
  formatLeagueSpans,
  getAllPlayerSlugs,
  getMetricCatalog,
  getPlayerAdjusted,
  getPlayerBySlug,
  getPlayerInsights,
  getPlayerMetrics,
  getPlayerRapm,
  getPlayerRatingSeasons,
  getPlayerCareer,
  getPlayerCareerRank,
  getPlayerSkill,
  getPlayerSpans,
  getPlayerStints,
  getPlayerRole,
  getPlayerStyle,
  getPlayerStyleArtifact,
  getRole,
  getSkillSeasons,
  latestRatingRun,
  latestRun,
  latestCareerRun,
  latestCareerRankRun,
  latestSkillRun,
  teamSlug,
  type MetricCatalog,
  type PlayerMetricValue,
  type PlayerRapm,
  type PlayerRatings,
  type PlayerCareerRow,
  type PlayerCareerRankSummary,
  type PlayerCareerRankSeason,
  type PlayerSkillSeason,
  type PlayerStyle,
  type PlayerStylePoint,
  type RoleModel,
  type RoleSeason,
  type SeasonAdjusted,
  getModeCatalog,
} from "@/lib/analytics";
import { kindLabel } from "@/lib/insightKinds";
import { RATINGS, primacyReason } from "@/lib/primacy";
import { type ModeCatalog, modeLabel, modeRank } from "@/lib/modes";

// The archive is frozen and the models only change on a rerun, so this page is
// prerendered and revalidated on a timer rather than queried per request.
export const revalidate = 3600;

// Every player in the archive is known at build time, so all of them prerender
// rather than each waiting for its first visitor.
export async function generateStaticParams() {
  const slugs = await getAllPlayerSlugs();
  return slugs.map((slug) => ({ slug }));
}

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

// Card order within a season is `game_modes` order, overall first; S&D is
// pulled out of this grid into its own combined panel.

// Stats sort by position here (then label); the first HEADLINE_STATS present on
// a card stay visible, the tail collapses behind the "all n metrics" toggle.
// The same ranking picks the career fingerprint's rows.
const METRIC_PRIORITY = [
  "kills_p10",
  "deaths_p10",
  "plus_minus_p10",
  "kill_share",
  "ekia_p10",
  "hill_time_p10",
  "hill_time_share",
  "snd_kpr",
  "snd_dpr",
  "snd_fb_rate",
  "snd_fd_rate",
  "snd_opening_duel_win",
  "ctf_caps_pm",
  "ctf_returns_pm",
  "ctf_carry_efficiency",
  "uplink_points_pm",
  "uplink_dunk_rate",
  "ctrl_caps_pm",
  "ctrl_fb_net_pr",
  "ctrl_opening_duel_win",
  "damage_p10",
  "blitz_index_p10",
  "clean_kill_rate",
  "time_per_life_s",
];

function metricRank(key: string): number {
  const i = METRIC_PRIORITY.indexOf(key);
  return i === -1 ? METRIC_PRIORITY.length : i;
}

// `kills_p10` and `kills_pm` are one quantity normalized two ways. Per map is
// published everywhere and per 10 minutes only where map time is recorded, so a
// slice inside the archive carries both and would otherwise spend two of its six
// slots saying the same thing. Keys with no twin — `ctf_caps_pm`, `suicides_pm`
// — never match a stem here and are left alone.
const NORMALIZED = /^(.+)_(p10|pm)$/;

function normalizedPair(key: string): { stem: string; form: "p10" | "pm" } | null {
  const m = NORMALIZED.exec(key);
  return m ? { stem: m[1], form: m[2] as "p10" | "pm" } : null;
}

/** The per-map keys to drop because their per-10-minute twin is also present. */
function redundantPerMap(keys: Iterable<string>): Set<string> {
  const timed = new Set<string>();
  const perMap = new Map<string, string>();
  for (const key of keys) {
    const pair = normalizedPair(key);
    if (!pair) continue;
    if (pair.form === "p10") timed.add(pair.stem);
    else perMap.set(pair.stem, key);
  }
  const drop = new Set<string>();
  for (const [stem, key] of perMap) if (timed.has(stem)) drop.add(key);
  return drop;
}

const HEADLINE_STATS = 6;

type CardStat = ProfileStat & { key: string };

type MetricCard = {
  key: string;
  year: number;
  mode: string; // mode slug, or "all"
  heading: string;
  sample: string;
  qualified: boolean;
  stats: CardStat[];
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
  modes: ModeCatalog,
): MetricCard[] {
  if (!catalog) return [];
  // Phase B (kill-feed) metrics get their own dedicated cards below, so keep
  // them out of the general mode profiles.
  const gold = new Map(
    catalog.metrics
      .filter(
        (m) =>
          m.tier.startsWith("gold") &&
          !FEED_CATEGORIES.has(m.category) &&
          !ROUND_CARD_KEYS.has(m.key),
      )
      .map((m) => [m.key, m]),
  );
  const cards = new Map<string, MetricCard>();
  for (const v of values) {
    const entry = gold.get(v.metric);
    if (!entry || v.pctl === null) continue;
    const mode = v.mode ?? "all";
    const key = `${v.year}-${mode}`;
    let card = cards.get(key);
    if (!card) {
      card = {
        key,
        year: v.year,
        mode,
        heading: modeLabel(modes, v.mode),
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
      key: v.metric,
      label: entry.label,
      pctl: entry.higher_is_better ? v.pctl : 1 - v.pctl,
      value: formatMetric(v.value, entry.unit),
    });
  }
  return [...cards.values()]
    .map((c) => {
      const drop = redundantPerMap(c.stats.map((s) => s.key));
      return { ...c, stats: c.stats.filter((s) => !drop.has(s.key)) };
    })
    .filter((c) => c.stats.length >= 2)
    .map((c) => ({
      ...c,
      stats: c.stats.sort(
        (a, b) =>
          metricRank(a.key) - metricRank(b.key) || a.label.localeCompare(b.label),
      ),
    }));
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

// ---------- Round profile and streak cards ----------

type Slices = {
  years: number[];
  titleOf: Map<number, string>;
  bySlice: Map<string, Map<string, PlayerMetricValue>>;
};

/** Index every metric by (year, mode) so a card can look up its keys directly. */
function sliceMetrics(values: PlayerMetricValue[]): Slices {
  const bySlice = new Map<string, Map<string, PlayerMetricValue>>();
  const titleOf = new Map<number, string>();
  for (const v of values) {
    titleOf.set(v.year, v.title);
    const key = `${v.year}:${v.mode ?? "all"}`;
    const slice = bySlice.get(key) ?? new Map<string, PlayerMetricValue>();
    slice.set(v.metric, v);
    bySlice.set(key, slice);
  }
  return { years: [...titleOf.keys()].sort((a, b) => a - b), titleOf, bySlice };
}

const ROUND_SHARE_KEYS = [
  "snd_rounds_0k_share",
  "snd_rounds_1k_share",
  "snd_rounds_2k_share",
  "snd_rounds_3k_share",
  "snd_rounds_4k_share",
];
const ROUND_PROFILE_KEYS = [
  "snd_fb_net_pr",
  "snd_opening_involvement",
  "snd_survival_rate",
  "snd_zero_kill_round_rate",
];
// Raw career-style counts, shown as numbers rather than rates.
const ROUND_COUNT_KEYS = ["snd_ace_total", "sneak_defuses_total"];

// Everything the "Round by round" card renders, kept out of the S&D mode
// profile so the same number never appears twice in one section.
const ROUND_CARD_KEYS = new Set([
  ...ROUND_SHARE_KEYS,
  ...ROUND_PROFILE_KEYS,
  ...ROUND_COUNT_KEYS,
]);

const STREAK_KEYS = [
  "streak4_pm",
  "streak5_pm",
  "streak6_pm",
  "streak7_pm",
  "streak8plus_pm",
];

type CountStat = { label: string; value: string };

type RoundProfile = {
  key: string;
  year: number;
  title: string;
  shares: { label: string; share: number }[];
  profile: FeedCard;
  counts: CountStat[];
};

function buildRoundProfiles(
  values: PlayerMetricValue[],
  catalog: MetricCatalog | null,
): RoundProfile[] {
  if (!catalog) return [];
  const entries = new Map(catalog.metrics.map((m) => [m.key, m]));
  const { years, titleOf, bySlice } = sliceMetrics(values);
  const out: RoundProfile[] = [];
  for (const year of years) {
    const snd = bySlice.get(`${year}:search-and-destroy`);
    if (!snd) continue;
    const shares = ROUND_SHARE_KEYS.map((k, i) => ({
      label: `${i}k`,
      share: snd.get(k)?.value ?? 0,
    }));
    const hasShares = ROUND_SHARE_KEYS.some((k) => snd.has(k));
    const profile = feedCard(snd, catalog, ROUND_PROFILE_KEYS);
    const counts = ROUND_COUNT_KEYS.flatMap((k) => {
      const v = snd.get(k);
      const entry = entries.get(k);
      if (!v || !entry || v.value <= 0) return [];
      return [{ label: entry.label, value: String(Math.round(v.value)) }];
    });
    if (!hasShares && !profile && counts.length === 0) continue;
    out.push({
      key: String(year),
      year,
      title: titleOf.get(year) ?? "",
      shares: hasShares ? shares : [],
      profile,
      counts,
    });
  }
  return out;
}

type StreakRow = { year: number; streaks: { label: string; value: number }[] };

// Streak counts render as a footer line on the all-modes card; blitz index and
// deep-streak rate already live there as regular metrics.
function buildStreakRows(values: PlayerMetricValue[]): StreakRow[] {
  const { years, bySlice } = sliceMetrics(values);
  const out: StreakRow[] = [];
  for (const year of years) {
    const all = bySlice.get(`${year}:all`);
    if (!all) continue;
    const streaks = STREAK_KEYS.flatMap((k, i) => {
      const v = all.get(k);
      if (!v) return [];
      return [{ label: i === 4 ? "8+" : String(i + 4), value: v.value }];
    });
    if (streaks.length === 0) continue;
    out.push({ year, streaks });
  }
  return out;
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

// ---------- Career fingerprint ----------

const FINGERPRINT_ROWS = 6;
// A mode the player only touched in one season has no drift to show, so it
// earns fewer rows — enough to characterise it, not a column of dashes.
const FINGERPRINT_ROWS_SINGLE = 3;

// Pinned to the top of their mode's group, so each block leads with what makes
// that mode distinct rather than repeating the same slaying line six times.
const MODE_SIGNATURE: Record<string, string[]> = {
  hardpoint: ["hill_time_p10", "hill_time_share"],
  "search-and-destroy": [
    "snd_kpr",
    "snd_fb_rate",
    "snd_fd_rate",
    "snd_opening_duel_win",
  ],
  control: ["ctrl_caps_pm", "ctrl_fb_net_pr", "ctrl_opening_duel_win"],
  "capture-the-flag": ["ctf_caps_pm", "ctf_returns_pm", "ctf_carry_efficiency"],
  uplink: ["uplink_points_pm", "uplink_dunk_rate"],
};

type StyleView = {
  axes: StyleAxisMeta[];
  era: string;
  points: StyleSeasonPoint[];
  cohortN: number;
  bestSilhouette: number;
  nullLo: number;
  nullHi: number;
};

// The style section only renders when the model actually refused a taxonomy and
// the player is in the fitted cohort. If a future archive does produce clusters
// that beat the null, this returns null rather than silently drawing a
// continuum over a real partition.
function buildStyleView(
  artifact: PlayerStyle | null,
  points: PlayerStylePoint[],
): StyleView | null {
  if (!artifact || points.length === 0) return null;
  // Style is fitted per era and the axes are not comparable across the seam, so
  // a career that spans it is drawn on the axes of its most recent era.
  const eras = artifact.bases
    .filter((b) => artifact.published_bases.includes(b.basis))
    .map((b) => ({ basis: b, points: points.filter((p) => b.years.includes(p.year)) }))
    .filter((e) => e.points.length > 0)
    .sort((a, b) => Math.max(...b.basis.years) - Math.max(...a.basis.years));
  const latest = eras[0];
  if (!latest) return null;
  const published = latest.basis;
  if (published.taxonomy) return null;
  const scored = published.clustering.filter(
    (c) => c.silhouette !== null && c.silhouette_null !== null,
  );
  if (scored.length === 0) return null;
  const best = scored.reduce((a, b) =>
    (b.silhouette ?? 0) > (a.silhouette ?? 0) ? b : a,
  );
  return {
    axes: published.axes,
    era: published.era,
    points: latest.points.map((p) => ({
      year: p.year,
      title: p.title,
      axis: p.axis,
      pctl: p.pctl,
    })),
    cohortN: published.n,
    bestSilhouette: best.silhouette ?? 0,
    nullLo: best.silhouette_null?.lo ?? 0,
    nullHi: best.silhouette_null?.hi ?? 0,
  };
}

type FingerprintData = {
  seasons: FingerprintSeason[];
  groups: FingerprintGroup[];
};

// Headline metrics (same priority ranking as the cards) × seasons, one group
// per mode, cells as within-cohort percentiles.
function buildFingerprint(
  values: PlayerMetricValue[],
  catalog: MetricCatalog | null,
  modes: ModeCatalog,
): FingerprintData | null {
  if (!catalog) return null;
  const entries = new Map(catalog.metrics.map((m) => [m.key, m]));
  const { years, titleOf, bySlice } = sliceMetrics(values);
  if (years.length === 0) return null;
  const groups: FingerprintGroup[] = [];
  for (const mode of ["all", ...modes.order]) {
    // How many seasons each candidate metric covers, so a row that would be
    // mostly dashes doesn't take a slot from one that shows the whole career.
    const coverage = new Map<string, number>();
    for (const year of years) {
      for (const [k, v] of bySlice.get(`${year}:${mode}`) ?? []) {
        const entry = entries.get(k);
        if (!entry || !entry.tier.startsWith("gold") || v.pctl === null) continue;
        if (FEED_CATEGORIES.has(entry.category) || ROUND_CARD_KEYS.has(k)) continue;
        coverage.set(k, (coverage.get(k) ?? 0) + 1);
      }
    }
    // Across a career the twin to keep is the one that reaches more seasons: a
    // career inside the archive keeps per 10 minutes, one that crosses into the
    // CDL years keeps per map, and a tie keeps per 10 minutes as the finer read.
    for (const [stem, perMapKey] of [...coverage.keys()]
      .map((k) => [normalizedPair(k), k] as const)
      .filter(([p]) => p?.form === "pm")
      .map(([p, k]) => [p!.stem, k] as const)) {
      const timedKey = `${stem}_p10`;
      const timed = coverage.get(timedKey);
      if (timed === undefined) continue;
      coverage.delete(timed >= (coverage.get(perMapKey) ?? 0) ? perMapKey : timedKey);
    }
    const span = Math.max(0, ...coverage.values());
    const signature = MODE_SIGNATURE[mode] ?? [];
    const rankIn = (k: string) => {
      const i = signature.indexOf(k);
      return i === -1 ? signature.length + metricRank(k) : i;
    };
    const keys = [...coverage.keys()]
      .sort(
        (a, b) =>
          // A single-season row is only demoted when the mode itself spans more.
          Number(coverage.get(a) === 1 && span > 1) -
            Number(coverage.get(b) === 1 && span > 1) ||
          rankIn(a) - rankIn(b) ||
          a.localeCompare(b),
      )
      .slice(0, span > 1 ? FINGERPRINT_ROWS : FINGERPRINT_ROWS_SINGLE);
    if (keys.length === 0) continue;
    groups.push({
      label: modeLabel(modes, mode),
      rows: keys.map((k) => {
        const entry = entries.get(k)!;
        return {
          label: entry.label,
          cells: years.map((year) => {
            const v = bySlice.get(`${year}:${mode}`)?.get(k);
            if (!v || v.pctl === null) return null;
            return {
              pctl: entry.higher_is_better ? v.pctl : 1 - v.pctl,
              value: formatMetric(v.value, entry.unit),
            };
          }),
        };
      }),
    });
  }
  if (groups.length === 0) return null;
  return {
    seasons: years.map((y) => ({ year: y, title: titleOf.get(y) ?? "" })),
    groups,
  };
}

// ---------- Per-season view assembly ----------

// Everything the page knows about one season, gathered from the per-category
// builders so a single tab can render the whole year.
type SeasonView = {
  year: number;
  title: string;
  overall: SeasonAdjusted | undefined;
  byMode: SeasonAdjusted[];
  modeCards: MetricCard[];
  sndCard: MetricCard | undefined;
  round: RoundProfile | undefined;
  streaks: StreakRow | undefined;
  feed: FeedSeason | undefined;
};

function buildSeasonViews(
  allModes: SeasonAdjusted[],
  byMode: SeasonAdjusted[],
  metricCards: MetricCard[],
  roundProfiles: RoundProfile[],
  streakRows: StreakRow[],
  feedSeasons: FeedSeason[],
  modes: ModeCatalog,
): SeasonView[] {
  const titleOf = new Map<number, string>();
  for (const a of allModes) titleOf.set(a.year, a.title);
  for (const c of metricCards) if (!titleOf.has(c.year)) titleOf.set(c.year, "");
  const years = [...titleOf.keys()].sort((a, b) => a - b);
  return years.map((year) => {
    const cards = metricCards.filter((c) => c.year === year);
    return {
      year,
      title: titleOf.get(year) ?? "",
      overall: allModes.find((a) => a.year === year),
      byMode: byMode.filter((a) => a.year === year),
      modeCards: cards
        .filter((c) => c.mode !== "search-and-destroy")
        .sort((a, b) => modeRank(modes, a.mode) - modeRank(modes, b.mode)),
      sndCard: cards.find((c) => c.mode === "search-and-destroy"),
      round: roundProfiles.find((r) => r.year === year),
      streaks: streakRows.find((s) => s.year === year),
      feed: feedSeasons.find((f) => f.year === year),
    };
  });
}

// ---------- Presentational helpers ----------

function sampleNote(sample: string, qualified: boolean): string {
  return qualified ? sample : `${sample} · below minimum`;
}

/** Card header + dim-below-minimum wrapper shared by every percentile card. */
function StatCard({
  heading,
  sample,
  qualified,
  children,
}: {
  heading: string;
  sample?: string;
  qualified: boolean;
  children: ReactNode;
}) {
  return (
    <div>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold text-ink-secondary">{heading}</span>
        {sample && (
          <span className="font-mono text-xs text-ink-muted">
            {sampleNote(sample, qualified)}
          </span>
        )}
      </div>
      <div className={qualified ? "" : "opacity-50"}>{children}</div>
    </div>
  );
}

/**
 * The headline stats stay visible; the tail collapses. No toggle when it would
 * hide only a row or two.
 */
function MetricList({ stats }: { stats: CardStat[] }) {
  if (stats.length <= HEADLINE_STATS + 2) {
    return <PercentileProfile stats={stats} />;
  }
  const tail = stats.slice(HEADLINE_STATS);
  return (
    <div>
      <PercentileProfile stats={stats.slice(0, HEADLINE_STATS)} />
      <details className="mt-1">
        <summary className="cursor-pointer select-none font-mono text-xs text-ink-muted hover:text-ink-secondary">
          all {stats.length} metrics
        </summary>
        <div className="mt-2">
          <PercentileProfile stats={tail} />
        </div>
      </details>
    </div>
  );
}

function HowToRead({ children }: { children: ReactNode }) {
  return (
    <details className="mt-10 border-t border-hairline pt-3 text-xs text-ink-muted">
      <summary className="eyebrow cursor-pointer select-none text-ink-secondary">
        How to read this
      </summary>
      <div className="mt-3 space-y-2">{children}</div>
    </details>
  );
}

function ClutchTable({ lines }: { lines: ClutchLine[] }) {
  return (
    <table className="mt-2 w-full max-w-xs text-left text-sm">
      <tbody>
        {lines.map((c) => (
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
  );
}

// RAPM. The only number on this page with nothing from the box score in it,
// and the only one whose interval usually covers zero -- 7 of 196 players clear
// 1.96 SE. So the interval is the chart and the coefficient is a label on it,
// not the other way round, and the verdict is written out rather than left for
// a reader to infer from a sign.
function RapmSection({ rapm }: { rapm: PlayerRapm }) {
  const half = 1.96 * rapm.se;
  const lo = rapm.coef - half;
  const hi = rapm.coef + half;
  const domain = Math.max(1, Math.ceil(Math.max(Math.abs(lo), Math.abs(hi)) * 2) / 2);

  const W = 460;
  const H = 46;
  // Wide enough that the end ticks, which are centred on the axis extremes,
  // do not run off the viewBox.
  const M = { left: 26, right: 26 };
  const iw = W - M.left - M.right;
  const x = (v: number) => M.left + ((v + domain) / (2 * domain)) * iw;
  const yMid = 20;

  return (
    <section className="mt-10">
      <h2 className="lower-third">
        Value in wins
        <span className="lt-note">RAPM, with its 95% interval</span>
      </h2>
      <div className="mt-3 border border-hairline bg-surface p-4">
        <figure>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="w-full"
            role="img"
            aria-label={`RAPM ${rapm.coef.toFixed(2)}, 95% interval ${lo.toFixed(2)} to ${hi.toFixed(2)}`}
          >
            {[-domain, 0, domain].map((v) => (
              <g key={v}>
                <line
                  x1={x(v)}
                  x2={x(v)}
                  y1={4}
                  y2={yMid + 10}
                  stroke={v === 0 ? "var(--baseline)" : "var(--hairline)"}
                />
                <text
                  x={x(v)}
                  y={H - 6}
                  textAnchor="middle"
                  fontSize={9.5}
                  fill="var(--ink-muted)"
                  className="font-mono"
                >
                  {v > 0 ? `+${v}` : v}
                </text>
              </g>
            ))}
            <line
              x1={x(lo)}
              x2={x(hi)}
              y1={yMid}
              y2={yMid}
              stroke={rapm.resolved ? "var(--series-1)" : "var(--ink-muted)"}
              strokeWidth={6}
              strokeLinecap="round"
              opacity={rapm.resolved ? 1 : 0.55}
            />
            <circle
              cx={x(rapm.coef)}
              cy={yMid}
              r={4}
              fill="var(--surface)"
              stroke={rapm.resolved ? "var(--series-1)" : "var(--ink-secondary)"}
              strokeWidth={2}
            />
          </svg>
        </figure>
        <p className="mt-2 font-mono text-sm text-ink-secondary">
          {rapm.coef >= 0 ? "+" : ""}
          {rapm.coef.toFixed(2)}{" "}
          <span className="text-ink-muted">
            ({lo >= 0 ? "+" : ""}
            {lo.toFixed(2)} to {hi >= 0 ? "+" : ""}
            {hi.toFixed(2)}) · {rapm.maps} maps
          </span>
        </p>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-ink-muted">
        Ridge-regressed contribution to the log-odds of winning a map, holding
        the other seven players on the server constant. Nothing from the box
        score enters the fit, which is what makes it worth reading next to the
        composite rating rather than as a version of it.{" "}
        {rapm.resolved ? (
          <>
            This interval clears zero &mdash; one of the few on the site that
            does, since the ridge penalty is larger than the signal for most
            players.
          </>
        ) : (
          <>
            This interval covers zero, so the sign is not evidence: the model
            cannot separate this player from average.
            That is true of 189 of the 196 players it rates.
          </>
        )}{" "}
        {rapm.entangled && (
          <>
            It is also substantially a team number. This player spent{" "}
            {Math.round(rapm.concentration * 100)}% of their maps beside the
            same teammate, and players who never play apart are one column
            wearing two names &mdash; ridge splits the credit between them
            evenly, which is correct and is not a finding about either.
          </>
        )}
      </p>
    </section>
  );
}

// SKILL, and the two quantities it is made of. The row is only arguable with
// all three shown: the prior is what the box score expected, the coefficient is
// what the maps said, and the weight is which of them the rating listened to.
function SkillSection({
  skill,
  coveredYears,
  lastYear,
}: {
  skill: PlayerSkillSeason[];
  coveredYears: number[];
  lastYear: number | null;
}) {
  const covered = new Set(coveredYears);
  if (skill.length === 0) {
    return (
      <section data-surface="skill" data-state="absent" className="mt-10">
        <h2 className="lower-third">
          How good now
          <span className="lt-note">SKILL — not published for this player</span>
        </h2>
        <p className="mt-3 max-w-3xl text-xs leading-relaxed text-ink-muted">
          {lastYear === null
            ? "SKILL is not published for this player."
            : primacyReason(lastYear, covered)}{" "}
          The rating below answers a different question — what a season was
          worth — and it is the one this page leads with here.
        </p>
      </section>
    );
  }
  const meanWeight =
    skill.reduce((s, r) => s + r.weightPrior, 0) / skill.length;
  return (
    <section data-surface="skill" data-state="present" className="mt-10">
      <h2 className="lower-third">
        How good now
        <span className="lt-note">SKILL, by season</span>
      </h2>
      <div className="mt-3 border border-hairline bg-surface p-4">
        <SkillBlend
          seasons={skill.map((r) => ({
            label: String(r.year),
            priorMean: r.priorMean,
            coef: r.coef,
            se: r.se,
            skill: r.skill,
            skillSd: r.skillSd,
            weightPrior: r.weightPrior,
          }))}
        />
        <p className="mt-2 text-xs leading-relaxed text-ink-muted">
          The wide bar is what this player&rsquo;s maps established, with its
          own uncertainty; the upright tick is what the box score expected of
          them. SKILL is the dot, and it lands beside the tick rather than in
          the middle — the blend is a prior with a correction, not two opinions
          meeting halfway.
        </p>
      </div>
      <div className="mt-3 overflow-x-auto border border-hairline bg-surface p-4">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-hairline text-xs text-ink-muted">
              <th className="py-2 pr-4 font-normal">Season</th>
              <th className="py-2 pr-4 text-right font-normal">
                Box-score prior
              </th>
              <th className="py-2 pr-4 text-right font-normal">
                Plus-minus ± se
              </th>
              <th className="py-2 pr-4 text-right font-normal">SKILL ± sd</th>
              <th className="py-2 text-right font-normal">From the prior</th>
            </tr>
          </thead>
          <tbody>
            {skill.map((r) => (
              <tr key={r.seasonId} className="border-b border-hairline/60">
                <td className="py-1.5 pr-4 text-ink-secondary">
                  {r.year} {r.title}
                </td>
                <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                  {r.priorMean >= 0 ? "+" : ""}
                  {r.priorMean.toFixed(3)}
                </td>
                <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums text-ink-secondary">
                  {r.coef >= 0 ? "+" : ""}
                  {r.coef.toFixed(3)} ±{r.se.toFixed(3)}
                </td>
                <td className="py-1.5 pr-4 text-right font-mono text-xs tabular-nums">
                  {r.skill >= 0 ? "+" : ""}
                  {r.skill.toFixed(3)}
                  <span className="text-ink-muted">
                    {" "}
                    ±{r.skillSd.toFixed(3)}
                  </span>
                </td>
                <td className="py-1.5 text-right font-mono text-xs tabular-nums text-ink-secondary">
                  {(r.weightPrior * 100).toFixed(0)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 max-w-3xl text-xs leading-relaxed text-ink-muted">
        {RATINGS.skill.judge}, with the prior fitted on seasons strictly before
        the one it predicts. The last column says which side the posterior
        listened to: it averages {(meanWeight * 100).toFixed(0)}% here, so this
        rating is close to what the box score expected of this player rather
        than what their maps established. Its own forward test went against it —{" "}
        {RATINGS.skill.failure}. <a href={RATINGS.skill.href}>Methodology</a>.
      </p>
    </section>
  );
}

// The composite rating, drawn with the posterior interval that belongs to it.
// A career of two-decimal ratings invites reading a 0.03 gap as improvement;
// with the bands drawn, most of a player's seasons turn out to be one season
// measured three times.
function RatingSection({ ratings }: { ratings: PlayerRatings }) {
  const best = ratings.seasons.reduce((a, b) => (b.rating > a.rating ? b : a));
  const separable = ratings.seasons.filter(
    (s) => s.seasonId !== best.seasonId && !overlaps(s, best),
  ).length;
  const others = ratings.seasons.length - 1;
  const times = ["", "once", "twice", "three times", "four times", "five times"];
  const timesLabel =
    times[ratings.seasons.length] ?? `${ratings.seasons.length} times`;

  return (
    <section className="mt-10">
      <h2 className="lower-third">
        Rating
        <span className="lt-note">composite, with its 95% interval</span>
      </h2>
      <div className="mt-3 border border-hairline bg-surface p-4">
        <RatingIntervals
          seasons={ratings.seasons}
          lo={ratings.scale.lo}
          hi={ratings.scale.hi}
          minMaps={ratings.minMaps}
        />
      </div>
      <p className="mt-3 text-xs text-ink-muted">
        The composite rating weights each stat by what it is worth to winning
        maps in that title and mode, then reads the result through a two-level
        model of the cohort; an average qualified season is 1.00. The bar is
        ±1.96 posterior sd — what is still unknown about the player after
        pooling — drawn on the range the archive&rsquo;s qualified seasons
        occupy, widened where a band runs past it.{" "}
        {others === 0 ? (
          <>
            One rated season, so there is nothing here to compare it against
            except the league.
          </>
        ) : separable === 0 ? (
          <>
            No other season&rsquo;s interval clears {best.year} {best.title}
            &rsquo;s, so this career reads as one level measured {timesLabel}{" "}
            rather than a trajectory.
          </>
        ) : separable === others ? (
          <>
            Every other season sits clear of {best.year} {best.title}: the gaps
            here are wider than the model&rsquo;s uncertainty about them.
          </>
        ) : (
          <>
            {separable} of the {others} other seasons{" "}
            {separable === 1 ? "sits" : "sit"} clear of {best.year} {best.title};
            the remaining {others - separable} overlap it and should be read as
            the same level, not a decline or a rise.
          </>
        )}{" "}
        <Link href="/methodology/player-rating">Methodology</Link>.
      </p>
    </section>
  );
}

// ---------- Tab content ----------

// Career totals over a replacement baseline, every way of counting them.
//
// Three columns because they rank different players first, and both credit
// rules because the choice between them is a choice. On this record the two
// rules agree at rho 0.998, so a reader who only wants one number can take
// either; the pair is here so that agreement is checkable on a player page and
// not only in the methodology.
const CREDIT_LABEL: Record<string, string> = {
  none: "scoreboard",
  deviation: "own deviation",
  deviation_plus_team: "with team share",
};

function CareerTotalsSection({ rows }: { rows: PlayerCareerRow[] }) {
  if (rows.length === 0) return null;
  const composite = rows.filter((r) => r.axis === "composite");
  const plusMinus = rows.filter((r) => r.axis === "plus_minus");
  return (
    <section className="mt-10">
      <h2 className="lower-third">
        Career value
        <span className="lt-note">summed over the qualified-cohort minimum</span>
      </h2>
      <div className="mt-3 overflow-x-auto border border-hairline bg-surface p-4">
        <table className="w-full min-w-[34rem] text-left text-xs">
          <thead className="text-ink-muted">
            <tr>
              <th className="py-1 pr-4 font-normal">Counted as</th>
              <th className="py-1 pr-4 font-normal">Seasons</th>
              <th className="py-1 pr-4 font-normal">Total</th>
              <th className="py-1 pr-4 font-normal">Peak</th>
              <th className="py-1 font-normal">Best three</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {[...composite, ...plusMinus].map((r) => (
              <tr
                key={`${r.axis}-${r.credit}-${r.eraScope}`}
                className="border-t border-hairline"
              >
                <td className="py-1 pr-4">
                  {r.axis === "composite"
                    ? "scoreboard, all seasons"
                    : `plus-minus ${r.eraScope.toUpperCase()}, ${CREDIT_LABEL[r.credit] ?? r.credit}`}
                </td>
                <td className="py-1 pr-4">{r.seasons}</td>
                <td className="py-1 pr-4">
                  {r.total.toFixed(2)}
                  {r.totalSd !== null && (
                    <span className="text-ink-muted">
                      {" \u00b1 "}
                      {r.totalSd.toFixed(2)}
                    </span>
                  )}
                </td>
                {/* An era row is one pooled estimate across three seasons, so
                    it cannot name which of them was the peak. Showing the total
                    again in this column would read as a season that stood out. */}
                <td className="py-1 pr-4">
                  {r.peakSeasonYear === null ? (
                    <span className="text-ink-muted">—</span>
                  ) : (
                    <>
                      {r.peak.toFixed(2)}
                      <span className="text-ink-muted"> {r.peakSeasonYear}</span>
                    </>
                  )}
                </td>
                <td className="py-1">
                  {r.bestThree === null ? (
                    <span className="text-ink-muted">—</span>
                  ) : (
                    <>
                      {r.bestThree.toFixed(2)}
                      {r.bestThreeStartYear !== null && (
                        <span className="text-ink-muted">
                          {" from "}
                          {r.bestThreeStartYear}
                        </span>
                      )}
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs leading-relaxed text-ink-muted">
          A CWL row is one pooled estimate for the whole era, so it sits beside a
          CDL total and is never added to it. Most plus-minus intervals across
          the league overlap, which is what the season coefficients under them
          support.
        </p>
      </div>
    </section>
  );
}

/** A second, independent career axis: peak/best-three/total over the
 *  gold-tier metric basket instead of over VALUE or SKILL. Disagrees with
 *  `CareerTotalsSection` where the two measure different things — see
 *  docs/methodology.md#career-rank. Net-of-teammates and opponent strength
 *  are shown per season as context, never folded into the score itself. */
function CareerRankSection({
  summary,
  seasons,
}: {
  summary: PlayerCareerRankSummary | null;
  seasons: PlayerCareerRankSeason[];
}) {
  if (summary === null || seasons.length === 0) return null;
  return (
    <section className="mt-10">
      <h2 className="lower-third">
        Career rank
        <span className="lt-note">the gold-tier metric basket, blended by season</span>
      </h2>
      <div className="mt-3 overflow-x-auto border border-hairline bg-surface p-4">
        <p className="font-mono text-sm">
          {summary.total.toFixed(1)}
          {summary.totalSd !== null && (
            <span className="text-ink-muted">
              {" ± "}
              {summary.totalSd.toFixed(1)}
            </span>
          )}
          <span className="ml-2 text-xs text-ink-muted">
            over {summary.nSeasons} season{summary.nSeasons === 1 ? "" : "s"}
            {!summary.qualified && " · below the ranking floor"}
          </span>
        </p>
        <p className="mt-1 text-xs text-ink-muted">
          Peak {summary.peak.toFixed(1)}
          {summary.peakSeasonYear !== null && ` (${summary.peakSeasonYear})`}
          {summary.bestThree !== null && (
            <>
              {" · best three "}
              {summary.bestThree.toFixed(1)}
              {summary.bestThreeStartYear !== null &&
                ` from ${summary.bestThreeStartYear}`}
            </>
          )}
        </p>
        <table className="mt-4 w-full min-w-[34rem] text-left text-xs">
          <thead className="text-ink-muted">
            <tr>
              <th className="py-1 pr-4 font-normal">Season</th>
              <th className="py-1 pr-4 font-normal">Score</th>
              <th className="py-1 pr-4 font-normal">Net of teammates</th>
              <th className="py-1 font-normal">Opponent strength</th>
            </tr>
          </thead>
          <tbody className="font-mono">
            {seasons.map((s) => (
              <tr key={s.seasonId} className="border-t border-hairline">
                <td className="py-1 pr-4">
                  {s.year} {s.league}
                </td>
                <td className="py-1 pr-4">
                  {s.score.toFixed(1)}
                  {s.sd !== null && (
                    <span className="text-ink-muted">
                      {" ± "}
                      {s.sd.toFixed(1)}
                    </span>
                  )}
                </td>
                <td className="py-1 pr-4">
                  {s.netOfTeammates === null ? (
                    <span className="text-ink-muted">—</span>
                  ) : (
                    s.netOfTeammates.toFixed(2)
                  )}
                </td>
                <td className="py-1">
                  {s.opponentStrength === null ? (
                    <span className="text-ink-muted">—</span>
                  ) : (
                    s.opponentStrength.toFixed(2)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs leading-relaxed text-ink-muted">
          The score blends every gold-tier stat the player page shows,
          weighted by how much each mode was played that season, plus award
          credit. Its SD reflects how much that basket disagreed with itself,
          not a measurement error on any one stat. Net of teammates and
          opponent strength are context, not adjustments to the score.
        </p>
      </div>
    </section>
  );
}

/** Where this player stood at the opening engagement of a Search and Destroy
 *  round, and what the league-wide entry cost gives back on their K/D.
 *
 *  Three K/D numbers ship together or not at all: the raw one, the part the
 *  contact rate accounts for, and what is left. The adjustment here is small by
 *  construction, because the league fit it comes from could not separate K/D
 *  from contact rate at all, and a reader can see that rather than take it. */
function RoleSection({
  rows,
  model,
}: {
  rows: RoleSeason[];
  model: RoleModel | null;
}) {
  if (rows.length === 0) return null;
  const kd = model?.entryCost.find((c) => c.outcome === "kd") ?? null;
  return (
    <section className="mt-10">
      <h2 className="lower-third">
        Opening engagement
        <span className="lt-note">Search &amp; Destroy &middot; 2020&ndash;2026</span>
      </h2>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-hairline text-xs text-ink-muted">
              <th className="py-2 pr-4 font-normal">Season</th>
              <th className="py-2 pr-4 text-right font-normal">Maps</th>
              <th className="py-2 pr-4 text-right font-normal">Contacts / map</th>
              <th className="py-2 pr-4 text-right font-normal">Pctl</th>
              <th className="py-2 pr-4 text-right font-normal">Won</th>
              <th className="py-2 pr-4 text-right font-normal">K/D (raw)</th>
              <th className="py-2 pr-4 text-right font-normal">Role</th>
              <th className="py-2 text-right font-normal">K/D (adjusted)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.year}-${r.title}`} className="border-b border-hairline/50">
                <td className="py-2 pr-4">
                  {r.year} {r.title}
                </td>
                <td className="py-2 pr-4 text-right font-mono">{r.maps}</td>
                <td className="py-2 pr-4 text-right font-mono">
                  {r.contactRate.toFixed(2)}
                </td>
                <td className="py-2 pr-4 text-right font-mono text-ink-muted">
                  {Math.round(r.contactPctl * 100)}
                </td>
                <td className="py-2 pr-4 text-right font-mono">
                  {Math.round(r.contactWinRate * 100)}%
                </td>
                <td className="py-2 pr-4 text-right font-mono">
                  {r.kdRaw === null ? "—" : `${r.kdRaw > 0 ? "+" : ""}${r.kdRaw.toFixed(2)}`}
                </td>
                <td className="py-2 pr-4 text-right font-mono text-ink-muted">
                  {r.kdAdjustment === null
                    ? "—"
                    : `${r.kdAdjustment > 0 ? "+" : ""}${r.kdAdjustment.toFixed(2)}`}
                </td>
                <td className="py-2 text-right font-mono">
                  {r.kdAdjusted === null
                    ? "—"
                    : `${r.kdAdjusted > 0 ? "+" : ""}${r.kdAdjusted.toFixed(2)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs leading-relaxed text-ink-muted">
        A contact is an opening kill or an opening death, so the rate is how
        often this player was in the first fight of a round, and the percentile
        places it among that season&rsquo;s qualified players. The K/D columns
        are standardised within the season, and all three are printed because
        the middle one is the whole adjustment.
        {kd && (
          <>
            {` Across the league, one SD more opening contact went with ${kd.slope > 0 ? "+" : ""}${kd.slope.toFixed(3)} SD of K/D, on an interval of ${kd.lo95.toFixed(3)} to ${kd.hi95.toFixed(3)} that ${kd.separates ? "excludes zero" : "does not clear zero"}.`}
          </>
        )}{" "}
        The position is published without a role name: the style work found no
        archetype to name it with. See{" "}
        <Link href="/methodology/role">methodology</Link>.
      </p>
    </section>
  );
}

function CareerTab({
  arcPoints,
  fingerprint,
  role,
  roleModel,
  style,
  ratings,
  rapm,
  skill,
  careerTotals,
  careerRank,
  skillYears,
  lastYear,
  allModes,
  playerInsights,
}: {
  arcPoints: ArcPoint[];
  fingerprint: FingerprintData | null;
  role: RoleSeason[];
  roleModel: RoleModel | null;
  style: StyleView | null;
  ratings: PlayerRatings | null;
  rapm: PlayerRapm | null;
  skill: PlayerSkillSeason[];
  careerTotals: PlayerCareerRow[];
  careerRank: {
    summary: PlayerCareerRankSummary | null;
    seasons: PlayerCareerRankSeason[];
  };
  skillYears: number[];
  lastYear: number | null;
  allModes: SeasonAdjusted[];
  playerInsights: { id: number; kind: string; headline: string }[];
}) {
  return (
    <div>
      <section>
        <h2 className="lower-third">
          Career arc
          <span className="lt-note">K/D against each season&rsquo;s cohort</span>
        </h2>
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

      <CareerTotalsSection rows={careerTotals} />

      <CareerRankSection summary={careerRank.summary} seasons={careerRank.seasons} />

      <SkillSection
        skill={skill}
        coveredYears={skillYears}
        lastYear={lastYear}
      />

      {ratings && <RatingSection ratings={ratings} />}

      {rapm && <RapmSection rapm={rapm} />}

      {fingerprint && (
        <section className="mt-10">
          <h2 className="lower-third">
            Fingerprint
            <span className="lt-note">headline percentiles by season</span>
          </h2>
          <div className="mt-4">
            <Fingerprint
              seasons={fingerprint.seasons}
              groups={fingerprint.groups}
            />
          </div>
          <p className="mt-3 text-xs text-ink-muted">
            Each cell is the percentile within the qualified players of that
            season and mode; metrics where low is good are flipped so brighter
            always reads better. Reading down a column gives the season&rsquo;s
            shape; reading across a row gives the career drift.
          </p>
        </section>
      )}

      <RoleSection rows={role} model={roleModel} />

      {style && (
        <section className="mt-10">
          <h2 className="lower-third">
            Style
            <span className="lt-note">position, not label &middot; {style.era}</span>
          </h2>
          <div className="mt-4 border border-hairline bg-surface p-4">
            <StyleAxes
              axes={style.axes}
              points={style.points}
              cohortN={style.cohortN}
            />
          </div>
          <p className="mt-3 text-xs text-ink-muted">
            There is no archetype here to compare against, and that is a
            finding rather than a gap. Clustering these metrics never beats a
            cloud with no clusters in it: the best-separated partition scores a
            silhouette of {style.bestSilhouette.toFixed(3)} where an unclustered
            cloud of the same size and shape scores{" "}
            {style.nullLo.toFixed(3)}&ndash;{style.nullHi.toFixed(3)}. So a
            player is drawn as a position on the {style.axes.length}{" "}
            axes that survive Horn&rsquo;s parallel analysis, over the{" "}
            {style.cohortN.toLocaleString()} {style.era}{" "}
            player-seasons, with the composite
            rating already projected out &mdash; this is how someone played at
            their level, not what level that was. See{" "}
            <Link href="/methodology/player-style">methodology</Link>.
          </p>
        </section>
      )}

      <section className="mt-10">
        <h2 className="lower-third">Seasons</h2>
        <div className="mt-3 overflow-x-auto">
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
                    {a.kdZ !== null && a.kdZSe !== null && (
                      <span className="text-ink-muted"> ±{a.kdZSe.toFixed(2)}</span>
                    )}
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
        </div>
        <p className="mt-3 text-xs text-ink-muted">
          The ± on the cohort z-score is the era model&rsquo;s standard error
          for that season, the same quantity the career arc bands. Coverage is
          the share of the season&rsquo;s metrics the source data actually
          supports; a low one is why a season can be rated and still be wide.
        </p>
      </section>

      {playerInsights.length > 0 && (
        <section className="mt-10">
          <h2 className="lower-third">
            Findings
            <span className="lt-note">this player, current model run</span>
          </h2>
          <ul className="mt-3 space-y-3">
            {playerInsights.map((i) => (
              <li
                key={i.id}
                className="border border-hairline bg-surface p-3 text-sm"
              >
                <span className="eyebrow mr-2 text-[10px] text-accent">
                  {kindLabel(i.kind)}
                </span>
                {i.headline}
              </li>
            ))}
          </ul>
        </section>
      )}

      <HowToRead>
        <p>
          The career arc plots K/D as standard deviations from the
          qualified-player mean of each season and title, so seasons in
          different games are comparable.
        </p>
        <p>
          In the seasons table, &ldquo;vs cohort&rdquo; is the same z-score and
          coverage is the share of this player&rsquo;s map rows with complete
          kill and death data. Stats the archive lacks are shown as
          &ldquo;—&rdquo;.
        </p>
      </HowToRead>
    </div>
  );
}

function SeasonTab({ view }: { view: SeasonView }) {
  const profile = view.overall ? seasonProfile(view.overall) : [];
  // A season with box scores and no event feed can never fill the trade,
  // clutch and man-advantage cards. Read off the season rather than named, so
  // every such season says so instead of only the one that was named here.
  const noFeed = Boolean(view.overall || view.modeCards.length > 0) && !view.feed;
  const hasSnd = Boolean(
    view.sndCard ||
      view.round ||
      view.feed?.clutch ||
      view.feed?.advantage,
  );
  const hasTempo = Boolean(view.streaks || view.feed?.trades);
  return (
    <div>
      {profile.length > 0 && view.overall && (
        <section>
          <div className="grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
            <StatCard
              heading="Season profile"
              sample={`${view.overall.mapsPlayed} maps`}
              qualified
            >
              <PercentileProfile stats={profile} />
            </StatCard>
          </div>
        </section>
      )}

      {(view.modeCards.length > 0 || hasTempo) && (
        <section className="mt-10">
          <h2 className="lower-third">
            Mode profiles
            <span className="lt-note">percentile within season-and-mode cohort</span>
          </h2>
          <div className="mt-4 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
            {view.modeCards.map((card) => (
              <StatCard
                key={card.key}
                heading={card.heading}
                sample={card.sample}
                qualified={card.qualified}
              >
                <MetricList stats={card.stats} />
                {card.mode === "all" && view.streaks && (
                  <div className="mt-3">
                    <div className="eyebrow text-[10px] text-ink-muted">
                      Streaks per map
                    </div>
                    <div className="mt-1.5 flex gap-4">
                      {view.streaks.streaks.map((s) => (
                        <div key={s.label}>
                          <div className="font-mono text-xs text-ink-muted">
                            {s.label}
                          </div>
                          <div className="font-mono text-sm tabular-nums">
                            {s.value.toFixed(2)}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </StatCard>
            ))}
            {view.feed?.trades && (
              <StatCard
                heading="Trades · all modes"
                sample={view.feed.trades.sample}
                qualified={view.feed.trades.qualified}
              >
                <PercentileProfile stats={view.feed.trades.stats} />
              </StatCard>
            )}
          </div>
        </section>
      )}

      {hasSnd && (
        <section className="mt-10">
          <h2 className="lower-third">
            Search &amp; Destroy
            <span className="lt-note">mode profile, rounds, clutches</span>
          </h2>
          <div className="mt-4 grid grid-cols-1 gap-x-10 gap-y-8 md:grid-cols-2">
            {view.sndCard && (
              <StatCard
                heading="Mode profile"
                sample={view.sndCard.sample}
                qualified={view.sndCard.qualified}
              >
                <MetricList stats={view.sndCard.stats} />
              </StatCard>
            )}
            {view.round && (
              <StatCard
                heading="Round by round"
                sample={view.round.profile?.sample}
                qualified={view.round.profile?.qualified ?? true}
              >
                {view.round.shares.length > 0 && (
                  <div className="mb-4">
                    <RoundShareBar segments={view.round.shares} />
                  </div>
                )}
                {view.round.profile && (
                  <PercentileProfile stats={view.round.profile.stats} />
                )}
                {view.round.counts.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1">
                    {view.round.counts.map((c) => (
                      <span key={c.label} className="text-xs text-ink-secondary">
                        {c.label}{" "}
                        <span className="font-mono tabular-nums text-ink">
                          {c.value}
                        </span>
                      </span>
                    ))}
                  </div>
                )}
              </StatCard>
            )}
            {view.feed?.advantage && (
              <StatCard
                heading="Man advantage"
                sample={view.feed.advantage.sample}
                qualified={view.feed.advantage.qualified}
              >
                <PercentileProfile stats={view.feed.advantage.stats} />
              </StatCard>
            )}
            {view.feed?.clutch && (
              <StatCard
                heading="Clutch record"
                sample={view.feed.clutch.sample}
                qualified={view.feed.clutch.qualified}
              >
                {view.feed.clutch.rate && (
                  <PercentileProfile stats={[view.feed.clutch.rate]} />
                )}
                <ClutchTable lines={view.feed.clutch.lines} />
              </StatCard>
            )}
          </div>
        </section>
      )}

      {noFeed && (
        <p className="mt-6 text-xs text-ink-muted">
          {view.title} has box scores but no event feed, so trade, clutch and
          man-advantage detail does not apply to this season.
        </p>
      )}

      {view.byMode.length > 0 && (
        <section className="mt-10">
          <h2 className="lower-third">By mode</h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline text-xs text-ink-muted">
                  <th className="py-2 pr-4 font-normal">Mode</th>
                  <th className="py-2 pr-4 text-right font-normal">Maps</th>
                  <th className="py-2 pr-4 text-right font-normal">K/D</th>
                  <th className="py-2 pr-4 font-normal">Percentile</th>
                  <th className="py-2 text-right font-normal">Objective</th>
                </tr>
              </thead>
              <tbody>
                {view.byMode.map((a) => (
                  <tr
                    key={`${a.seasonId}-${a.modeId}`}
                    className="border-b border-hairline/60"
                  >
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
          </div>
        </section>
      )}

      <HowToRead>
        <p>
          Percentile tracks are within the qualified players of the same season
          and mode; metrics where low is good are flipped so a full track
          always reads well. Cards below the sample minimum are dimmed. In the
          season profile, K/D is an exact percentile and engagement and
          objective are cohort z-scores placed on the track through a normal
          approximation.
        </p>
        {view.round && (
          <p>
            The round bar is the share of the player&rsquo;s Search &amp;
            Destroy rounds ending with each kill count, so the left edge is how
            often they went scoreless. It is a distribution, not a ranking.
            Clutch W–L is raw.
          </p>
        )}
        {view.streaks && (
          <p>
            The blitz index weights multikills by size — a 4-piece counts four
            times a 2-piece — so it separates players who kill in bursts from
            players with the same total spread evenly. Streaks per map count
            runs of that many kills without dying.
          </p>
        )}
        <p>
          &ldquo;Objective&rdquo; in the mode table is the mode&rsquo;s own
          metric (hill time, S&amp;D opening plays, captures) as a cohort
          z-score; &ldquo;—&rdquo; means the archive lacks that stat or the
          player didn&rsquo;t qualify. Full definitions are in the{" "}
          <Link href="/methodology/metrics" className="underline">
            metric glossary
          </Link>
          .
        </p>
      </HowToRead>
    </div>
  );
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

  const [
    eraRun,
    insightsRun,
    metricRun,
    styleRun,
    roleRun,
    ratingRun,
    skillRun,
    careerRun,
    careerRankRun,
  ] = await Promise.all([
      latestRun("era_adjust"),
      latestRun("insights"),
      latestRun("metric_layer"),
      latestRun("player_style"),
      latestRun("role"),
      latestRatingRun(),
      latestSkillRun(),
      latestCareerRun(),
      latestCareerRankRun(),
    ]);
  const [
    adjusted,
    spans,
    stints,
    playerInsights,
    metricValues,
    metricCatalog,
    stylePoints,
    styleArtifact,
    rolePoints,
    roleArtifact,
    ratings,
    rapm,
    skill,
    skillSeasons,
    modeCatalog,
    careerTotals,
    careerRank,
  ] =
    await Promise.all([
      eraRun ? getPlayerAdjusted(player.id, eraRun.id) : Promise.resolve([]),
      getPlayerSpans(player.id),
      getPlayerStints(player.id),
      insightsRun ? getPlayerInsights(player.id, insightsRun.id) : Promise.resolve([]),
      metricRun ? getPlayerMetrics(metricRun.id, player.id) : Promise.resolve([]),
      metricRun ? getMetricCatalog(metricRun.id) : Promise.resolve(null),
      styleRun ? getPlayerStyle(styleRun.id, player.id) : Promise.resolve([]),
      getPlayerStyleArtifact(),
      roleRun ? getPlayerRole(roleRun.id, player.id) : Promise.resolve([]),
      getRole(),
      ratingRun
        ? getPlayerRatingSeasons(player.id, ratingRun.id)
        : Promise.resolve(null),
      ratingRun
        ? getPlayerRapm(ratingRun.id, player.id)
        : Promise.resolve(null),
      skillRun ? getPlayerSkill(skillRun.id, player.id) : Promise.resolve([]),
      skillRun ? getSkillSeasons(skillRun.id) : Promise.resolve([]),
      getModeCatalog(),
      careerRun
        ? getPlayerCareer(careerRun.id, player.id)
        : Promise.resolve([]),
      careerRankRun
        ? getPlayerCareerRank(careerRankRun.id, player.id)
        : Promise.resolve({ summary: null, seasons: [] }),
    ]);
  const skillYears = skillSeasons.map((s) => s.year);
  const metricCards = buildMetricCards(metricValues, metricCatalog, modeCatalog);
  const feedSeasons = buildFeedSeasons(metricValues, metricCatalog);
  const roundProfiles = buildRoundProfiles(metricValues, metricCatalog);
  const streakRows = buildStreakRows(metricValues);
  const fingerprint = buildFingerprint(metricValues, metricCatalog, modeCatalog);
  const style = buildStyleView(styleArtifact?.style ?? null, stylePoints);

  const allModes = adjusted.filter((a) => a.modeId === null);
  const byMode = adjusted.filter((a) => a.modeId !== null);
  const careerMaps = allModes.reduce((s, a) => s + a.mapsPlayed, 0);

  const seasonViews = buildSeasonViews(
    allModes,
    byMode,
    metricCards,
    roundProfiles,
    streakRows,
    feedSeasons,
    modeCatalog,
  );

  const arcPoints: ArcPoint[] = allModes
    .filter((a) => a.kdZ !== null && a.kdPctl !== null)
    .map((a) => ({
      year: a.year,
      title: a.title,
      kdZ: a.kdZ as number,
      kdZSe: a.kdZSe,
      kdPctl: a.kdPctl as number,
      maps: a.mapsPlayed,
    }));

  const teamsPlayed = [...new Map(stints.map((s) => [s.teamId, s.team])).values()];

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <p className="eyebrow text-accent">Player · {formatLeagueSpans(spans)}</p>
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

      <div className="mt-8">
        <Tabs
          tabs={[
            {
              label: "Career",
              content: (
                <CareerTab
                  arcPoints={arcPoints}
                  fingerprint={fingerprint}
                  role={rolePoints}
                  roleModel={roleArtifact?.role ?? null}
                  style={style}
                  ratings={ratings}
                  rapm={rapm}
                  skill={skill}
                  careerTotals={careerTotals}
                  careerRank={careerRank}
                  skillYears={skillYears}
                  lastYear={
                    allModes.length > 0
                      ? Math.max(...allModes.map((a) => a.year))
                      : null
                  }
                  allModes={allModes}
                  playerInsights={playerInsights}
                />
              ),
            },
            ...seasonViews.map((view) => ({
              label: `${view.year}${view.title ? ` ${view.title}` : ""}`,
              content: <SeasonTab view={view} />,
            })),
          ]}
        />
      </div>
    </main>
  );
}
