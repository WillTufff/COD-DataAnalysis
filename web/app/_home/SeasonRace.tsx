"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import type { RaceTeam, SeasonEvent } from "./queries";

const PALETTE = [
  "var(--series-5)",
  "var(--series-1)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-4)",
  "var(--series-3)",
  "var(--series-8)",
  "#5ec4c4",
  "#c7b24a",
  "#8fa3b8",
  "#b07fb8",
  "#7fae6b",
];

const W = 1000;
const H = 400;
const M = { top: 34, right: 12, bottom: 26, left: 44 };

function ratingAt(tm: RaceTeam, t: number): number {
  let r = tm.points[0].r;
  for (const p of tm.points) {
    if (p.t > t) break;
    r = p.r;
  }
  return r;
}

export function SeasonRace({ teams, events }: { teams: RaceTeam[]; events: SeasonEvent[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const color = useMemo(
    () => new Map(teams.map((tm, i) => [tm.teamId, PALETTE[i % PALETTE.length]])),
    [teams],
  );
  const t0 = Math.min(...teams.map((tm) => tm.points[0].t));
  const t1 = Math.max(...teams.map((tm) => tm.points[tm.points.length - 1].t));
  const all = teams.flatMap((tm) => tm.points.map((p) => p.r));
  const lo = Math.floor((Math.min(...all) - 10) / 50) * 50;
  const hi = Math.ceil((Math.max(...all) + 10) / 50) * 50;
  const x = (t: number) => M.left + ((t - t0) / (t1 - t0)) * (W - M.left - M.right);
  const y = (r: number) => M.top + ((hi - r) / (hi - lo)) * (H - M.top - M.bottom);
  const ticks = [];
  for (let v = lo; v <= hi; v += 50) ticks.push(v);

  const focus = hover ?? pinned;
  const at = cursor ?? t1;
  const ranked = [...teams]
    .map((tm) => ({ tm, r: ratingAt(tm, at) }))
    .sort((a, b) => b.r - a.r);
  const lans = events.filter((e) => e.lan);

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    if (px < M.left || px > W - M.right) return setCursor(null);
    setCursor(t0 + ((px - M.left) / (W - M.left - M.right)) * (t1 - t0));
  }

  const dateLabel = new Date(at).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_260px]">
      <div className="min-w-0">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="w-full touch-none select-none"
          onPointerMove={onMove}
          onPointerLeave={() => setCursor(null)}
          role="img"
          aria-label="Team Elo through the season"
        >
          {lans.map((e) => {
            const a = x(new Date(e.start).getTime());
            const b = x(new Date(e.end).getTime() + 86400_000);
            return (
              <g key={e.id}>
                <rect x={a} y={M.top} width={Math.max(b - a, 4)} height={H - M.top - M.bottom} fill="var(--surface-raised)" />
                <text x={(a + b) / 2} y={M.top - 10} textAnchor="middle" className="fill-ink-muted font-mono text-[10px]">
                  {e.name.replace("Championship", "Champs").replace("Major ", "M")}
                </text>
              </g>
            );
          })}
          {ticks.map((v) => (
            <g key={v}>
              <line x1={M.left} x2={W - M.right} y1={y(v)} y2={y(v)} stroke="var(--hairline)" />
              <text x={M.left - 8} y={y(v) + 3} textAnchor="end" className="fill-ink-muted font-mono text-[10px]">
                {v}
              </text>
            </g>
          ))}
          {teams.map((tm) => {
            const d = tm.points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.r).toFixed(1)}`).join("");
            const on = focus === tm.teamId;
            const dim = focus !== null && !on;
            return (
              <g key={tm.teamId}>
                <path
                  d={d}
                  fill="none"
                  stroke={color.get(tm.teamId)}
                  strokeWidth={on ? 3 : 1.6}
                  strokeOpacity={dim ? 0.12 : on ? 1 : 0.7}
                  strokeLinejoin="round"
                  style={{ transition: "stroke-opacity 150ms" }}
                />
                <path
                  d={d}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={10}
                  onPointerEnter={() => setHover(tm.teamId)}
                  onPointerLeave={() => setHover(null)}
                  onClick={() => setPinned(pinned === tm.teamId ? null : tm.teamId)}
                  className="cursor-pointer"
                />
              </g>
            );
          })}
          {cursor !== null && (
            <g pointerEvents="none">
              <line x1={x(at)} x2={x(at)} y1={M.top} y2={H - M.bottom} stroke="var(--ink-muted)" strokeDasharray="3 3" />
              <text x={x(at)} y={H - 8} textAnchor="middle" className="fill-ink font-mono text-[11px]">
                {dateLabel}
              </text>
              {ranked.map(({ tm, r }) => (
                <circle key={tm.teamId} cx={x(at)} cy={y(r)} r={focus === tm.teamId ? 4.5 : 2.5} fill={color.get(tm.teamId)} />
              ))}
            </g>
          )}
        </svg>
        <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-xs">
          {events
            .filter((e) => e.winner)
            .map((e) => {
              const w = teams.find((tm) => tm.team === e.winner);
              return (
                <li
                  key={e.id}
                  onPointerEnter={() => w && setHover(w.teamId)}
                  onPointerLeave={() => setHover(null)}
                  className="cursor-default"
                >
                  <span className="text-ink-muted">{e.name}</span>{" "}
                  <span style={{ color: w ? color.get(w.teamId) : undefined }}>{e.winner}</span>
                </li>
              );
            })}
        </ul>
        <p className="mt-2 text-xs text-ink-muted">
          Drag across the chart to see the table on any date. Shaded bands are LAN events; hover an
          event winner to find their line.
        </p>
      </div>
      <ol className="text-sm">
        <li className="mb-2 flex justify-between font-mono text-[11px] text-ink-muted">
          <span>{cursor === null ? "End of season" : dateLabel}</span>
          <span>Elo · W–L</span>
        </li>
        {ranked.map(({ tm, r }, i) => {
          const on = focus === tm.teamId;
          return (
            <li
              key={tm.teamId}
              onPointerEnter={() => setHover(tm.teamId)}
              onPointerLeave={() => setHover(null)}
              className={`flex items-center gap-2 border-b border-hairline/60 py-1 transition-opacity ${
                focus !== null && !on ? "opacity-40" : ""
              }`}
            >
              <span className="w-4 font-mono text-[11px] tabular-nums text-ink-muted">{i + 1}</span>
              <span className="h-2 w-2 flex-none rounded-full" style={{ background: color.get(tm.teamId) }} />
              <Link href={`/teams/${tm.slug}`} className="truncate hover:text-accent">
                {tm.team}
              </Link>
              <span className="ml-auto font-mono tabular-nums">{Math.round(r)}</span>
              <span className="w-12 text-right font-mono text-[11px] tabular-nums text-ink-muted">
                {tm.wins}–{tm.losses}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
