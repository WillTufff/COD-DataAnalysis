// The normalised export matrix. Every download format — CSV, JSON, XML, XLSX —
// consumes this one shape, so the formats can never disagree about what a report
// contains. It is built from the same resolved query the page renders, so a file
// always matches the table it came from.

import { type ReportColumn, type ReportRow } from "@/lib/analytics";
import { type ModeCatalog, modeLabel } from "./labels";
import { type ResolvedReport } from "./resolve";

// A hard ceiling so a pathological request can't stream an unbounded file. The
// matrix records when it bit, so a truncated export is never silent.
export const MAX_EXPORT_ROWS = 10_000;

// Attribution travels with the file. These are derived season aggregates, never
// the underlying box scores, which is what the CDL-era source's licence allows
// to leave the site at all.
export const EXPORT_ATTRIBUTION = {
  derived: "Derived season aggregates. Not a redistribution of any source's box scores.",
  sources: [
    "Box scores 2017-2019: Activision Publishing (cwl-data), BSD-3-Clause.",
    "Box scores 2020-2026: data via Cito, carrying Breaking Point match data, used with attribution.",
    "Tournaments, placements, rosters, transfers, bios: Liquipedia, CC-BY-SA 3.0.",
  ],
} as const;

export type ExportMeta = {
  generatedAt: string;
  entity: "players" | "teams";
  attribution: typeof EXPORT_ATTRIBUTION;
  run: { model: string; version: string };
  cohort: {
    seasons: number[] | "all";
    mode: string;
    players: string[] | "all";
    teams: string[] | "all";
  };
  sort: string;
  dir: "asc" | "desc";
  qualifiedOnly: boolean;
  detail: boolean;
  rowCount: number;
  truncated: boolean;
};

export type ExportMatrix = {
  headers: string[];
  // Aligned to `headers`. Metric values stay numeric (Excel/JSON sort them as
  // numbers); absent cells are null; text columns (player, season, mode) are
  // strings.
  rows: (string | number | null)[][];
  columns: ReportColumn[];
  meta: ExportMeta;
};

/** A filesystem-safe cohort tag for the download filename, e.g. `hardpoint-2018`. */
export function cohortSlug(resolved: ResolvedReport): string {
  const entity = resolved.entity === "teams" ? "teams-" : "";
  const mode = resolved.modeSlug ?? "all-modes";
  const seasons =
    resolved.years.length > 0 ? resolved.years.join("-") : "all-seasons";
  // A filter names itself when it fits; past three picks it's just a count.
  const named = (slugs: string[], noun: string) =>
    slugs.length === 0
      ? ""
      : slugs.length <= 3
        ? `-${slugs.join("-")}`
        : `-${slugs.length}-${noun}`;
  const players = named(resolved.playerSlugs, "players");
  const teamsPart = named(resolved.teamSlugs, "teams");
  return `${entity}${mode}-${seasons}${teamsPart}${players}`
    .replace(/[^a-z0-9-]+/gi, "-")
    .toLowerCase();
}

/**
 * Flatten the report into headers + numeric rows + metadata. With `detail`, each
 * metric contributes its percentile and z-score columns alongside the value.
 * The fixed left columns mirror the on-screen table: player, season, and — only
 * for an all-modes cohort, where a player can appear per mode — the mode.
 */
export function buildExportMatrix(
  resolved: ResolvedReport,
  columns: ReportColumn[],
  rows: ReportRow[],
  run: { model: string; version: string },
  modeCatalog: ModeCatalog,
): ExportMatrix {
  const detail = false; // reserved for a future ?detail=1; value-only for v1
  const showMode = resolved.modeSlug === undefined;

  const headers: string[] = [
    resolved.entity === "teams" ? "Team" : "Player",
    "Season",
  ];
  if (showMode) headers.push("Mode");
  for (const c of columns) {
    headers.push(c.label);
    if (detail) headers.push(`${c.label} (pctl)`, `${c.label} (z)`);
  }

  const truncated = rows.length > MAX_EXPORT_ROWS;
  const used = truncated ? rows.slice(0, MAX_EXPORT_ROWS) : rows;

  const matrixRows: (string | number | null)[][] = used.map((r) => {
    const out: (string | number | null)[] = [r.handle, `${r.year} ${r.title}`];
    if (showMode) out.push(modeLabel(modeCatalog, r.mode, "All"));
    for (const c of columns) {
      const cell = r.cells[c.key];
      out.push(cell ? cell.value : null);
      if (detail) {
        out.push(cell?.pctl ?? null, cell?.z ?? null);
      }
    }
    return out;
  });

  return {
    headers,
    rows: matrixRows,
    columns,
    meta: {
      generatedAt: new Date().toISOString(),
      entity: resolved.entity,
      attribution: EXPORT_ATTRIBUTION,
      run,
      cohort: {
        seasons: resolved.years.length > 0 ? resolved.years : "all",
        mode: resolved.modeSlug ?? "all",
        players:
          resolved.playerSlugs.length > 0 ? resolved.playerSlugs : "all",
        teams: resolved.teamSlugs.length > 0 ? resolved.teamSlugs : "all",
      },
      sort: resolved.sort,
      dir: resolved.dir,
      qualifiedOnly: resolved.qualifiedOnly,
      detail,
      rowCount: matrixRows.length,
      truncated,
    },
  };
}
