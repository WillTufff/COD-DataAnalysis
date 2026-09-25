"use client";

import type { ReportEntity } from "@/lib/reports/resolve";
import { useReportUrl } from "./reportUrl";

/**
 * Players | Teams. Switching swaps the whole metric catalog, so the columns,
 * preset, sort, mode and player filter cannot survive it. Seasons and the team
 * filter mean the same thing on either side and carry over.
 */
export function EntityTabs({ entity }: { entity: ReportEntity }) {
  const push = useReportUrl();
  return (
    <div role="tablist" className="flex gap-5 border-b border-hairline print:hidden">
      {(["players", "teams"] as const).map((e) => {
        const on = entity === e;
        return (
          <button
            key={e}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => {
              if (on) return;
              push({
                entity: e === "teams" ? "teams" : null,
                metrics: null,
                metric: null,
                preset: null,
                sort: null,
                dir: null,
                players: null,
                mode: null,
              });
            }}
            className={`-mb-px border-b-2 pb-2 font-display text-sm font-semibold uppercase tracking-wide transition-colors motion-reduce:transition-none ${
              on
                ? "border-accent text-ink"
                : "border-transparent text-ink-muted hover:text-ink"
            }`}
          >
            {e === "teams" ? "Teams" : "Players"}
          </button>
        );
      })}
    </div>
  );
}
