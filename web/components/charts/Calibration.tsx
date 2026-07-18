"use client";

import { useState } from "react";

export type CalBin = {
  lo: number;
  hi: number;
  n: number;
  mean_pred?: number;
  frac_won?: number;
};

// Reliability plot: mean predicted P(win) vs observed win rate per bin.
// A perfectly calibrated model sits on the diagonal.
export function Calibration({ bins }: { bins: CalBin[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const pts = bins.filter(
    (b): b is Required<CalBin> => b.n > 0 && b.mean_pred !== undefined,
  );

  const W = 300;
  const H = 300;
  const M = { top: 12, right: 12, bottom: 36, left: 40 };
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const x = (v: number) => M.left + v * iw;
  const y = (v: number) => M.top + ih - v * ih;
  const maxN = Math.max(...pts.map((p) => p.n), 1);

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full max-w-[300px]"
        role="img"
        aria-label="Calibration: predicted win probability versus observed win rate"
        onMouseLeave={() => setHover(null)}
      >
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line x1={x(0)} x2={x(1)} y1={y(v)} y2={y(v)} stroke="var(--hairline)" />
            <line x1={x(v)} x2={x(v)} y1={y(0)} y2={y(1)} stroke="var(--hairline)" />
            <text
              x={M.left - 6}
              y={y(v) + 3}
              textAnchor="end"
              fontSize={9}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {v}
            </text>
            <text
              x={x(v)}
              y={H - M.bottom + 14}
              textAnchor="middle"
              fontSize={9}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {v}
            </text>
          </g>
        ))}
        {/* perfect-calibration reference */}
        <line
          x1={x(0)}
          y1={y(0)}
          x2={x(1)}
          y2={y(1)}
          stroke="var(--baseline)"
          strokeDasharray="4 3"
        />
        {pts.map((p, i) => (
          <circle
            key={i}
            cx={x(p.mean_pred)}
            cy={y(p.frac_won)}
            r={4 + (p.n / maxN) * 6}
            fill="var(--series-1)"
            fillOpacity={0.75}
            stroke="var(--surface)"
            strokeWidth={2}
            onMouseEnter={() => setHover(i)}
          />
        ))}
        {hover !== null && pts[hover] && (
          <g pointerEvents="none">
            <rect
              x={12}
              y={M.top}
              width={166}
              height={34}
              rx={4}
              fill="var(--surface-raised)"
              stroke="var(--hairline)"
            />
            <text x={20} y={M.top + 14} fontSize={10} fill="var(--ink)">
              predicted {(pts[hover].mean_pred * 100).toFixed(0)}% · won{" "}
              {(pts[hover].frac_won * 100).toFixed(0)}%
            </text>
            <text x={20} y={M.top + 28} fontSize={10} fill="var(--ink-secondary)">
              {pts[hover].n} series in bin
            </text>
          </g>
        )}
        <text
          x={x(0.5)}
          y={H - 4}
          textAnchor="middle"
          fontSize={9.5}
          fill="var(--ink-secondary)"
        >
          predicted P(win)
        </text>
        <text
          x={10}
          y={y(0.5)}
          fontSize={9.5}
          fill="var(--ink-secondary)"
          transform={`rotate(-90 10 ${y(0.5)})`}
          textAnchor="middle"
        >
          observed win rate
        </text>
      </svg>
      <figcaption className="mt-1 text-xs text-ink-muted">
        Dot size scales with the number of series in each probability bin; the
        dashed diagonal is perfect calibration.
      </figcaption>
    </figure>
  );
}
