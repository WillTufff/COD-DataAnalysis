import { describe, expect, it } from "vitest";
import {
  NO_CONTENT,
  STAGES,
  STAGE_VALUES,
  contentParts,
  contentSlug,
  dateRangeLabel,
  hasContent,
  parseContent,
  parseDay,
} from "./content";

describe("parseContent", () => {
  it("reads nothing from a bare URL", () => {
    expect(parseContent({})).toEqual(NO_CONTENT);
    expect(hasContent(parseContent({}))).toBe(false);
  });

  it("keeps lan and online and drops anything else", () => {
    expect(parseContent({ venue: "lan" }).venue).toBe("lan");
    expect(parseContent({ venue: "online" }).venue).toBe("online");
    expect(parseContent({ venue: "offline" }).venue).toBeNull();
    expect(hasContent(parseContent({ venue: "online" }))).toBe(true);
  });

  it("keeps each stage and drops anything else", () => {
    for (const stage of ["league", "event", "group", "bracket", "final"]) {
      expect(parseContent({ stage }).stage).toBe(stage);
    }
    expect(parseContent({ stage: "playoffs" }).stage).toBeNull();
    expect(hasContent(parseContent({ stage: "league" }))).toBe(true);
  });

  it("keeps only values series.stage can hold", () => {
    // The CHECK constraint in db/migrations/0041_series_stage.sql.
    const column = new Set(["league", "group", "bracket", "final"]);
    for (const stage of STAGES) {
      for (const v of STAGE_VALUES[stage]) expect(column.has(v)).toBe(true);
    }
  });

  it("keeps tier 1 and 2 and drops anything else", () => {
    expect(parseContent({ tier: "1" }).tier).toBe("1");
    expect(parseContent({ tier: "2" }).tier).toBe("2");
    expect(parseContent({ tier: "0" }).tier).toBeNull();
    expect(parseContent({ tier: "premier" }).tier).toBeNull();
  });

  it("slugs and de-duplicates map picks, keeping unknown ones", () => {
    expect(parseContent({ map: "Raid, raid ,Hacienda,,nowhere" }).maps).toEqual([
      "raid",
      "hacienda",
      "nowhere",
    ]);
  });

  it("drops a day that is not a real date and orders a reversed range", () => {
    expect(parseContent({ from: "2024-02-30" }).from).toBeNull();
    expect(parseContent({ from: "24-01-01" }).from).toBeNull();
    const r = parseContent({ from: "2024-06-30", to: "2024-01-05" });
    expect([r.from, r.to]).toEqual(["2024-01-05", "2024-06-30"]);
  });
});

describe("labels", () => {
  it("names what each filter keeps", () => {
    const c = {
      tier: "1" as const,
      venue: "lan" as const,
      stage: "bracket" as const,
      maps: ["raid"],
      from: "2024-01-05",
      to: null,
    };
    expect(contentParts(c, new Map([["raid", "Raid"]]))).toEqual([
      "tier 1 events",
      "lan only",
      "brackets",
      "Raid",
      "from 2024-01-05",
    ]);
    expect(dateRangeLabel("2024-01-05", "2024-01-05")).toBe("2024-01-05");
    expect(dateRangeLabel(null, "2024-06-30")).toBe("to 2024-06-30");
  });

  it("builds a filename part, empty with no filter", () => {
    expect(contentSlug(NO_CONTENT)).toBe("");
    expect(
      contentSlug({
        tier: "2",
        venue: "online",
        stage: "league",
        maps: ["a", "b", "c", "d"],
        from: null,
        to: "2019-08-18",
      }),
    ).toBe("-tier2-online-league-4-maps-start-to-2019-08-18");
  });
});

describe("parseDay", () => {
  it("accepts a leap day only in a leap year", () => {
    expect(parseDay("2024-02-29")).toBe("2024-02-29");
    expect(parseDay("2023-02-29")).toBeNull();
  });
});
