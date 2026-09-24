// Mode display names, shared by the on-screen cohort labels and the export
// matrix so a downloaded file can never disagree with the table about what a
// mode is called. The names come from `game_modes` via `lib/modes`; this module
// only pins the wording of the all-modes slice, which is not a row in that
// table.

export { type ModeCatalog, EMPTY_MODE_CATALOG, modeLabel } from "@/lib/modes";

export const ALL_MODES_LABEL = "All modes combined";

const CATEGORY_LABELS: Record<string, string> = {
  team: "Team",
  slaying: "Slaying & engagement",
  discipline: "Discipline & survival",
  trades: "Trades & entries",
  advantage: "Man-advantage",
  clutch: "Clutch",
  objective: "Objective",
  hardpoint: "Hardpoint",
  snd: "Search & Destroy",
  control: "Control",
  ctf: "Capture the Flag",
  uplink: "Uplink",
  blitz: "Blitz",
  domination: "Domination",
  streaks: "Multikills & streaks",
  scorestreaks: "Scorestreaks",
};

/** A metric category's display name; an unlisted slug reads as itself, capitalised. */
export function categoryLabel(slug: string): string {
  return (
    CATEGORY_LABELS[slug] ??
    slug.replace(/[-_]+/g, " ").replace(/^./, (c) => c.toUpperCase())
  );
}
