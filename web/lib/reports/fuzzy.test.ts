import { describe, expect, it } from "vitest";
import { foldName, fuzzyRank, matchScore } from "./fuzzy";

describe("foldName", () => {
  it("drops case, accents and punctuation", () => {
    expect(foldName("Céz")).toBe("cez");
    expect(foldName("Mr. Pap_1")).toBe("mrpap1");
  });
});

describe("matchScore", () => {
  it("ranks exact, prefix, substring, then in-order letters", () => {
    expect(matchScore("simp", "simp")).toBe(0);
    expect(matchScore("sim", "simp")).toBe(1);
    expect(matchScore("imp", "simp")).toBe(2);
    expect(matchScore("smp", "simp")).toBe(3);
  });

  it("forgives one typo from three letters and two from six", () => {
    expect(matchScore("scmup", "scump")).toBe(4);
    expect(matchScore("crimsxi", "crimsix")).not.toBeNull();
    expect(matchScore("cirmsxi", "crimsix")).not.toBeNull();
    expect(matchScore("xy", "simp")).toBeNull();
    expect(matchScore("qqq", "simp")).toBeNull();
  });

  it("judges a typo in a partial query against the name's start", () => {
    expect(matchScore("shttz", "shottzzy")).not.toBeNull();
  });
});

describe("fuzzyRank", () => {
  const people = [
    { name: "Scump", team: "OpTic Texas" },
    { name: "Shotzzy", team: "OpTic Texas" },
    { name: "Simp", team: "Atlanta FaZe" },
    { name: "Cellium", team: "Atlanta FaZe" },
  ];
  const fields = (p: (typeof people)[number]) => [p.name, p.team];

  it("puts a name match above a team match", () => {
    const out = fuzzyRank(people, "s", fields).map((p) => p.name);
    expect(out.slice(0, 3)).toEqual(["Scump", "Shotzzy", "Simp"]);
  });

  it("finds players by their team", () => {
    const out = fuzzyRank(people, "faze", fields).map((p) => p.name);
    expect(out).toEqual(["Simp", "Cellium"]);
  });

  it("returns everything for an empty query, in order", () => {
    expect(fuzzyRank(people, " ", fields)).toEqual(people);
  });
});
