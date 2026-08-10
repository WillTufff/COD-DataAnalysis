import { describe, expect, it } from "vitest";
import type { ReportColumn, ReportRow } from "@/lib/analytics";
import { MAX_EXPORT_ROWS, buildExportMatrix, cohortSlug } from "./export";
import type { ResolvedReport } from "./resolve";

const RUN = { model: "metric_layer", version: "2.1.0" };

// The mode catalog the site reads from `game_modes`, as a fixture: the export's
// Mode column is a lookup against it, and a slug it lacks must survive as itself.
const MODES = {
  order: ["hardpoint", "search-and-destroy", "overload"],
  names: {
    hardpoint: "Hardpoint",
    "search-and-destroy": "Search & Destroy",
    overload: "Overload",
  },
};

function resolved(overrides: Partial<ResolvedReport> = {}): ResolvedReport {
  return {
    entity: "players",
    selected: ["kd"],
    selectedEntries: [],
    scope: { years: [], seasons: [], modes: [], allModes: false },
    rankedScope: { years: [], seasons: [], modes: [], allModes: false },
    years: [],
    playerSlugs: [],
    teamSlugs: [],
    modeSlug: undefined,
    qualifiedOnly: true,
    sort: "kd",
    dir: "desc",
    defaultSortKey: "kd",
    defaultDir: "desc",
    query: { metrics: ["kd"], qualifiedOnly: true, sort: "kd", dir: "desc" },
    ...overrides,
  };
}

const kdColumn: ReportColumn = {
  key: "kd",
  label: "K/D",
  unit: "ratio",
  higherIsBetter: true,
  denomKind: "maps",
  minDenom: 8,
};

function row(overrides: Partial<ReportRow> = {}): ReportRow {
  return {
    playerId: 1,
    handle: "Scump",
    slug: "scump",
    year: 2018,
    title: "WWII",
    mode: "hardpoint",
    cells: {
      kd: { value: 1.13, denom: 100, z: 1.2, pctl: 0.9, qualified: true },
    },
    ...overrides,
  };
}

describe("buildExportMatrix", () => {
  it("adds a Mode column only for all-modes cohorts, with display labels", () => {
    const all = buildExportMatrix(resolved(), [kdColumn], [row()], RUN, MODES);
    expect(all.headers).toEqual(["Player", "Season", "Mode", "K/D"]);
    expect(all.rows[0]).toEqual(["Scump", "2018 WWII", "Hardpoint", 1.13]);

    const one = buildExportMatrix(
      resolved({ modeSlug: "hardpoint" }),
      [kdColumn],
      [row()],
      RUN,
      MODES,
    );
    expect(one.headers).toEqual(["Player", "Season", "K/D"]);
  });

  it("labels an all-modes row 'All' and a missing cell null", () => {
    const m = buildExportMatrix(
      resolved(),
      [kdColumn],
      [row({ mode: null, cells: {} })],
      RUN,
      MODES,
    );
    expect(m.rows[0]).toEqual(["Scump", "2018 WWII", "All", null]);
  });

  it("caps rows at MAX_EXPORT_ROWS and records the truncation", () => {
    const many = Array.from({ length: MAX_EXPORT_ROWS + 1 }, (_, i) =>
      row({ playerId: i }),
    );
    const m = buildExportMatrix(resolved(), [kdColumn], many, RUN, MODES);
    expect(m.rows).toHaveLength(MAX_EXPORT_ROWS);
    expect(m.meta.truncated).toBe(true);
    expect(m.meta.rowCount).toBe(MAX_EXPORT_ROWS);
  });

  it("records empty filters as 'all' in the cohort meta", () => {
    const m = buildExportMatrix(resolved(), [kdColumn], [row()], RUN, MODES);
    expect(m.meta.cohort).toEqual({
      seasons: "all",
      mode: "all",
      players: "all",
      teams: "all",
    });
    const picked = buildExportMatrix(
      resolved({ years: [2018], playerSlugs: ["scump"] }),
      [kdColumn],
      [row()],
      RUN,
      MODES,
    );
    expect(picked.meta.cohort.seasons).toEqual([2018]);
    expect(picked.meta.cohort.players).toEqual(["scump"]);
  });
});

describe("cohortSlug", () => {
  it("names the default report all-modes across all seasons", () => {
    expect(cohortSlug(resolved())).toBe("all-modes-all-seasons");
  });

  it("names up to three picks and counts past that", () => {
    expect(
      cohortSlug(resolved({ modeSlug: "hardpoint", years: [2018], playerSlugs: ["a", "b"] })),
    ).toBe("hardpoint-2018-a-b");
    expect(
      cohortSlug(resolved({ playerSlugs: ["a", "b", "c", "d"] })),
    ).toBe("all-modes-all-seasons-4-players");
  });

  it("prefixes team reports and sanitises to filesystem-safe characters", () => {
    const slug = cohortSlug(
      resolved({ entity: "teams", teamSlugs: ["rise nation!"] }),
    );
    expect(slug).toBe("teams-all-modes-all-seasons-rise-nation-");
    expect(slug).toMatch(/^[a-z0-9-]+$/);
  });
});
