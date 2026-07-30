import { describe, expect, it, vi } from "vitest";
import type { MetricCatalogEntry } from "@/lib/analytics";
import {
  parseEntity,
  parseMetrics,
  parseYears,
  resolveReport,
  resolveReportForUrl,
} from "./resolve";
import { DEFAULT_PRESET, REPORT_PRESETS, sanitizePresetMetrics } from "./presets";

// The scope queries are the only database touch in resolveReport; everything
// else is deterministic. Mocking them keeps these tests runnable anywhere.
vi.mock("@/lib/analytics", () => ({
  getReportScope: vi.fn(async () => ({
    years: [2017, 2018, 2019],
    seasons: [
      { year: 2017, code: "IW", name: "Infinite Warfare" },
      { year: 2018, code: "WWII", name: "WWII" },
      { year: 2019, code: "BO4", name: "Black Ops 4" },
    ],
    modes: ["hardpoint", "search-and-destroy"],
    allModes: true,
  })),
  getTeamReportScope: vi.fn(async () => ({
    years: [2018],
    seasons: [{ year: 2018, code: "WWII", name: "WWII" }],
    modes: [],
    allModes: true,
  })),
}));

const slayingCore = REPORT_PRESETS.find((p) => p.id === DEFAULT_PRESET)!;

function entry(key: string, higher = true): MetricCatalogEntry {
  return {
    key,
    label: key,
    category: "slaying",
    tier: "gold",
    unit: "per 10 min",
    higher_is_better: higher,
    formula: key,
    denom_kind: "maps",
    min_denom: 8,
    sources: [],
    titles: ["WWII"],
    modes: ["hardpoint"],
    note: null,
  };
}

// A catalog that covers the default preset plus one extra column.
const CATALOG = [...slayingCore.metrics, "kd"].map((k) => entry(k));

describe("parseYears", () => {
  it("reads a CSV, dedupes, sorts, and tolerates the legacy single key", () => {
    expect(parseYears({ years: "2019, 2017,2019" })).toEqual([2017, 2019]);
    expect(parseYears({ year: "2018" })).toEqual([2018]);
    expect(parseYears({})).toEqual([]);
    expect(parseYears({ years: "abc" })).toEqual([]);
  });
});

describe("parseMetrics", () => {
  it("keeps order, dedupes, and tolerates the legacy single key", () => {
    expect(parseMetrics({ metrics: "b, a ,b" })).toEqual(["b", "a"]);
    expect(parseMetrics({ metric: "kd" })).toEqual(["kd"]);
    expect(parseMetrics({})).toEqual([]);
  });
});

describe("parseEntity", () => {
  it("is players unless the URL says teams", () => {
    expect(parseEntity({})).toBe("players");
    expect(parseEntity({ entity: "teams" })).toBe("teams");
    expect(parseEntity({ entity: "nonsense" })).toBe("players");
  });
});

describe("sanitizePresetMetrics", () => {
  it("drops keys the live catalog no longer publishes, keeping order", () => {
    const known = new Set(slayingCore.metrics.slice(1));
    expect(sanitizePresetMetrics(slayingCore, known)).toEqual(
      slayingCore.metrics.slice(1),
    );
  });
});

