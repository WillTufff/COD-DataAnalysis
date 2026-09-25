// Default column sets: what a bare visit to the report shows. Keys are
// validated against the live catalog at render time (`sanitizePresetMetrics`),
// so a retired key drops its column and never crashes the page.

// Per map rather than per 10 minutes: map time is recorded only in the CWL
// archive, so the per-10-minute forms stop at 2019. Shared by the page and the
// export route, which must resolve a bare URL to the same report.
export const DEFAULT_PRESET = "slaying-core";

/** The team side's landing preset. */
export const DEFAULT_TEAM_PRESET = "team-results";

export type ReportPreset = {
  id: string;
  entity?: "players" | "teams"; // omit for players
  name: string;
  metrics: string[]; // ordered column keys
  defaultMode?: string; // mode slug, or omit for all-modes
  defaultSort?: string; // metric key to rank by
};

export const REPORT_PRESETS: ReportPreset[] = [
  {
    id: "slaying-core",
    name: "Slaying Core",
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
];

export const TEAM_PRESETS: ReportPreset[] = [
  {
    id: "team-results",
    entity: "teams",
    name: "Results",
    metrics: [
      "map_win_rate",
      "series_win_rate",
      "decider_win_rate",
      "kill_diff_per_map",
      "slay_balance",
    ],
    defaultSort: "map_win_rate",
  },
];

/** The presets offered for a row entity. */
export function presetsFor(entity: "players" | "teams"): ReportPreset[] {
  return entity === "teams" ? TEAM_PRESETS : REPORT_PRESETS;
}

export function presetById(
  id: string,
  entity: "players" | "teams" = "players",
): ReportPreset | undefined {
  return presetsFor(entity).find((p) => p.id === id);
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
