import { describe, expect, it } from "vitest";
import type { ReportColumn, ReportRow } from "@/lib/analytics";
import { MAX_EXPORT_ROWS, buildExportMatrix, cohortSlug } from "./export";
import type { ResolvedReport } from "./resolve";

const RUN = { model: "metric_layer", version: "2.1.0" };

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
    mapsFloor: 8,
    minMaps: 8,
    where: [],
    top: null,
    filters: { minMaps: 8, where: [], top: null },
    sort: "kd",
    dir: "desc",
    view: "value",
    defaultSortKey: "kd",
    defaultDir: "desc",
    query: { metrics: ["kd"], mapsMetrics: ["kd"] },
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
  formula: "sum(kills) / max(sum(deaths), 1)",
  note: null,
};

function row(overrides: Partial<ReportRow> = {}): ReportRow {
  return {
    playerId: 1,
    handle: "Scump",
    slug: "scump",
    year: 2018,
    title: "WWII",
    mode: "hardpoint",
    maps: 40,
    cells: {
      kd: { value: 1.13, denom: 100, z: 1.2, pctl: 0.9, qualified: true },
    },
    ...overrides,
  };
}

describe("buildExportMatrix", () => {
  it("leads with player and season, and names the mode only in the meta", () => {
    const all = buildExportMatrix(resolved(), [kdColumn], [row({ mode: null })], RUN);
    expect(all.headers).toEqual(["Player", "Season", "K/D"]);
    expect(all.rows[0]).toEqual(["Scump", "2018 WWII", 1.13]);
    expect(all.meta.cohort.mode).toBe("all");

    const one = buildExportMatrix(
      resolved({ modeSlug: "hardpoint" }),
      [kdColumn],
      [row()],
      RUN,
    );
    expect(one.headers).toEqual(["Player", "Season", "K/D"]);
    expect(one.meta.cohort.mode).toBe("hardpoint");
  });

  it("writes a missing cell as null", () => {
    const m = buildExportMatrix(
      resolved(),
      [kdColumn],
      [row({ mode: null, cells: {} })],
      RUN,
    );
    expect(m.rows[0]).toEqual(["Scump", "2018 WWII", null]);
  });

  it("caps rows at MAX_EXPORT_ROWS and records the truncation", () => {
    const many = Array.from({ length: MAX_EXPORT_ROWS + 1 }, (_, i) =>
      row({ playerId: i }),
    );
    const m = buildExportMatrix(resolved(), [kdColumn], many, RUN);
    expect(m.rows).toHaveLength(MAX_EXPORT_ROWS);
    expect(m.meta.truncated).toBe(true);
    expect(m.meta.rowCount).toBe(MAX_EXPORT_ROWS);
  });

  it("records empty filters as 'all' in the cohort meta", () => {
    const m = buildExportMatrix(resolved(), [kdColumn], [row()], RUN);
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
