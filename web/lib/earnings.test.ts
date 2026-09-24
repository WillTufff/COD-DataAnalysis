import { describe, expect, it } from "vitest";
import {
  formatMoney,
  parseEarningsByYear,
  sumPrizeBySeason,
  type PrizePlacement,
} from "./earnings";

describe("parseEarningsByYear", () => {
  it("orders years and fills the gaps with zero", () => {
    expect(parseEarningsByYear({ "2017": 1000, "2015": 27650 })).toEqual([
      { year: 2015, amount: 27650 },
      { year: 2016, amount: 0 },
      { year: 2017, amount: 1000 },
    ]);
  });

  it("returns nothing for null or a non-object", () => {
    expect(parseEarningsByYear(null)).toEqual([]);
    expect(parseEarningsByYear([1, 2])).toEqual([]);
    expect(parseEarningsByYear({})).toEqual([]);
  });
});

describe("sumPrizeBySeason", () => {
  // One franchise under two brands. Each brand is its own team row, so its
  // placements carry its own money and neither inherits the other's.
  const atlantaFaze: PrizePlacement[] = [
    { seasonId: 20, year: 2024, title: "MWIII", league: "CDL", prize: 400000 },
    { seasonId: 20, year: 2024, title: "MWIII", league: "CDL", prize: 70000 },
    { seasonId: 21, year: 2025, title: "BO6", league: "CDL", prize: 505000 },
    { seasonId: 21, year: 2025, title: "BO6", league: "CDL", prize: null },
  ];
  const fazeVegas: PrizePlacement[] = [
    { seasonId: 22, year: 2026, title: "BO7", league: "CDL", prize: 1064000 },
  ];

  it("gives each brand of a rebranded org its own money", () => {
    const atl = sumPrizeBySeason(atlantaFaze);
    const vegas = sumPrizeBySeason(fazeVegas);
    expect(atl.map((s) => [s.year, s.prize])).toEqual([
      [2025, 505000],
      [2024, 470000],
    ]);
    expect(vegas.map((s) => [s.year, s.prize])).toEqual([[2026, 1064000]]);
  });

  it("counts unpaid events without adding to the prize", () => {
    const [bo6] = sumPrizeBySeason(atlantaFaze);
    expect(bo6).toMatchObject({ events: 2, paidEvents: 1, prize: 505000 });
  });
});

describe("formatMoney", () => {
  it("compacts by magnitude", () => {
    expect(formatMoney(2186194)).toBe("$2.19M");
    expect(formatMoney(437500)).toBe("$438K");
    expect(formatMoney(2500)).toBe("$2.5K");
    expect(formatMoney(950)).toBe("$950");
  });
});
