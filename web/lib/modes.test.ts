import { readFileSync, readdirSync } from "node:fs";
import { extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { EMPTY_MODE_CATALOG, type ModeCatalog, modeLabel, modeRank } from "./modes";

const CATALOG: ModeCatalog = {
  order: ["hardpoint", "search-and-destroy", "domination", "overload"],
  names: {
    hardpoint: "Hardpoint",
    "search-and-destroy": "Search & Destroy",
    domination: "Domination",
    overload: "Overload",
  },
};

describe("modeLabel", () => {
  it("names a mode the catalog carries", () => {
    expect(modeLabel(CATALOG, "search-and-destroy")).toBe("Search & Destroy");
  });

  it("names the all-modes slice for null, undefined and 'all'", () => {
    expect(modeLabel(CATALOG, null)).toBe("All modes");
    expect(modeLabel(CATALOG, undefined)).toBe("All modes");
    expect(modeLabel(CATALOG, "all")).toBe("All modes");
    expect(modeLabel(CATALOG, null, "All")).toBe("All");
  });

  it("prints an unknown slug as itself rather than blank", () => {
    expect(modeLabel(CATALOG, "blitz")).toBe("blitz");
    expect(modeLabel(EMPTY_MODE_CATALOG, "hardpoint")).toBe("hardpoint");
  });
});

describe("modeRank", () => {
  it("leads with the all-modes slice, then follows game_modes order", () => {
    const slugs = ["overload", "all", "hardpoint", "domination"];
    expect(slugs.sort((a, b) => modeRank(CATALOG, a) - modeRank(CATALOG, b))).toEqual([
      "all",
      "hardpoint",
      "domination",
      "overload",
    ]);
  });

  it("sorts an unknown slug last instead of ahead of everything", () => {
    // indexOf returns -1 for a mode the catalog lacks, which as a sort key puts
    // it in front of "all" — the defect this replaced.
    expect(modeRank(CATALOG, "blitz")).toBeGreaterThan(modeRank(CATALOG, "overload"));
    expect(modeRank(CATALOG, "blitz")).toBeGreaterThan(modeRank(CATALOG, "all"));
  });
});

// ---------- No page may keep its own copy of a measured list ----------
//
// Every list of modes, titles or seasons on this site is derived: modes from
// `game_modes`, titles and seasons from the run's own coverage. A literal copy
// of one goes stale silently — it keeps rendering, just without whatever was
// ingested after it was written. These scans fail the build when one comes back.

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SCANNED = ["app", "components", "lib"];
// `lib/modes.ts` is the one place these belong; a test fixture is not a list
// the site renders from.
const EXEMPT = (path: string) => path === "lib/modes.ts" || path.endsWith(".test.ts");

function sources(): { path: string; text: string }[] {
  const out: { path: string; text: string }[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "node_modules") walk(full);
      } else if ([".ts", ".tsx"].includes(extname(entry.name))) {
        const path = relative(ROOT, full);
        if (!EXEMPT(path)) out.push({ path, text: readFileSync(full, "utf8") });
      }
    }
  };
  for (const dir of SCANNED) walk(join(ROOT, dir));
  return out;
}

// `"search-and-destroy": "Search & Destroy"` — an object entry mapping a mode
// slug to its display name. Matched on the hyphenated slugs only: the metric
// catalog has its own categories that share the short names (`snd`, `control`)
// and are a different taxonomy, not a copy of this one.
const MODE_ENTRY = /["'](search-and-destroy|capture-the-flag)["']\s*:\s*["'][A-Z]/g;

// `2019: "BO4 ’19"` — a literal keyed by season year.
const YEAR_KEY = /^\s*(?:19|20)\d{2}\s*:/m;

describe("derived lists", () => {
  it("has no second copy of the mode display names", () => {
    const offenders = sources()
      .map((f) => ({ path: f.path, hits: [...f.text.matchAll(MODE_ENTRY)].length }))
      .filter((f) => f.hits >= 1)
      .map((f) => `${f.path} (${f.hits} entries)`);
    expect(offenders).toEqual([]);
  });

  it("has no literal keyed by season year", () => {
    const offenders = sources()
      .filter((f) => YEAR_KEY.test(f.text))
      .map((f) => f.path);
    expect(offenders).toEqual([]);
  });
});
