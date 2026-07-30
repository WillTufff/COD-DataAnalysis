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
    metrics: [
      "clutch_win_rate",
      "clutch_1v1_win_rate",
      "clutch_1v2_win_rate",
      "clutch_1v3_win_rate",
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
    metrics: [
      "hill_time_p10",
      "hill_time_share",
      "hill_caps_p10",
      "hill_defends_p10",
      "hp_obj_slay_split",
    ],
    defaultMode: "hardpoint",
    defaultSort: "hill_time_p10",
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
    blurb: "The engagement core — kills, deaths, plus-minus and trade discipline.",
    category: "All-mode cores",
    metrics: [
      "plus_minus_p10",
      "kills_p10",
      "deaths_p10",
      "ekia_p10",
      "kill_share",
      "kill_answered_rate",
      "time_per_life_s",
    ],
    defaultSort: "plus_minus_p10",
  },
  {
    id: "discipline-survival",
    name: "Discipline & Survival",
    blurb: "Staying alive and staying useful: clean kills, trades, mistakes.",
    category: "All-mode cores",
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
