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
  it("falls back to the default preset on a bare URL and marks it active", async () => {
    const r = await resolveReport(1, {}, CATALOG, {
      fallbackPreset: DEFAULT_PRESET,
    });
    expect(r.selected).toEqual(slayingCore.metrics);
    expect(r.sort).toBe(slayingCore.defaultSort);
    expect(r.activePreset?.id).toBe(DEFAULT_PRESET);
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

  it("defaults to the latest covered season, and reads years=all as every season", async () => {
    expect((await resolveReport(1, { metrics: "kd" }, CATALOG)).years).toEqual([2019]);
    expect((await resolveReport(1, { metrics: "kd", years: "all" }, CATALOG)).years).toEqual([]);
    expect((await resolveReport(1, { metrics: "kd", years: "" }, CATALOG)).years).toEqual([]);
    // A stale pick that no longer matches any covered season lands on the latest.
    expect((await resolveReport(1, { metrics: "kd", years: "2011" }, CATALOG)).years).toEqual([2019]);
  });

  it("drops years outside the selected columns' coverage", async () => {
    const r = await resolveReport(1, { metrics: "kd", years: "2016,2018" }, CATALOG);
    expect(r.years).toEqual([2018]);
  });

  it("shows every column on a bare team visit without a fallback preset", async () => {
    const teamCatalog = [entry("map_win_rate"), entry("series_win_rate")];
    const r = await resolveReport(1, { entity: "teams" }, teamCatalog);
    expect(r.entity).toBe("teams");
    expect(r.selected).toEqual(["map_win_rate", "series_win_rate"]);
  });

  it("resolves a team preset only on the team side", async () => {
    const teamCatalog = [entry("map_win_rate"), entry("series_win_rate")];
    const team = await resolveReport(1, { entity: "teams", preset: "team-results" }, teamCatalog);
    expect(team.activePreset?.id).toBe("team-results");
    expect(team.selected).toEqual(["map_win_rate", "series_win_rate"]);
    const player = await resolveReport(1, { preset: "team-results" }, teamCatalog);
    expect(player.activePreset).toBeUndefined();
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

describe("resolveReport result filters and view", () => {
  it("defaults min maps to the catalog's maps floor, and to any on a pick", async () => {
    const bare = await resolveReport(1, { metrics: "kd" }, CATALOG);
    expect(bare.mapsFloor).toBe(8);
    expect(bare.minMaps).toBe(8);
    expect((await resolveReport(1, { metrics: "kd", all: "1" }, CATALOG)).minMaps).toBe(0);
    expect((await resolveReport(1, { metrics: "kd", players: "scump" }, CATALOG)).minMaps).toBe(0);
    expect((await resolveReport(1, { metrics: "kd", teams: "optic" }, CATALOG)).minMaps).toBe(0);
    expect(
      (await resolveReport(1, { metrics: "kd", teams: "optic", minmaps: "20" }, CATALOG)).minMaps,
    ).toBe(20);
  });

  it("does not depend on the sort column", async () => {
    const a = await resolveReport(1, { metrics: "kd,kills_p10", sort: "kd" }, CATALOG);
    const b = await resolveReport(1, { metrics: "kd,kills_p10", sort: "player" }, CATALOG);
    expect(a.filters).toEqual(b.filters);
  });

  it("ignores a player filter on a team report when defaulting min maps", async () => {
    const teamCatalog = [entry("map_win_rate")];
    const r = await resolveReport(
      1,
      { entity: "teams", metrics: "map_win_rate", players: "scump" },
      teamCatalog,
    );
    expect(r.minMaps).toBe(8);
  });

  it("carries the maps-denominated keys into the query", async () => {
    const mixed = [entry("kd"), { ...entry("snd_fb"), denom_kind: "rounds", min_denom: 50 }];
    const r = await resolveReport(1, { metrics: "kd,snd_fb" }, mixed);
    expect(r.query.mapsMetrics).toEqual(["kd"]);
    expect(r.mapsFloor).toBe(8);
  });

  it("keeps thresholds only on columns on screen, and reads top", async () => {
    const r = await resolveReport(
      1,
      { metrics: "kd", where: "kd:value:gte:1.1,gone:value:gte:1", top: "10" },
      CATALOG,
    );
    expect(r.where).toEqual([{ metric: "kd", field: "value", op: "gte", n: 1.1 }]);
    expect(r.filters.top).toBe(10);
  });

  it("resolves the view the export sorts by", async () => {
    expect((await resolveReport(1, { metrics: "kd", view: "z" }, CATALOG)).view).toBe("z");
    expect((await resolveReport(1, { metrics: "kd", view: "bogus" }, CATALOG)).view).toBe("pctl");
    expect((await resolveReport(1, { metrics: "kd", view: "value" }, CATALOG)).view).toBe("value");
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

  it("applies the team landing preset to a bare team URL", async () => {
    const teamCatalog = [entry("map_win_rate"), entry("series_win_rate")];
    const r = await resolveReportForUrl(1, { entity: "teams" }, teamCatalog);
    expect(r.activePreset?.id).toBe("team-results");
    expect(r.selected).toEqual(["map_win_rate", "series_win_rate"]);
  });

  it("still honours explicit columns", async () => {
    const r = await resolveReportForUrl(1, { metrics: "kd" }, CATALOG);
    expect(r.selected).toEqual(["kd"]);
  });
});

describe("mode mix and combined span", () => {
  it("keeps a single mode on the published rows", async () => {
    const r = await resolveReport(1, { metrics: "kd", mode: "hardpoint" }, CATALOG);
    expect(r.modeSlug).toBe("hardpoint");
    expect(r.modeMix).toEqual([]);
    expect(r.aggregated).toBe(false);
    expect(r.aggregate).toBeUndefined();
  });

  it("reads a CSV of two modes as one re-aggregated mix, in scope order", async () => {
    const r = await resolveReport(
      1,
      { metrics: "kd", mode: "search-and-destroy,hardpoint", years: "2018" },
      CATALOG,
    );
    expect(r.modeSlug).toBeUndefined();
    expect(r.modeMix).toEqual(["hardpoint", "search-and-destroy"]);
    expect(r.aggregate).toMatchObject({
      modes: ["hardpoint", "search-and-destroy"],
      years: [2018],
      span: false,
    });
  });

  it("drops unknown modes from a mix, and one survivor is a single mode", async () => {
    const r = await resolveReport(1, { metrics: "kd", mode: "hardpoint,bogus" }, CATALOG);
    expect(r.modeSlug).toBe("hardpoint");
    expect(r.aggregated).toBe(false);
  });

  it("re-aggregates a span over every covered season when none is picked", async () => {
    const r = await resolveReport(
      1,
      { metrics: "kd", mode: "", rows: "span", years: "all" },
      CATALOG,
    );
    expect(r.span).toBe(true);
    expect(r.aggregate).toMatchObject({ years: [2017, 2018, 2019], modes: [], span: true });
  });
});
