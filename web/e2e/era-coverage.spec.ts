// Era coverage: does every era actually have a rating surface on it?
//
// A page whose join comes back empty renders its empty state and returns 200,
// so a status-code check passes while a whole era shows nothing. That has
// happened on this site. These assertions are about rows on the page, per era,
// and they are derived from the database rather than written down here.
//
// Two modes. With a fitted database (E2E_MODE unset) the coverage assertions
// run. In smoke mode — CI, where the database holds synthetic seeds and no
// model run — only the "renders without throwing" half runs, which is still
// worth having: an unguarded reader crashing on empty data is the other way
// this page breaks.
import { expect, test } from "@playwright/test";
import { eraSamples, skillYears } from "./population";

const SMOKE = process.env.E2E_MODE === "smoke";

const PAGES = [
  "/",
  "/players",
  "/teams",
  "/findings",
  "/methodology",
  "/stats",
];

for (const path of PAGES) {
  test(`${path} renders without an error`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const response = await page.goto(path);
    expect(response?.status(), `${path} status`).toBe(200);
    await expect(page.locator("main")).toBeVisible();
    expect(errors, `${path} client errors`).toEqual([]);
  });
}

test.describe("rating surfaces hold rows", () => {
  test.skip(SMOKE, "smoke mode: the seeded database has no fitted model");

  test("/players leads with SKILL and keeps the season rating", async ({
    page,
  }) => {
    await page.goto("/players");
    const skill = page.locator('[data-surface="skill-board"]');
    const value = page.locator('[data-surface="value-board"]');
    await expect(skill).toBeVisible();
    expect(await skill.locator("tbody tr").count()).toBeGreaterThan(0);
    await expect(value).toBeVisible();
    expect(await value.locator("tbody tr").count()).toBeGreaterThan(0);
    // The demoted rating has to say it is the season rating, not the forecast.
    await expect(value).toContainText("What a season was worth");
  });

  test("every era has a populated player page", async ({ page }) => {
    const samples = await eraSamples();
    expect(samples.length, "leagues with a rated season").toBeGreaterThan(1);
    const years = new Set(await skillYears());
    for (const s of samples) {
      await page.goto(`/players/${s.slug}`);
      await expect(
        page.locator("main"),
        `${s.league} sample ${s.handle}`,
      ).toBeVisible();
      // The career tab always carries the season rating, in both eras.
      await expect(page.getByRole("heading", { name: "Rating" })).toBeVisible();
      const skill = page.locator('[data-surface="skill"]');
      await expect(skill, `${s.league} skill surface`).toBeVisible();
      const state = await skill.getAttribute("data-state");
      if (years.has(s.year)) {
        // Where SKILL covers the era it must hold rows, not a heading alone.
        expect(
          await skill.locator("tbody tr").count(),
          `${s.league} skill rows`,
        ).toBeGreaterThan(0);
      } else {
        // And where it does not, the page must say so rather than show nothing.
        expect(state, `${s.league} skill state`).toBe("absent");
        await expect(skill).toContainText("SKILL");
      }
    }
  });

  test("/methodology renders every published phase", async ({ page }) => {
    await page.goto("/methodology");
    for (const id of [
      "primacy",
      "player-rating",
      "season-rapm",
      "opponent-adjustment",
      "match-context",
      "evaluation",
      "skill",
    ]) {
      const section = page.locator(`#${id}`);
      await expect(section, `#${id}`).toBeVisible();
      // A section with a heading and no number is a stub; every one of these
      // argues from an artifact and must show one.
      await expect(section).toContainText(/\d/);
    }
  });

  test("match context shows both eras and its ablation nulls", async ({
    page,
  }) => {
    await page.goto("/methodology");
    const section = page.locator("#match-context");
    await expect(section).toBeVisible();
    // The phase's whole argument is that most families did nothing, so the
    // table has to carry the families that were dropped, not only the kept one.
    await expect(section).toContainText("dropped");
    await expect(section).toContainText("kept");
    // One line per era. The CWL count is the reason the venue question is only
    // answerable in the CDL era, and a page that shows one era hides that.
    await expect(section).toContainText("Call of Duty League lines");
    await expect(section).toContainText("CWL");
    expect(await section.locator("tbody tr").count()).toBeGreaterThan(1);
  });

  test("/teams and /findings are not empty", async ({ page }) => {
    await page.goto("/teams");
    expect(await page.locator("tbody tr").count()).toBeGreaterThan(0);
    await page.goto("/findings");
    await expect(page.locator("main")).toContainText(/\d/);
  });
});
