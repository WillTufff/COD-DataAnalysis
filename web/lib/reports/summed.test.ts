import { describe, expect, it } from "vitest";
import {
  type AggSpec,
  type SeasonTotals,
  coversModes,
  evalSpec,
  summedRows,
  zAndPctl,
} from "./summed";

const MAPS: [string, number][] = [["@maps", 1]];

function spec(overrides: Partial<AggSpec>): AggSpec {
  return {
    num: [],
    den: null,
    denom: MAPS,
    per: 1,
    den_floor: 0,
    complement: false,
    ...overrides,
  };
}

const totals =
  (t: Record<string, number>) =>
  (k: string): number =>
    t[k] ?? 0;

describe("evalSpec", () => {
  it("divides once, per unit of time", () => {
    const kills_p10 = spec({ num: [["kills", 1]], den: [["@duration_s", 1]], per: 600 });
    expect(evalSpec(kills_p10, totals({ kills: 40, "@duration_s": 1200, "@maps": 2 }))).toEqual({
      value: 20,
      denom: 2,
    });
  });

  it("floors the divisor where the metric layer does (K/D over zero deaths)", () => {
    const kd = spec({ num: [["kills", 1]], den: [["deaths", 1]], den_floor: 1 });
    expect(evalSpec(kd, totals({ kills: 7, "@maps": 1 }))?.value).toBe(7);
  });

  it("publishes nothing over a zero divisor or a zero sample", () => {
    const rate = spec({ num: [["fb", 1]], den: [["rounds", 1]], denom: [["rounds", 1]] });
    expect(evalSpec(rate, totals({ fb: 3 }))).toBeNull();
    const total = spec({ num: [["aces", 1]], denom: [["rounds", 1]] });
    expect(evalSpec(total, totals({ aces: 3 }))).toBeNull();
  });

  it("takes the complement after dividing", () => {
    const traded = spec({
      num: [["clean", 1]],
      den: [["kills", 1]],
      denom: [["kills", 1]],
      complement: true,
    });
    expect(evalSpec(traded, totals({ clean: 150, kills: 200 }))?.value).toBeCloseTo(0.25);
  });
});

describe("zAndPctl", () => {
  it("publishes percentiles below 15 qualified and z-scores from 15 up", () => {
    const small = new Map([1, 2, 3].map((v) => [v, v]));
    const scored = zAndPctl(small, [1, 2, 3]);
    expect(scored.get(2)).toEqual({ z: null, pctl: 2 / 3 });
    const big = new Map(Array.from({ length: 15 }, (_, i) => [i, i]));
    expect(zAndPctl(big, [...big.keys()]).get(7)?.z).toBeCloseTo(0);
  });

  it("scores an unqualified value against the qualified field only", () => {
    const values = new Map([
      ["a", 1],
      ["b", 2],
      ["c", 10],
    ]);
    expect(zAndPctl(values, ["a", "b"]).get("c")?.pctl).toBe(1);
  });

  it("returns nothing for a field with no spread", () => {
    expect(zAndPctl(new Map([[1, 5], [2, 5]]), [1, 2]).size).toBe(0);
  });
});

describe("coversModes", () => {
  it("admits a mode metric only when every picked mode is its own", () => {
    expect(coversModes(["hardpoint"], ["hardpoint"])).toBe(true);
    expect(coversModes(["hardpoint"], ["hardpoint", "control"])).toBe(false);
    expect(coversModes(["hardpoint"], [])).toBe(false);
    expect(coversModes(["__all__"], ["hardpoint", "control"])).toBe(true);
  });
});

describe("summedRows", () => {
  const kd = {
    key: "kd",
    spec: spec({ num: [["kills", 1]], den: [["deaths", 1]], den_floor: 1 }),
    titles: ["MWII", "MWIII"],
    minDenom: 8,
  };
  const season = (id: number, year: number, title: string, t: Record<string, number>) =>
    ({ id, year, title, totals: t }) as SeasonTotals;

  it("sums maps across seasons for a span row, not season values", () => {
    const rows = summedRows(
      [
        season(1, 2023, "MWII", { "@maps": 10, kills: 200, deaths: 100 }),
        season(1, 2024, "MWIII", { "@maps": 30, kills: 300, deaths: 300 }),
      ],
      [kd],
      true,
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].cells.kd.value).toBeCloseTo(500 / 400);
    expect(rows[0].maps).toBe(40);
    expect(rows[0].years).toEqual([2023, 2024]);
  });

  it("leaves out seasons whose title does not track the metric", () => {
    const rows = summedRows(
      [
        season(1, 2022, "VG", { "@maps": 50, kills: 999, deaths: 1 }),
        season(1, 2023, "MWII", { "@maps": 10, kills: 200, deaths: 100 }),
      ],
      [kd],
      true,
    );
    expect(rows[0].cells.kd.value).toBe(2);
    expect(rows[0].cells.kd.denom).toBe(10);
  });

  it("scores per season, or once over a span", () => {
    const input = [
      season(1, 2023, "MWII", { "@maps": 10, kills: 10, deaths: 10 }),
      season(2, 2023, "MWII", { "@maps": 10, kills: 20, deaths: 10 }),
      season(1, 2024, "MWIII", { "@maps": 10, kills: 30, deaths: 10 }),
      season(2, 2024, "MWIII", { "@maps": 10, kills: 5, deaths: 10 }),
    ];
    const perSeason = summedRows(input, [kd], false);
    const p1 = perSeason.find((r) => r.id === 1 && r.years[0] === 2024);
    expect(p1?.cells.kd.pctl).toBe(1);
    const span = summedRows(input, [kd], true);
    expect(span.find((r) => r.id === 1)?.cells.kd.pctl).toBe(1);
    expect(span.find((r) => r.id === 2)?.cells.kd.pctl).toBe(0.5);
  });
});
