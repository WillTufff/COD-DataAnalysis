"use client";

import { useState } from "react";
import type { PaceCell } from "@/lib/analytics";
import { type SeasonEra, seasonInk, seasonTag } from "@/lib/eras";
import { EraLegend } from "./EraLegend";

// League engagement pace, kills per player-seat per 10 minutes, split by
// mode and title. This is the era-adjustment argument drawn as a chart: the
// same mode runs at visibly different speeds in different games.
//
// A cell needs map time, which only the seasons whose source records a duration
// have. The chart therefore covers fewer seasons than the archive, and says
// which in its caption rather than presenting its span as the whole of it.
//
// Era colors are fixed by season (validated categorical palette, dark steps)
// and never reassigned — color follows the era, not the row.
const BAR = 14;
const BAR_GAP = 2; // 2px surface gap between adjacent bars
const ROW_GAP = 18;

export function PaceByMode({
  cells,
  seasons,
}: {
  cells: PaceCell[];
  seasons: SeasonEra[];
}) {
  const [hover, setHover] = useState<PaceCell | null>(null);

  const modes = [...new Set(cells.map((c) => c.mode))];
  // Fast modes first, so the eye reads a pace gradient down the chart.
  modes.sort((a, b) => {
    const max = (m: string) =>
      Math.max(...cells.filter((c) => c.mode === m).map((c) => c.killsPer10));
    return max(b) - max(a);
  });
  const years = [...new Set(cells.map((c) => c.year))].sort();
  const vMax = Math.ceil(Math.max(...cells.map((c) => c.killsPer10)) / 5) * 5;
  // Modes with a single bar carry no cross-era comparison of their own, which
  // is the caption's point. Counted here rather than named in the prose.
  const oneTitleModes = modes.filter(
    (m) => cells.filter((c) => c.mode === m).length === 1,
  );

  const M = { top: 6, right: 40, bottom: 20, left: 118 };
  const W = 560;
  const rows = modes.map((mode) => cells.filter((c) => c.mode === mode));
  const rowH = (n: number) => n * BAR + (n - 1) * BAR_GAP;
  const H =
    M.top + rows.reduce((s, r) => s + rowH(r.length) + ROW_GAP, -ROW_GAP) + M.bottom;
  const iw = W - M.left - M.right;
  const x = (v: number) => M.left + (v / vMax) * iw;

  let yCursor = M.top;
  const rowTops = rows.map((r) => {
    const top = yCursor;
    yCursor += rowH(r.length) + ROW_GAP;
    return top;
  });

  return (
    <figure>
      <EraLegend seasons={seasons} shownYears={years} />
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Kills per player per 10 minutes, by mode and title"
        onMouseLeave={() => setHover(null)}
      >
        {[0, 10, 20].map(
          (v) =>
            v <= vMax && (
              <g key={v}>
                <line
                  x1={x(v)}
                  x2={x(v)}
                  y1={M.top}
                  y2={H - M.bottom}
                  stroke={v === 0 ? "var(--baseline)" : "var(--hairline)"}
                />
                <text
                  x={x(v)}
                  y={H - 6}
                  textAnchor="middle"
                  fontSize={10}
                  fill="var(--ink-muted)"
                  className="font-mono"
                >
                  {v}
                </text>
              </g>
            ),
        )}
        {modes.map((mode, mi) => {
          const rowCells = rows[mi];
          const top = rowTops[mi];
          return (
            <g key={mode}>
              <text
                x={M.left - 10}
                y={top + rowH(rowCells.length) / 2 + 3.5}
                textAnchor="end"
                fontSize={11.5}
                fill="var(--ink-secondary)"
              >
                {mode}
              </text>
              {rowCells.map((c, ci) => {
                const yBar = top + ci * (BAR + BAR_GAP);
                const w = Math.max(x(c.killsPer10) - x(0), 2);
                // Full-width hover bands tile the chart with no dead space:
                // each extends halfway into the neighboring gap, so moving
                // the pointer between bars hands hover off without a flicker.
                const bandTop =
                  ci === 0
                    ? mi === 0
                      ? 0
                      : yBar - ROW_GAP / 2
                    : yBar - BAR_GAP / 2;
                const bandBottom =
                  ci === rowCells.length - 1
                    ? mi === rows.length - 1
                      ? H
                      : yBar + BAR + ROW_GAP / 2
                    : yBar + BAR + BAR_GAP / 2;
                return (
                  <g
                    key={c.year}
                    onMouseEnter={() => setHover(c)}
                    onMouseLeave={() => setHover(null)}
                  >
                    <rect
                      x={0}
                      y={bandTop}
                      width={W}
                      height={bandBottom - bandTop}
                      fill="transparent"
                    />
                    {/* rounded data-end only — flat edge stays on the baseline */}
                    <path
                      d={`M${x(0)},${yBar} h${w - 4} a4,4 0 0 1 4,4 v${BAR - 8} a4,4 0 0 1 -4,4 h${4 - w} Z`}
                      fill={seasonInk(seasons, c.year)}
                      opacity={hover && hover !== c ? 0.45 : 1}
                    />
                    <text
                      x={x(c.killsPer10) + 5}
                      y={yBar + BAR / 2 + 3.5}
                      fontSize={10}
                      fill="var(--ink-secondary)"
                      className="font-mono"
                    >
                      {c.killsPer10.toFixed(1)}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-ink-muted">
        {hover ? (
          <span className="text-ink-secondary">
            {hover.mode}, {seasonTag(seasons, hover.year)}:{" "}
            {hover.killsPer10.toFixed(2)} kills per seat-10min over{" "}
            {hover.maps.toLocaleString()} maps.
          </span>
        ) : (
          <>
            Kills per player per 10 minutes of map time.{" "}
            {oneTitleModes.length > 0 && (
              <>
                {oneTitleModes.join(", ")}{" "}
                {oneTitleModes.length === 1 ? "appears" : "each appear"} in one
                season only, which is why cross-era comparison uses cohort
                scoring.{" "}
              </>
            )}
            {years.length < seasons.length && (
              <>
                Map duration is recorded for {years.length} of the archive&rsquo;s{" "}
                {seasons.length} seasons — {seasonTag(seasons, years[0])} to{" "}
                {seasonTag(seasons, years[years.length - 1])} — so the rest have no
                bar here rather than a bar of zero.
              </>
            )}
          </>
        )}
      </figcaption>
    </figure>
  );
}
