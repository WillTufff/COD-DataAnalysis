// Preset views: curated column bundles so most readers never touch the full
// ~100-metric catalog. A preset only *seeds* a report — once its columns are
// edited it stops being a preset (the page drops `?preset=` from the URL).
//
// Keys here are validated against the live catalog at render time
// (`sanitizePresetMetrics`): the metric_layer can rename or retire a key
// between versions, and a stale preset must degrade to its surviving columns,
// never crash the page.

// Landing preset: the all-mode engagement core, so a bare visit covers every
// season and mode rather than one mode's specialism. Shared by the page and the
// export route — both must resolve a bare URL to the same report, or a download
// of the default view 400s.
export const DEFAULT_PRESET = "slaying-core";

export type ReportPreset = {
  id: string;
  name: string;
  blurb: string;
  category: string; // groups the preset picker
  metrics: string[]; // ordered column keys
  defaultMode?: string; // mode slug, or omit for all-modes
  defaultSort?: string; // metric key to rank by
};

export const REPORT_PRESETS: ReportPreset[] = [
  {
    id: "snd-entry",
    name: "S&D Entry Report",
    blurb: "Who wins the opening duel and sets the round on fire.",
    category: "Search & Destroy",
    metrics: [
      "snd_fb_rate",
      "snd_fd_rate",
      "snd_opening_duel_win",
      "snd_opening_involvement",
      "snd_fb_net_pr",
    ],
    defaultMode: "search-and-destroy",
    defaultSort: "snd_fb_net_pr",
  },
  {
    id: "clutch",
    name: "Clutch Report",
    blurb: "Last-alive win rates, from the even 1v1 to the stacked 1v3.",
    category: "Search & Destroy",
    // The by-N breakdown is read from the kill feed and stops with it; the
    // per-round rate is a box-score column and runs the other way, so the two
    // together cover the archive end to end rather than half of it.
    metrics: [
      "clutch_win_rate",
      "clutch_1v1_win_rate",
      "clutch_1v2_win_rate",
      "clutch_1v3_win_rate",
      "clutch_wins_pr",
    ],
    defaultMode: "search-and-destroy",
    defaultSort: "clutch_win_rate",
  },
  {
    id: "man-advantage",
    name: "Man-advantage Report",
    blurb: "Cashing in a numbers edge — and clawing back from a deficit.",
    category: "Search & Destroy",
    metrics: [
      "snd_adv_conversion",
      "snd_disadv_steal_rate",
      "snd_adv_rounds_lost",
      "snd_adv_thrown_deaths_pr",
    ],
    defaultMode: "search-and-destroy",
    defaultSort: "snd_adv_conversion",
  },
  {
    id: "hardpoint-objective",
    name: "Hardpoint Objective Report",
    blurb: "Hill time, captures and defends — objective work, not just kills.",
    category: "Objective modes",
    // Ranked on the per-map form: map time is recorded only where the source
    // ships a duration, so ranking on the per-10-minute form would default the
    // report to the seasons that have one.
    metrics: [
      "hill_time_pm",
      "hill_time_share",
      "hill_time_p10",
      "hill_caps_p10",
      "hill_defends_p10",
      "hp_obj_slay_split",
    ],
    defaultMode: "hardpoint",
    defaultSort: "hill_time_pm",
  },
  {
    id: "control",
    name: "Control Report",
    blurb: "Zone captures alongside the opening-duel slaying core.",
    category: "Objective modes",
    metrics: [
      "ctrl_caps_pm",
      "ctrl_fb_rate",
      "ctrl_fd_rate",
      "ctrl_opening_duel_win",
      "ctrl_fb_net_pr",
    ],
    defaultMode: "control",
    defaultSort: "ctrl_caps_pm",
  },
  {
    id: "ctf",
    name: "CTF Report",
    blurb: "Flag captures, carry efficiency, returns and defensive kills.",
    category: "Objective modes",
    metrics: [
      "ctf_caps_pm",
      "ctf_carry_efficiency",
      "ctf_returns_pm",
      "ctf_carrier_kills_pm",
      "ctf_flag_involvement_pm",
    ],
    defaultMode: "capture-the-flag",
    defaultSort: "ctf_caps_pm",
  },
  {
    id: "slaying-core",
    name: "Slaying Core",
    blurb: "The engagement core — kills, deaths, plus-minus and share of the team's work.",
    category: "All-mode cores",
    // Per-map rather than per-10-minute: map time is recorded only in the CWL
    // archive, so the per-10-minute forms of these stop at 2019. As the landing
    // preset this one has to cover every season, which is what per map buys.
    metrics: [
      "plus_minus_pm",
      "kills_pm",
      "deaths_pm",
      "kd",
      "kill_share",
      "damage_pm",
    ],
    defaultSort: "plus_minus_pm",
  },
  {
    id: "pace-tempo",
    name: "Pace & Tempo",
    blurb: "How much of the map a player spends in a fight, and what comes of it.",
    category: "All-mode cores",
    metrics: [
      "engagement_pm",
      "kills_pm",
      "deaths_pm",
      "assists_pm",
      "kill_share",
    ],
    defaultSort: "engagement_pm",
  },
  {
    id: "discipline-survival",
    name: "Discipline & Survival",
    blurb: "Staying alive and staying useful: clean kills, trades, mistakes.",
    // Every column here is read from the kill feed or the archive's extras, so
    // this preset stops where those do. It sits under its own heading rather
    // than beside the all-season cores, which would imply it spans them; the
    // seasons it actually reaches are stamped on the tile from the catalog.
    category: "Part-archive columns",
    metrics: [
      "clean_kill_rate",
      "kill_answered_rate",
      "time_per_life_s",
      "suicides_pm",
      "teamkills_total",
    ],
    defaultSort: "clean_kill_rate",
  },
];

export function presetById(id: string): ReportPreset | undefined {
  return REPORT_PRESETS.find((p) => p.id === id);
}

/**
 * A preset's columns, minus any key the live catalog no longer publishes. Drops
 * are logged server-side rather than surfaced — a reader should see the columns
 * that still exist, and the maintainer should see which preset went stale.
 */
export function sanitizePresetMetrics(
  preset: ReportPreset,
  knownKeys: Set<string>,
): string[] {
  const kept = preset.metrics.filter((k) => knownKeys.has(k));
  if (kept.length !== preset.metrics.length) {
    const dropped = preset.metrics.filter((k) => !knownKeys.has(k));
    console.warn(
      `Preset "${preset.id}" references unknown metric keys, dropped: ${dropped.join(", ")}`,
    );
  }
  return kept;
}
