import { describe, expect, it } from "vitest";
import type { ReportRow } from "@/lib/analytics";
import {
  applyResultFilters,
  filterReportRows,
  parseMinMaps,
  parseTop,
  parseView,
  parseWhere,
  serializeWhere,
  sortReportRows,
} from "./rows";

type Cell = ReportRow["cells"][string];

function cell(value: number, pctl: number | null, qualified = true): Cell {
  return { value, denom: 50, z: pctl === null ? null : pctl - 0.5, pctl, qualified };
}

function row(
  handle: string,
  year: number,
  cells: Record<string, Cell>,
  maps: number | null = 20,
): ReportRow {
  return {
    playerId: handle.length,
    handle,
    slug: handle.toLowerCase(),
    year,
    title: "T",
    mode: null,
    maps,
    cells,
  };
}

// Two seasons: A has the higher raw value in a weak year, B the higher
// percentile in a strong one. Value and percentile order them differently.
const A = row("Alpha", 2014, { kd: cell(1.4, 0.7), dmg: cell(90, 0.9, false) });
const B = row("Bravo", 2023, { kd: cell(1.2, 0.95), dmg: cell(80, 0.2) });
const C = row("Charlie", 2023, { dmg: cell(100, 0.99) }, 5);
const KEYS = ["kd", "dmg"];

describe("parseView", () => {
  it("reads pctl and z, and treats anything else as the value", () => {
    expect(parseView({ view: "pctl" })).toBe("pctl");
    expect(parseView({ view: "z" })).toBe("z");
    expect(parseView({ view: "value" })).toBe("value");
    expect(parseView({ view: "nope" })).toBe("pctl");
    expect(parseView({})).toBe("pctl");
  });
});

describe("parseMinMaps", () => {
  it("defaults to the floor, or any when a player or team is picked", () => {
    expect(parseMinMaps({}, 8, false)).toBe(8);
    expect(parseMinMaps({}, 8, true)).toBe(0);
    expect(parseMinMaps({ minmaps: "30" }, 8, true)).toBe(30);
    expect(parseMinMaps({ minmaps: "0" }, 8, false)).toBe(0);
  });

  it("reads the legacy small-samples flag as any, and ignores junk", () => {
    expect(parseMinMaps({ all: "1" }, 8, false)).toBe(0);
    expect(parseMinMaps({ minmaps: "-3" }, 8, false)).toBe(8);
    expect(parseMinMaps({ minmaps: "2.5" }, 8, false)).toBe(8);
    expect(parseMinMaps({ minmaps: "99999" }, 8, false)).toBe(8);
  });
});

describe("parseTop", () => {
  it("reads a positive count and nothing else", () => {
    expect(parseTop({ top: "25" })).toBe(25);
    expect(parseTop({ top: "0" })).toBeNull();
    expect(parseTop({ top: "ten" })).toBeNull();
    expect(parseTop({})).toBeNull();
  });
});

describe("parseWhere", () => {
  it("reads metric:field:op:n and drops what does not parse", () => {
    const where = parseWhere(
      { where: "kd:value:gte:1.2,dmg:pctl:lte:50,kd:bogus:gte:1,kd:value:eq:1,kd:value:gte:,x:value:gte:1" },
      KEYS,
    );
    expect(where).toEqual([
      { metric: "kd", field: "value", op: "gte", n: 1.2 },
      { metric: "dmg", field: "pctl", op: "lte", n: 50 },
    ]);
  });

  it("keeps the last of two thresholds with the same metric, field and op", () => {
    expect(parseWhere({ where: "kd:value:gte:1,kd:value:gte:2" }, KEYS)).toEqual([
      { metric: "kd", field: "value", op: "gte", n: 2 },
    ]);
  });

  it("round-trips through serializeWhere", () => {
    const where = parseWhere({ where: "kd:z:gte:-0.5,dmg:value:lte:90" }, KEYS);
    expect(serializeWhere(where)).toBe("kd:z:gte:-0.5,dmg:value:lte:90");
    expect(serializeWhere([])).toBeNull();
  });
});

describe("filterReportRows", () => {
  it("hides rows under min maps whatever the sort column", () => {
    expect(filterReportRows([A, B, C], { minMaps: 8, where: [] })).toEqual([A, B]);
    expect(filterReportRows([A, B, C], { minMaps: 0, where: [] })).toHaveLength(3);
  });

  it("treats an unknown maps count as zero", () => {
    const D = row("Delta", 2023, { kd: cell(1, 0.5) }, null);
    expect(filterReportRows([D], { minMaps: 1, where: [] })).toEqual([]);
    expect(filterReportRows([D], { minMaps: 0, where: [] })).toEqual([D]);
  });

  it("applies thresholds on the named field, and fails an absent cell", () => {
    const kdAtLeast = (n: number) => [
      { metric: "kd", field: "value" as const, op: "gte" as const, n },
    ];
    expect(filterReportRows([A, B, C], { minMaps: 0, where: kdAtLeast(1.3) })).toEqual([A]);
    const pctl90 = [{ metric: "kd", field: "pctl" as const, op: "gte" as const, n: 90 }];
    expect(filterReportRows([A, B, C], { minMaps: 0, where: pctl90 })).toEqual([B]);
  });

  it("reads a stored percentile as a whole number without float drift", () => {
    const E = row("Echo", 2023, { kd: cell(1, 0.9) });
    const at90 = (op: "gte" | "lte") => [{ metric: "kd", field: "pctl" as const, op, n: 90 }];
    expect(filterReportRows([E], { minMaps: 0, where: at90("lte") })).toEqual([E]);
    expect(filterReportRows([E], { minMaps: 0, where: at90("gte") })).toEqual([E]);
  });
});

describe("applyResultFilters", () => {
  it("cuts top N after the sort, so a new sort re-cuts it", () => {
    const f = { minMaps: 0, where: [], top: 1 };
    const byValue = applyResultFilters([A, B, C], f, "kd", "desc", "value", KEYS);
    expect(byValue.map((r) => r.handle)).toEqual(["Alpha"]);
    const byPctl = applyResultFilters([A, B, C], f, "kd", "desc", "pctl", KEYS);
    expect(byPctl.map((r) => r.handle)).toEqual(["Bravo"]);
  });

  it("filters before cutting, so top N counts only surviving rows", () => {
    const f = { minMaps: 8, where: [], top: 2 };
    const out = applyResultFilters([A, B, C], f, "dmg", "desc", "value", KEYS);
    expect(out.map((r) => r.handle)).toEqual(["Alpha", "Bravo"]);
  });
});

describe("sortReportRows", () => {
  it("sorts on the field the view shows", () => {
    const byValue = sortReportRows([A, B], "kd", "desc", "value", KEYS);
    expect(byValue.map((r) => r.handle)).toEqual(["Alpha", "Bravo"]);
    const byPctl = sortReportRows([A, B], "kd", "desc", "pctl", KEYS);
    expect(byPctl.map((r) => r.handle)).toEqual(["Bravo", "Alpha"]);
  });

  it("sinks absent readings in both directions", () => {
    for (const dir of ["asc", "desc"] as const) {
      const out = sortReportRows([C, A, B], "kd", dir, "value", KEYS);
      expect(out[2].handle).toBe("Charlie");
    }
  });

  it("sorts by name when the sort is not a column", () => {
    const out = sortReportRows([C, A, B], "player", "asc", "value", KEYS);
    expect(out.map((r) => r.handle)).toEqual(["Alpha", "Bravo", "Charlie"]);
  });
});
