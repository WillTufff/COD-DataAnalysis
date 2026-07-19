"use client";

import { useState } from "react";

export type ArcPoint = {
  year: number;
  title: string; // IW / WWII / BO4 — annotation: cohorts change with the title
  kdZ: number;
  kdPctl: number; // 0..1
  maps: number;
};

// Era-adjusted career arc: season K/D z-score vs cohort, with a ±1.96/√maps
// sampling band (documented approximation — see /methodology#era).
export function CareerArc({ points }: { points: ArcPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const W = 640;
  const H = 260;
  const M = { top: 24, right: 24, bottom: 44, left: 44 };
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;

  const band = points.map((p) => 1.96 / Math.sqrt(Math.max(p.maps, 1)));
  const yLo = Math.min(-1, ...points.map((p, i) => p.kdZ - band[i]));
  const yHi = Math.max(1, ...points.map((p, i) => p.kdZ + band[i]));
  const x = (i: number) =>
    M.left + (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw);
  const y = (v: number) => M.top + ih - ((v - yLo) / (yHi - yLo)) * ih;

  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.kdZ)}`).join(" ");
  const bandPath =
    points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.kdZ + band[i])}`).join(" ") +
    points
      .map((p, i) => {
        const j = points.length - 1 - i;
        return `L${x(j)},${y(points[j].kdZ - band[j])}`;
      })
      .join(" ") +
    " Z";

  const ticks = [];
  for (let v = Math.ceil(yLo); v <= Math.floor(yHi); v++) ticks.push(v);

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Era-adjusted K/D by season with sampling uncertainty band"
        onMouseLeave={() => setHover(null)}
      >
        {/* grid + y axis (z units) */}
        {ticks.map((v) => (
          <g key={v}>
            <line
              x1={M.left}
              x2={W - M.right}
              y1={y(v)}
              y2={y(v)}
              stroke={v === 0 ? "var(--baseline)" : "var(--hairline)"}
              strokeWidth={1}
            />
            <text
              x={M.left - 8}
              y={y(v) + 3.5}
              textAnchor="end"
              fontSize={10}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {v > 0 ? `+${v}σ` : v === 0 ? "avg" : `${v}σ`}
            </text>
          </g>
        ))}

        {/* uncertainty band */}
        {points.length > 1 && (
          <path d={bandPath} fill="var(--series-1)" opacity={0.16} />
        )}

        {/* line + points */}
        {points.length > 1 && (
          <path d={line} fill="none" stroke="var(--series-1)" strokeWidth={2} />
        )}
        {points.map((p, i) => (
          <g key={p.year}>
            <circle
              cx={x(i)}
              cy={y(p.kdZ)}
              r={hover === i ? 6 : 4.5}
              fill="var(--series-1)"
              stroke="var(--surface)"
              strokeWidth={2}
            />
            {/* selective direct label: the percentile, the number people share */}
            <text
              x={x(i)}
              y={y(p.kdZ) - 12}
              textAnchor="middle"
              fontSize={11}
              fill="var(--ink)"
              className="font-mono"
            >
              {Math.round(p.kdPctl * 100)}th
            </text>
            {/* x tick: year + title annotation (cohort boundary) */}
            <text
              x={x(i)}
              y={H - M.bottom + 18}
              textAnchor="middle"
              fontSize={11}
              fill="var(--ink-secondary)"
            >
              {p.year}
            </text>
            <text
              x={x(i)}
              y={H - M.bottom + 32}
              textAnchor="middle"
              fontSize={9.5}
              fill="var(--ink-muted)"
              letterSpacing={1}
            >
              {p.title}
            </text>
            {/* generous hover hit target */}
            <rect
              x={x(i) - (points.length > 1 ? iw / (points.length - 1) / 2 : iw / 2)}
              y={M.top}
              width={points.length > 1 ? iw / (points.length - 1) : iw}
              height={ih}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          </g>
        ))}

        {/* tooltip */}
        {hover !== null && (
          <g pointerEvents="none">
            <rect
              x={Math.min(x(hover) + 10, W - 178)}
              y={M.top + 2}
              width={168}
              height={58}
              rx={4}
              fill="var(--surface-raised)"
              stroke="var(--hairline)"
            />
            <text
              x={Math.min(x(hover) + 20, W - 168)}
              y={M.top + 20}
              fontSize={11}
              fill="var(--ink)"
            >
              {points[hover].year} {points[hover].title} · {points[hover].maps} maps
            </text>
            <text
              x={Math.min(x(hover) + 20, W - 168)}
              y={M.top + 36}
              fontSize={11}
              fill="var(--ink-secondary)"
              className="font-mono"
            >
              z {points[hover].kdZ >= 0 ? "+" : ""}
              {points[hover].kdZ.toFixed(2)} · {Math.round(points[hover].kdPctl * 100)}th
              pctl
            </text>
            <text
              x={Math.min(x(hover) + 20, W - 168)}
              y={M.top + 52}
              fontSize={10}
              fill="var(--ink-muted)"
            >
              band ±{band[hover].toFixed(2)}σ sampling noise
            </text>
          </g>
        )}
      </svg>
      <figcaption className="mt-1 text-xs text-ink-muted">
        Season K/D as standard deviations from the qualified cohort mean of that
        season and title (all modes). Shaded band is ±1.96/√maps sampling noise
        (an approximation), spec in{" "}
        <a href="/methodology#era" className="underline">
          methodology
        </a>
        .
      </figcaption>
    </figure>
  );
}
