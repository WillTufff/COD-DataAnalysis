// Display labels for the finding kinds the generator emits. One list, because
// three pages render findings and the copies drifted: the overview only knew
// the five original kinds, so the nine metric-layer kinds added later leaked
// their raw database keys onto the front page.
//
// Keep in step with the `kind` values written by analytics/src/cdlhub_analytics
// /insights*.py. `kindLabel` degrades gracefully rather than showing snake_case,
// so a new kind shipped before this list is updated still reads as English.

export const INSIGHT_KIND_LABELS: Record<string, string> = {
  outlier: "Outlier",
  trend: "Trend",
  milestone: "Milestone",
  era_context: "Era context",
  h2h_edge: "Head-to-head",
  what_wins: "What wins maps",
  rating_top: "Top rated",
  model_null: "Model null",
  mode_null: "Mode null",
  series_dynamics: "Series dynamics",
  intangible_outlier: "Split profile",
  profile_extreme: "League best",
  clutch_milestone: "Clutch record",
  trade_asymmetry: "Trade economy",
  meta_shift: "Meta shift",
  team_style: "Team style",
};

/** Human label for a finding kind; unknown kinds are de-snaked, never raw. */
export function kindLabel(kind: string): string {
  return INSIGHT_KIND_LABELS[kind] ?? kind.replace(/_/g, " ");
}
