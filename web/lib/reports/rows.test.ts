import { describe, expect, it } from "vitest";
import type { ReportRow } from "@/lib/analytics";
import { gateReportRows, parseView, sortReportRows } from "./rows";

type Cell = ReportRow["cells"][string];

function cell(value: number, pctl: number | null, qualified = true): Cell {
  return { value, denom: 50, z: pctl === null ? null : pctl - 0.5, pctl, qualified };
}

function row(handle: string, year: number, cells: Record<string, Cell>): ReportRow {
  return {
    playerId: handle.length,
    handle,
    slug: handle.toLowerCase(),
    year,
    title: "T",
    mode: null,
    cells,
  };
}

// Two seasons: A has the higher raw value in a weak year, B the higher
// percentile in a strong one. Value and percentile order them differently.
const A = row("Alpha", 2014, { kd: cell(1.4, 0.7), dmg: cell(90, 0.9, false) });
const B = row("Bravo", 2023, { kd: cell(1.2, 0.95), dmg: cell(80, 0.2) });
const C = row("Charlie", 2023, { dmg: cell(100, 0.99) });
const KEYS = ["kd", "dmg"];

describe("parseView", () => {
  it("reads pctl and z, and treats anything else as the value", () => {
    expect(parseView({ view: "pctl" })).toBe("pctl");
    expect(parseView({ view: "z" })).toBe("z");
    expect(parseView({ view: "nope" })).toBe("value");
    expect(parseView({})).toBe("value");
  });
});

describe("gateReportRows", () => {
  it("keeps only rows qualified in the sort column", () => {
    expect(gateReportRows([A, B, C], "kd", KEYS, true)).toEqual([A, B]);
    expect(gateReportRows([A, B, C], "dmg", KEYS, true)).toEqual([B, C]);
  });

  it("does nothing when inactive or sorted by name", () => {
    expect(gateReportRows([A, B, C], "kd", KEYS, false)).toHaveLength(3);
    expect(gateReportRows([A, B, C], "player", KEYS, true)).toHaveLength(3);
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
