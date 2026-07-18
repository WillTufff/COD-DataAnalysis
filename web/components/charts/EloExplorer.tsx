"use client";

import { useMemo, useState } from "react";
import type { EloTimeline, EraSpan } from "@/lib/analytics";

// Fixed slot order (validated categorical palette, dark steps). Color follows
// the team — slot is assigned once by standings rank and never repainted when
// the selection changes.
const SLOTS = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
];

const DEFAULT_SHOWN = 4;

export function EloExplorer({
  timelines,
  eras = [],
  height = 340,
}: {
  timelines: EloTimeline[];
  eras?: EraSpan[];
  height?: number;
}) {
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(timelines.slice(0, DEFAULT_SHOWN).map((t) => t.teamId)),
  );
  const [hover, setHover] = useState<{ team: string; t: string; rating: number } | null>(
    null,
  );

  const slotOf = useMemo(() => {
    const m = new Map<number, string>();
    timelines.forEach((tl, i) => m.set(tl.teamId, SLOTS[i % SLOTS.length]));
    return m;
  }, [timelines]);

  const shown = timelines.filter((tl) => selected.has(tl.teamId));

  const W = 760;
  const H = height;
  const M = { top: 16, right: 110, bottom: 28, left: 46 };
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;

  const allPts = shown.flatMap((tl) => tl.points);
  const t0 = Math.min(...allPts.map((p) => Date.parse(p.t)));
  const t1 = Math.max(...allPts.map((p) => Date.parse(p.t)));
  const r0 = Math.min(1400, ...allPts.map((p) => p.rating));
  const r1 = Math.max(1600, ...allPts.map((p) => p.rating));
  const x = (t: string) => M.left + ((Date.parse(t) - t0) / (t1 - t0 || 1)) * iw;
  const y = (r: number) => M.top + ih - ((r - r0) / (r1 - r0)) * ih;

  const years: number[] = [];
  if (allPts.length) {
    for (
      let yr = new Date(t0).getUTCFullYear();
      yr <= new Date(t1).getUTCFullYear();
      yr++
    )
      years.push(yr);
  }

  const rTicks: number[] = [];
  for (let v = Math.ceil(r0 / 100) * 100; v <= r1; v += 100) rTicks.push(v);

  function toggle(teamId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(teamId)) next.delete(teamId);
      else next.add(teamId);
      return next;
    });
  }

  return (
    <figure>
      {/* filter row: one row above the chart */}
      <div className="mb-3 flex flex-wrap gap-2">
        {timelines.map((tl) => {
          const on = selected.has(tl.teamId);
          return (
            <button
              key={tl.teamId}
              onClick={() => toggle(tl.teamId)}
              aria-pressed={on}
              className={`flex items-center gap-1.5 rounded border px-2 py-1 text-xs transition-colors ${
                on
                  ? "border-hairline bg-surface-raised text-ink"
                  : "border-transparent text-ink-muted hover:text-ink-secondary"
              }`}
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: slotOf.get(tl.teamId), opacity: on ? 1 : 0.35 }}
              />
              {tl.team}
            </button>
          );
        })}
      </div>

      {shown.length === 0 ? (
        <p className="py-16 text-center text-sm text-ink-muted">
          Pick a team above to plot its rating path.
        </p>
      ) : (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          role="img"
          aria-label="Team Elo rating over time, one line per selected team"
          onMouseLeave={() => setHover(null)}
        >
          {/* era bands: each title is literally a different game — the chart
              says so before any line is read */}
          {eras.map((era, i) => {
            const a = Math.max(Date.parse(era.from), t0);
            const b = Math.min(Date.parse(era.to), t1);
            if (!(b > a)) return null;
            const xa = M.left + ((a - t0) / (t1 - t0 || 1)) * iw;
            const xb = M.left + ((b - t0) / (t1 - t0 || 1)) * iw;
            return (
              <g key={era.title}>
                {i % 2 === 1 && (
                  <rect
                    x={xa}
                    y={M.top}
                    width={xb - xa}
                    height={ih}
                    fill="var(--ink)"
                    opacity={0.035}
                  />
                )}
                <line
                  x1={xa}
                  x2={xa}
                  y1={M.top}
                  y2={M.top + ih}
                  stroke="var(--baseline)"
                  strokeDasharray="2 4"
                />
                <text
                  x={(xa + xb) / 2}
                  y={M.top + 12}
                  textAnchor="middle"
                  fontSize={11}
                  fill="var(--ink-muted)"
                  className="font-display"
                  style={{ letterSpacing: "0.12em", textTransform: "uppercase" }}
                >
                  {era.title} ’{String(era.year).slice(2)}
                </text>
              </g>
            );
          })}
          {rTicks.map((v) => (
            <g key={v}>
              <line
                x1={M.left}
                x2={W - M.right}
                y1={y(v)}
                y2={y(v)}
                stroke={v === 1500 ? "var(--baseline)" : "var(--hairline)"}
              />
              <text
                x={M.left - 8}
                y={y(v) + 3.5}
                textAnchor="end"
                fontSize={10}
                fill="var(--ink-muted)"
                className="font-mono"
              >
                {v}
              </text>
            </g>
          ))}
          {years.map((yr) => {
            const t = Date.UTC(yr, 0, 1);
            if (t < t0 || t > t1) return null;
            return (
              <text
                key={yr}
                x={M.left + ((t - t0) / (t1 - t0 || 1)) * iw}
                y={H - 8}
                fontSize={10}
                fill="var(--ink-muted)"
              >
                {yr}
              </text>
            );
          })}

          {shown.map((tl) => {
            const color = slotOf.get(tl.teamId);
            const d = tl.points
              .map((p, i) => `${i ? "L" : "M"}${x(p.t)},${y(p.rating)}`)
              .join(" ");
            const last = tl.points[tl.points.length - 1];
            return (
              <g key={tl.teamId}>
                <path d={d} fill="none" stroke={color} strokeWidth={2} />
                {/* direct label at line end — identity never color-alone */}
                <text
                  x={x(last.t) + 6}
                  y={y(last.rating) + 3.5}
                  fontSize={10.5}
                  fill="var(--ink-secondary)"
                >
                  {tl.team}
                </text>
                {tl.points.map((p, i) => (
                  <circle
                    key={i}
                    cx={x(p.t)}
                    cy={y(p.rating)}
                    r={7}
                    fill="transparent"
                    onMouseEnter={() =>
                      setHover({ team: tl.team, t: p.t, rating: p.rating })
                    }
                  />
                ))}
              </g>
            );
          })}

          {hover && (
            <g pointerEvents="none">
              <rect
                x={Math.min(x(hover.t) + 8, W - 190)}
                y={M.top}
                width={182}
                height={38}
                rx={4}
                fill="var(--surface-raised)"
                stroke="var(--hairline)"
              />
              <text
                x={Math.min(x(hover.t) + 18, W - 180)}
                y={M.top + 16}
                fontSize={11}
                fill="var(--ink)"
              >
                {hover.team}
              </text>
              <text
                x={Math.min(x(hover.t) + 18, W - 180)}
                y={M.top + 31}
                fontSize={11}
                fill="var(--ink-secondary)"
                className="font-mono"
              >
                {hover.rating.toFixed(0)} · {hover.t.slice(0, 10)}
              </text>
            </g>
          )}
        </svg>
      )}
      <figcaption className="mt-1 text-xs text-ink-muted">
        Series-level Elo (K=32, initial 1500) after each rated series. Teams shown
        are the top {timelines.length} by final rating; the 1500 line is league
        average by construction. Spec in{" "}
        <a href="/methodology#elo" className="underline">
          methodology
        </a>
        .
      </figcaption>
    </figure>
  );
}
