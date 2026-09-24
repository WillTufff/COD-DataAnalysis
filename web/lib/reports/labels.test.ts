import { describe, expect, it } from "vitest";
import { categoryLabel } from "./labels";

describe("categoryLabel", () => {
  it("names the categories the catalog publishes", () => {
    expect(categoryLabel("snd")).toBe("Search & Destroy");
    expect(categoryLabel("objective")).toBe("Objective");
    expect(categoryLabel("domination")).toBe("Domination");
  });

  it("capitalises a slug it has no name for", () => {
    expect(categoryLabel("kill_feed")).toBe("Kill feed");
  });
});
