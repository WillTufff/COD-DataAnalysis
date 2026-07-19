"use client";

import { useState } from "react";
import type { ModeWeightCohort } from "@/lib/analytics";

// What the map-outcome regression learned, drawn as an argument: for each
// (season × mode), how much a one-SD team edge in objective play was worth
// relative to the same edge in slaying. Bars diverge from 1× on a log scale —
// left means the gunfight decided maps, right means the objective did.
//
// Era colors are fixed by season and never reassigned (same convention as
// every other chart on the site: color follows the era, not the row).
const ERA_COLOR: Record<number, string> = {
  2017: "var(--series-1)",
  2018: "var(--series-2)",
  2019: "var(--series-3)",
};
const ERA_LABEL: Record<number, string> = {
  2017: "IW ’17",
  2018: "WWII ’18",
  2019: "BO4 ’19",
};

const BAR = 14;
const BAR_GAP = 2;
const ROW_GAP = 18;
const TICKS = [0.25, 0.5, 1, 2, 4, 8];

export function WhatWinsMaps({ cohorts }: { cohorts: ModeWeightCohort[] }) {
  const [hover, setHover] = useState<ModeWeightCohort | null>(null);

  const shown = cohorts.filter((c) => c.objVsSlay > 0);
  const modes = [...new Set(shown.map((c) => c.mode))];
  // Most objective-driven modes first, so the chart reads as a gradient.
  modes.sort((a, b) => {
    const max = (m: string) =>
      Math.max(...shown.filter((c) => c.mode === m).map((c) => c.objVsSlay));
    return max(b) - max(a);
  });
  const years = [...new Set(shown.map((c) => c.year))].sort();

  const lo = Math.min(...TICKS, ...shown.map((c) => c.objVsSlay));
  const hi = Math.max(...TICKS, ...shown.map((c) => c.objVsSlay));
  const [logLo, logHi] = [Math.log2(lo), Math.log2(hi)];

  const M = { top: 6, right: 46, bottom: 20, left: 118 };
  const W = 560;
  const iw = W - M.left - M.right;
  const x = (v: number) => M.left + ((Math.log2(v) - logLo) / (logHi - logLo)) * iw;

  const rows = modes.map((mode) => shown.filter((c) => c.mode === mode));
  const rowH = (n: number) => n * BAR + (n - 1) * BAR_GAP;
  const H =
    M.top + rows.reduce((s, r) => s + rowH(r.length) + ROW_GAP, -ROW_GAP) + M.bottom;

  let yCursor = M.top;
  const rowTops = rows.map((r) => {
    const top = yCursor;
    yCursor += rowH(r.length) + ROW_GAP;
    return top;
  });

  return (
    <figure>
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-secondary">
        {years.map((y) => (
          <span key={y} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: ERA_COLOR[y] }}
            />
            {ERA_LABEL[y]}
          </span>
        ))}
        <span className="ml-auto text-ink-muted">
          ← slaying decides · objective decides →
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Objective-vs-slaying map-win weight by mode and title"
        onMouseLeave={() => setHover(null)}
      >
        {TICKS.map((v) => (
          <g key={v}>
            <line
              x1={x(v)}
              x2={x(v)}
              y1={M.top}
              y2={H - M.bottom}
              stroke={v === 1 ? "var(--baseline)" : "var(--hairline)"}
            />
            <text
              x={x(v)}
              y={H - 6}
              textAnchor="middle"
              fontSize={10}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {v < 1 ? `${v}×` : `${v}×`}
            </text>
          </g>
        ))}
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
                const x0 = x(1);
                const x1 = x(c.objVsSlay);
                const objSide = x1 >= x0;
                const w = Math.max(Math.abs(x1 - x0), 2);
                // Rounded end on the data side; flat edge sits on the 1× line.
                const d = objSide
                  ? `M${x0},${yBar} h${w - 4} a4,4 0 0 1 4,4 v${BAR - 8} a4,4 0 0 1 -4,4 h${4 - w} Z`
                  : `M${x0},${yBar} h${4 - w} a4,4 0 0 0 -4,4 v${BAR - 8} a4,4 0 0 0 4,4 h${w - 4} Z`;
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
                    key={`${c.year}-${c.mode}`}
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
                    <path
                      d={d}
                      fill={ERA_COLOR[c.year]}
                      opacity={hover && hover !== c ? 0.45 : 1}
                    />
                    {/* Left-pointing bars label on their empty right side, so
                        small ratios never collide with the mode name. */}
                    <text
                      x={objSide ? x1 + 5 : x0 + 5}
                      y={yBar + BAR / 2 + 3.5}
                      textAnchor="start"
                      fontSize={10}
                      fill="var(--ink-secondary)"
                      className="font-mono"
                    >
                      {c.objVsSlay.toFixed(1)}×
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
            {ERA_LABEL[hover.year]} {hover.mode}: a one-SD objective edge was worth{" "}
            {hover.objVsSlay.toFixed(1)}× the equivalent slaying edge (regression
            over {hover.nMaps.toLocaleString()} maps).
          </span>
        ) : (
          <>
            Win-odds weight of a one-SD team objective edge relative to a one-SD
            slaying edge, per (title × mode), log scale. 1× means both mattered
            equally; hill time in 2018 WWII Hardpoint is the outlier.
          </>
        )}
      </figcaption>
    </figure>
  );
}
