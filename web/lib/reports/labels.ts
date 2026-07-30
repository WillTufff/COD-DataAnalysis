// Mode display names, shared by the on-screen cohort labels and the export
// matrix so a downloaded file can never disagree with the table about what a
// mode is called.

export const MODE_LABELS: Record<string, string> = {
  hardpoint: "Hardpoint",
  "search-and-destroy": "Search & Destroy",
  control: "Control",
  "capture-the-flag": "Capture the Flag",
  uplink: "Uplink",
};

export function modeLabel(slug: string | undefined): string {
  if (slug === undefined) return "All modes combined";
  return MODE_LABELS[slug] ?? slug;
}
