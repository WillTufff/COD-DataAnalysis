"use client";

import { useState } from "react";
import type { PlacementRow } from "@/lib/analytics";

// Event placements over time for one team. Rank 1 sits at the top; wins get
// the accent. Ties in the archive ("3rd-4th") plot at placement_min.
export function PlacementTimeline({ placements }: { placements: PlacementRow[] }) {
  const [hover, setHover] = useState<PlacementRow | null>(null);
  const pts = placements.filter((p) => p.placementMin !== null && p.startDate);
  if (pts.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-ink-muted">
        No recorded placements for this team.
      </p>
    );
  }

  const W = 720;
  const H = 200;
  const M = { top: 14, right: 16, bottom: 24, left: 34 };
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const t = (p: PlacementRow) => Date.parse(p.startDate as string);
  const t0 = Math.min(...pts.map(t));
  const t1 = Math.max(...pts.map(t));
  const worst = Math.max(8, ...pts.map((p) => p.placementMin as number));
  // sqrt scale keeps 1st/2nd/3rd separated while compressing 17th vs 24th
  const y = (place: number) =>
    M.top + ((Math.sqrt(place) - 1) / (Math.sqrt(worst) - 1)) * ih;
  const x = (p: PlacementRow) =>
    M.left + (t1 === t0 ? 0.5 : (t(p) - t0) / (t1 - t0)) * iw;

  const yTicks = [1, 2, 4, 8, 16, 32].filter((v) => v <= worst);
  const years = [...new Set(pts.map((p) => new Date(t(p)).getUTCFullYear()))];

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Event placements over time, first place at top"
        onMouseLeave={() => setHover(null)}
      >
        {yTicks.map((v) => (
          <g key={v}>
            <line
              x1={M.left}
              x2={W - M.right}
              y1={y(v)}
              y2={y(v)}
              stroke={v === 1 ? "var(--baseline)" : "var(--hairline)"}
            />
            <text
              x={M.left - 6}
              y={y(v) + 3}
              textAnchor="end"
              fontSize={9.5}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {v}
            </text>
          </g>
        ))}
        {years.map((yr) => {
          const tt = Date.UTC(yr, 0, 1);
          if (tt < t0 || tt > t1) return null;
          const xx = M.left + ((tt - t0) / (t1 - t0 || 1)) * iw;
          return (
            <text key={yr} x={xx} y={H - 8} fontSize={10} fill="var(--ink-muted)">
              {yr}
            </text>
          );
        })}
        <path
          d={pts.map((p, i) => `${i ? "L" : "M"}${x(p)},${y(p.placementMin as number)}`).join(" ")}
          fill="none"
          stroke="var(--ink-muted)"
          strokeWidth={1}
          opacity={0.5}
        />
        {pts.map((p) => {
          const win = p.placementMin === 1;
          return (
            <circle
              key={p.eventId}
              cx={x(p)}
              cy={y(p.placementMin as number)}
              r={win ? 5 : 3.5}
              fill={win ? "var(--accent)" : "var(--surface-raised)"}
              stroke={win ? "none" : "var(--ink-muted)"}
              strokeWidth={1.25}
              onMouseEnter={() => setHover(p)}
            />
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-ink-muted">
        {hover ? (
          <span className="text-ink-secondary">
            {hover.event}
            {hover.startDate && ` (${hover.startDate.slice(0, 10)})`}: finished{" "}
            {hover.placementMin}
            {hover.placementMax !== hover.placementMin && `–${hover.placementMax}`}
            {hover.prize != null && ` · $${Number(hover.prize).toLocaleString()}`}
          </span>
        ) : (
          <>
            One dot per event, first place at the top. Amber dots are event wins.
            Hover a dot for the event name and prize.
          </>
        )}
      </figcaption>
    </figure>
  );
}
