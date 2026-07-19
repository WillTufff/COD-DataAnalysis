"use client";

import { useState } from "react";
import type { PaceCell } from "@/lib/analytics";

// League engagement pace, kills per player-seat per 10 minutes, split by
// mode and title. This is the era-adjustment argument drawn as a chart: the
// same mode runs at visibly different speeds in different games, and half
// the modes only exist in one game at all.
//
// Era colors are fixed by season (validated categorical palette, dark steps)
// and never reassigned — color follows the era, not the row.
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
const BAR_GAP = 2; // 2px surface gap between adjacent bars
const ROW_GAP = 18;

export function PaceByMode({ cells }: { cells: PaceCell[] }) {
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
      </div>
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
                      fill={ERA_COLOR[c.year]}
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
            {hover.mode}, {ERA_LABEL[hover.year]}: {hover.killsPer10.toFixed(2)}{" "}
            kills per seat-10min over {hover.maps.toLocaleString()} maps.
          </span>
        ) : (
          <>
            Kills per player per 10 minutes of map time. Uplink, CTF, and
            Control each existed in only one title, which is why cross-era
            comparison uses cohort scoring.
          </>
        )}
      </figcaption>
    </figure>
  );
}
