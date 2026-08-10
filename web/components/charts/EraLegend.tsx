"use client";

import type { ReactNode } from "react";
import {
  SERIES_STEPS,
  type SeasonEra,
  byLeague,
  inkRepeats,
  seasonInk,
  seasonTag,
} from "@/lib/eras";

// The season key shared by the era-coloured charts. Seasons are grouped by
// league because the archive holds more of them than the palette has steps: the
// groups put the two repeating pairs on separate rows, and the note under them
// names the repeat rather than leaving two identical dots to be read as one
// season. Hover on the chart itself names the exact cohort.
export function EraLegend({
  seasons,
  shownYears,
  children,
}: {
  seasons: SeasonEra[];
  shownYears: number[];
  children?: ReactNode;
}) {
  const groups = byLeague(seasons.filter((s) => shownYears.includes(s.year)));
  const repeats = inkRepeats(seasons, shownYears);

  return (
    <div className="mb-2 text-xs text-ink-secondary">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        {groups.map((g) => (
          <span key={g.league} className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-mono text-[10px] uppercase text-ink-muted">
              {g.league}
            </span>
            {g.seasons.map((s) => (
              <span key={s.year} className="flex items-center gap-1.5">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: seasonInk(seasons, s.year) }}
                />
                {seasonTag(seasons, s.year)}
              </span>
            ))}
          </span>
        ))}
        {children}
      </div>
      {repeats.length > 0 && (
        <p className="mt-1 text-ink-muted">
          The palette has {SERIES_STEPS} steps and the archive has {seasons.length}{" "}
          seasons, so{" "}
          {repeats.join(", ")}. Hover a bar for its season.
        </p>
      )}
    </div>
  );
}