describe("resolveReport", () => {
  it("falls back to the default preset on a bare URL, without marking it chosen", async () => {
    const r = await resolveReport(1, {}, CATALOG, {
      fallbackPreset: DEFAULT_PRESET,
    });
    expect(r.selected).toEqual(slayingCore.metrics);
    expect(r.sort).toBe(slayingCore.defaultSort);
    // The fallback seeds the report; only a URL-named preset is "active".
    expect(r.activePreset).toBeUndefined();
  });

  it("treats an explicitly empty metrics key as a cleared report, not a fallback", async () => {
    const r = await resolveReport(1, { metrics: "" }, CATALOG, {
      fallbackPreset: DEFAULT_PRESET,
    });
    expect(r.selected).toEqual([]);
  });

  it("lets explicit metrics override a named preset", async () => {
    const r = await resolveReport(
      1,
      { preset: DEFAULT_PRESET, metrics: "kd" },
      CATALOG,
    );
    expect(r.selected).toEqual(["kd"]);
    expect(r.activePreset).toBeUndefined();
  });

  it("marks a URL-named preset active and applies its columns", async () => {
    const r = await resolveReport(1, { preset: DEFAULT_PRESET }, CATALOG);
    expect(r.activePreset?.id).toBe(DEFAULT_PRESET);
    expect(r.selected).toEqual(slayingCore.metrics);
  });

  it("silently drops unknown metric keys from the URL", async () => {
    const r = await resolveReport(1, { metrics: "kd,retired_metric" }, CATALOG);
    expect(r.selected).toEqual(["kd"]);
  });

  it("falls back to the default sort when ?sort= names an unselected column", async () => {
    const r = await resolveReport(1, { metrics: "kd", sort: "not_a_column" }, CATALOG);
    expect(r.sort).toBe("kd");
    expect(r.dir).toBe("desc");
  });

  it("sorts ascending by default when lower is better", async () => {
    const catalog = [entry("deaths_p10", false)];
    const r = await resolveReport(1, { metrics: "deaths_p10" }, catalog);
    expect(r.dir).toBe("asc");
  });

  it("normalises picking every covered season to the unfiltered report", async () => {
    const all = await resolveReport(
      1,
      { metrics: "kd", years: "2017,2018,2019" },
      CATALOG,
    );
    expect(all.years).toEqual([]);
    const some = await resolveReport(1, { metrics: "kd", years: "2018" }, CATALOG);
    expect(some.years).toEqual([2018]);
  });

  it("drops years outside the selected columns' coverage", async () => {
    const r = await resolveReport(1, { metrics: "kd", years: "2016,2018" }, CATALOG);
    expect(r.years).toEqual([2018]);
  });

  it("shows every column on a bare team visit", async () => {
    const teamCatalog = [entry("map_win_rate"), entry("series_win_rate")];
    const r = await resolveReport(1, { entity: "teams" }, teamCatalog);
    expect(r.entity).toBe("teams");
    expect(r.selected).toEqual(["map_win_rate", "series_win_rate"]);
  });

  it("resolves nothing from a bare URL without a fallback preset", async () => {
    // Why the shared entry point below has to exist: a caller that forgets the
    // fallback resolves the landing URL to no columns at all, which the export
    // route turns into a 400 on the very report the page is showing.
    const r = await resolveReport(1, {}, CATALOG);
    expect(r.selected).toEqual([]);
  });

  it("dedupes and lowercases the player and team filters", async () => {
    const r = await resolveReport(
      1,
      { metrics: "kd", players: "Scump, scump ,formal", teams: "" },
      CATALOG,
    );
    expect(r.playerSlugs).toEqual(["scump", "formal"]);
    expect(r.teamSlugs).toEqual([]);
  });
});

// The page and the export route both go through this, so a bare visit and a
// bare download cannot resolve to different reports.
describe("resolveReportForUrl", () => {
  it("applies the landing preset to a bare player URL", async () => {
    const r = await resolveReportForUrl(1, {}, CATALOG);
    expect(r.selected).toEqual(slayingCore.metrics);
    expect(r.query.metrics).toEqual(slayingCore.metrics);
  });

  it("shows the whole catalog on a bare team URL", async () => {
    const teamCatalog = [entry("map_win_rate"), entry("series_win_rate")];
    const r = await resolveReportForUrl(1, { entity: "teams" }, teamCatalog);
    expect(r.selected).toEqual(["map_win_rate", "series_win_rate"]);
  });

  it("still honours explicit columns", async () => {
    const r = await resolveReportForUrl(1, { metrics: "kd" }, CATALOG);
    expect(r.selected).toEqual(["kd"]);
  });
});
