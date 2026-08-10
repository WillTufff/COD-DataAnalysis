// Mode display names, shared by the on-screen cohort labels and the export
// matrix so a downloaded file can never disagree with the table about what a
// mode is called. The names come from `game_modes` via `lib/modes`; this module
// only pins the wording of the all-modes slice, which is not a row in that
// table.

export { type ModeCatalog, EMPTY_MODE_CATALOG, modeLabel } from "@/lib/modes";

export const ALL_MODES_LABEL = "All modes combined";
