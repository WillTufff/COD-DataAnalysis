import { describe, expect, it } from "vitest";
import { ordinal } from "./ordinal";

describe("ordinal", () => {
  it("suffixes by the last digit, with the teens as th", () => {
    expect([0, 1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 100, 101, 111].map(ordinal)).toEqual([
      "0th", "1st", "2nd", "3rd", "4th", "11th", "12th", "13th",
      "21st", "22nd", "23rd", "100th", "101st", "111th",
    ]);
  });
});
