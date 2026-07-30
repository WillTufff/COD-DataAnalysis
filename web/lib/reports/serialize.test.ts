import { describe, expect, it } from "vitest";
import type { ExportMatrix } from "./export";
import { toCsv, toJson, toXml } from "./serialize";

function matrix(overrides: Partial<ExportMatrix> = {}): ExportMatrix {
  return {
    headers: ["Player", "Season", "K/D"],
    rows: [
      ["Scump", "2018 WWII", 1.13],
      ["Céz", "2018 WWII", null],
    ],
    columns: [
      {
        key: "kd",
        label: "K/D",
        unit: "ratio",
        higherIsBetter: true,
        denomKind: "maps",
        minDenom: 8,
      },
    ],
    meta: {
      generatedAt: "2026-07-30T12:00:00.000Z",
      entity: "players",
      run: { model: "metric_layer", version: "2.1.0" },
      cohort: { seasons: [2018], mode: "hardpoint", players: "all", teams: "all" },
      sort: "kd",
      dir: "desc",
      qualifiedOnly: true,
      detail: false,
      rowCount: 2,
      truncated: false,
    },
    ...overrides,
  };
}

describe("toCsv", () => {
  it("starts with a BOM so Excel decodes UTF-8 handles", () => {
    expect(toCsv(matrix()).startsWith("﻿")).toBe(true);
  });

  it("uses CRLF line endings and one line per row plus the header", () => {
    const lines = toCsv(matrix()).split("\r\n");
    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe("﻿Player,Season,K/D");
  });

  it("renders null cells as empty fields, not the string 'null'", () => {
    expect(toCsv(matrix()).split("\r\n")[2]).toBe("Céz,2018 WWII,");
  });

  it("quotes fields containing commas, quotes, or newlines, doubling quotes", () => {
    const m = matrix({
      rows: [['a,b', 'say "hi"', 1], ["line\nbreak", "plain", 2]],
    });
    const [, r1, r2] = toCsv(m).split("\r\n");
    expect(r1).toBe('"a,b","say ""hi""",1');
    // The embedded \n is inside quotes, so the row splits only on CRLF.
    expect(r2).toBe('"line\nbreak",plain,2');
  });
});

describe("toJson", () => {
  it("round-trips the matrix with meta, columns, headers, and rows", () => {
    const parsed = JSON.parse(toJson(matrix()));
    expect(parsed.meta.run).toEqual({ model: "metric_layer", version: "2.1.0" });
    expect(parsed.headers).toEqual(["Player", "Season", "K/D"]);
    expect(parsed.columns[0].key).toBe("kd");
    expect(parsed.rows[1]).toEqual(["Céz", "2018 WWII", null]);
  });
});

describe("toXml", () => {
  it("escapes markup in cell values and attributes", () => {
    const m = matrix({
      headers: ["Player", "Season", 'A<&>" col'],
      rows: [['<b>&"', "2018 WWII", 1]],
    });
    const xml = toXml(m);
    expect(xml).toContain("&lt;b&gt;&amp;&quot;");
    expect(xml).not.toMatch(/<cell[^>]*><b>/);
  });

  it("aligns cell field attributes to the headers", () => {
    const xml = toXml(matrix());
    expect(xml).toContain('<cell field="Player">Scump</cell>');
    expect(xml).toContain('<cell field="K/D">1.13</cell>');
  });

  it("marks truncation on the meta element only when it happened", () => {
    expect(toXml(matrix())).not.toContain("truncated=");
    const cut = matrix();
    cut.meta = { ...cut.meta, truncated: true };
    expect(toXml(cut)).toContain('truncated="true"');
  });
});
