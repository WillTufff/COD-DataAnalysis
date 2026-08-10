"use client";

import { useState } from "react";
import { SERIES_STEPS } from "@/lib/eras";

export type StyleAxisMeta = {
  index: number;
  name: string;
  share: number;
  loadings: { column: string; loading: number }[];
};

export type StyleSeasonPoint = {
  year: number;
  title: string;
  axis: number;
  pctl: number;
};

// One row per style axis: the cohort as a track, the player's seasons as marks
// on it. This is deliberately not a radar. A radar implies a shape to compare
// against a template, and there is no template — the archetype partition does
// not beat a cloud with no clusters in it, so the honest visual is a position
// on a continuum with the cohort drawn behind it.
//
// The season marks are joined so the eye reads movement, which is the part a
// label could never have shown.
// The validated dark categorical palette, in its fixed order — one step per
// season the player played. A career longer than the palette wraps, and the
// legend below names the wrap rather than leaving two marks the same colour
// with nothing said about it.
const SEASON_INK = Array.from(
  { length: SERIES_STEPS },
  (_, i) => `var(--series-${i + 1})`,
);

export function StyleAxes({
  axes,
  points,
  cohortN,
}: {
  axes: StyleAxisMeta[];
  points: StyleSeasonPoint[];
  cohortN: number;
}) {
  const [hover, setHover] = useState<string | null>(null);

  const seasons = [...new Set(points.map((p) => p.year))].sort((a, b) => a - b);
  const titleOf = new Map(points.map((p) => [p.year, p.title]));
  const inkOf = (year: number) =>
    SEASON_INK[seasons.indexOf(year) % SEASON_INK.length];

  const W = 320;
  const ROW = 44;
  const M = { top: 8, right: 10, bottom: 26, left: 10 };
  const H = M.top + axes.length * ROW + M.bottom;
  const trackW = W - M.left - M.right;
  const x = (pctl: number) => M.left + pctl * trackW;

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full max-w-[320px]"
        role="img"
        aria-label={`Position on ${axes.length} style axes, against a cohort of ${cohortN} player-seasons`}
        onMouseLeave={() => setHover(null)}
      >
        {axes.map((axis, i) => {
          const top = M.top + i * ROW;
          const mid = top + 26;
          return (
            <g key={axis.index}>
              <text
                x={M.left}
                y={top + 10}
                fontSize={10}
                fill="var(--ink-secondary)"
                className="font-mono"
              >
                {axis.name}
              </text>
              <text
                x={W - M.right}
                y={top + 10}
                textAnchor="end"
                fontSize={9}
                fill="var(--ink-muted)"
                className="font-mono"
              >
                {(axis.share * 100).toFixed(1)}%
              </text>

              {/* the cohort: percentile is uniform by construction, so the
                  track itself is the distribution and needs no histogram */}
              <line
                x1={x(0)}
                x2={x(1)}
                y1={mid}
                y2={mid}
                stroke="var(--hairline)"
                strokeWidth={6}
                strokeLinecap="round"
              />
              <line
                x1={x(0.25)}
                x2={x(0.75)}
                y1={mid}
                y2={mid}
                stroke="var(--baseline)"
                strokeWidth={6}
              />
              <line
                x1={x(0.5)}
                x2={x(0.5)}
                y1={mid - 5}
                y2={mid + 5}
                stroke="var(--ink-muted)"
                strokeWidth={1}
              />

              {/* the player's seasons, joined in time */}
              {(() => {
                const mine = points
                  .filter((p) => p.axis === axis.index)
                  .sort((a, b) => a.year - b.year);
                return (
                  <>
                    {mine.length > 1 && (
                      <polyline
                        points={mine.map((p) => `${x(p.pctl)},${mid}`).join(" ")}
                        fill="none"
                        stroke="var(--ink-muted)"
                        strokeWidth={1}
                        strokeDasharray="2 2"
                      />
                    )}
                    {mine.map((p) => {
                      const key = `${axis.index}:${p.year}`;
                      return (
                        <circle
                          key={key}
                          cx={x(p.pctl)}
                          cy={mid}
                          r={hover === key ? 6 : 4.5}
                          fill={inkOf(p.year)}
                          stroke="var(--surface)"
                          strokeWidth={1.5}
                          onMouseEnter={() => setHover(key)}
                        >
                          <title>
                            {`${axis.name} · ${p.year} ${p.title}: ${Math.round(
                              p.pctl * 100,
                            )}th percentile of ${cohortN}`}
                          </title>
                        </circle>
                      );
                    })}
                  </>
                );
              })()}
            </g>
          );
        })}

        <text
          x={M.left}
          y={H - 14}
          fontSize={9}
          fill="var(--ink-muted)"
          className="font-mono"
        >
          0
        </text>
        <text
          x={W - M.right}
          y={H - 14}
          textAnchor="end"
          fontSize={9}
          fill="var(--ink-muted)"
          className="font-mono"
        >
          100th percentile
        </text>
      </svg>

      <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-[10px] text-ink-muted">
        {seasons.map((year) => (
          <span key={year} className="flex items-center gap-1">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: inkOf(year) }}
            />
            {year} {titleOf.get(year)}
          </span>
        ))}
      </div>
      {seasons.length > SEASON_INK.length && (
        <p className="mt-1 font-mono text-[10px] text-ink-muted">
          {seasons.length} seasons over {SEASON_INK.length} palette steps — the
          last {seasons.length - SEASON_INK.length} repeat a colour; read the
          marks left to right in time.
        </p>
      )}
    </figure>
  );
}
