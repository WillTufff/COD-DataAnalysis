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
import { RoundShareBar } from "@/components/charts/RoundShareBar";
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

// Card order within a season: overall first, then respawn modes; S&D is pulled
// out of this grid into its own combined panel.
const MODE_ORDER = ["all", "hardpoint", "control", "capture-the-flag", "uplink"];

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
        heading: v.mode ? (MODE_LABELS[v.mode] ?? v.mode) : "All modes",
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

const FINGERPRINT_MODES = [
  "all",
  "hardpoint",
  "search-and-destroy",
  "control",
  "capture-the-flag",
  "uplink",
];
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

type FingerprintData = {
  seasons: FingerprintSeason[];
  groups: FingerprintGroup[];
};

// Headline metrics (same priority ranking as the cards) × seasons, one group
// per mode, cells as within-cohort percentiles.
function buildFingerprint(
  values: PlayerMetricValue[],
  catalog: MetricCatalog | null,
): FingerprintData | null {
  if (!catalog) return null;
  const entries = new Map(catalog.metrics.map((m) => [m.key, m]));
  const { years, titleOf, bySlice } = sliceMetrics(values);
  if (years.length === 0) return null;
  const groups: FingerprintGroup[] = [];
  for (const mode of FINGERPRINT_MODES) {
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
      label: mode === "all" ? "All modes" : (MODE_LABELS[mode] ?? mode),
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
        .sort((a, b) => MODE_ORDER.indexOf(a.mode) - MODE_ORDER.indexOf(b.mode)),
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

// ---------- Tab content ----------

function CareerTab({
  arcPoints,
  fingerprint,
  allModes,
  playerInsights,
}: {
  arcPoints: ArcPoint[];
  fingerprint: FingerprintData | null;
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
                  {i.kind.replace("_", " ")}
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
  // BO4 (2019) has box scores but no event feed, so trade, clutch and
  // man-advantage cards can never populate for it. Say so, not silence.
  const isBo4 = view.title === "BO4";
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

      {isBo4 && !view.feed && (
        <p className="mt-6 text-xs text-ink-muted">
          Black Ops 4 has box scores but no event feed, so trade, clutch and
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
          <Link href="/methodology#metrics" className="underline">
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
  const roundProfiles = buildRoundProfiles(metricValues, metricCatalog);
  const streakRows = buildStreakRows(metricValues);
  const fingerprint = buildFingerprint(metricValues, metricCatalog);

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
  );

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

      <div className="mt-8">
        <Tabs
          tabs={[
            {
              label: "Career",
              content: (
                <CareerTab
                  arcPoints={arcPoints}
                  fingerprint={fingerprint}
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
