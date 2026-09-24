"use client";

import { useState } from "react";
import {
  type EarningsYear,
  formatMoney,
  formatMoneyExact,
} from "@/lib/earnings";

// Clean axis ticks: the smallest 1/2/5 step that fits the max in four ticks.
function ticks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = 0; v <= max + step * 0.001; v += step) out.push(v);
  if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
  return out;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function shortDate(iso: string): string {
  const [, m, d] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}`;
}

// Prize money by calendar year, one column per year. Years the archive holds
// no maps for the player are drawn lighter; the current year is labelled as
// partial, as of the date the figures were loaded.
export function EarningsByYear({
  years,
  coveredYears,
  loadedOn,
}: {
  years: EarningsYear[];
  coveredYears: number[];
  loadedOn: string | null;
}) {
  const [hover, setHover] = useState<EarningsYear | null>(null);
  if (years.length === 0) return null;

  const covered = new Set(coveredYears);
  const loadedYear = loadedOn ? Number(loadedOn.slice(0, 4)) : null;
  const partialYear =
    loadedYear !== null && years[years.length - 1].year === loadedYear
      ? loadedYear
      : null;
  const anyUncovered = years.some((y) => y.amount > 0 && !covered.has(y.year));

  const W = 720;
  const H = 210;
  const M = { top: 18, right: 8, bottom: 22, left: 48 };
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;
  const band = iw / years.length;
  const barW = Math.min(24, band - 2);
  const max = Math.max(...years.map((y) => y.amount));
  const yTicks = ticks(max);
  const top = yTicks[yTicks.length - 1] || 1;
  const y = (v: number) => M.top + ih - (v / top) * ih;
  const peak = years.reduce((a, b) => (b.amount > a.amount ? b : a));
  // Year labels thin out on long careers so they never collide.
  const every = band >= 34 ? 1 : band >= 17 ? 2 : 4;

  return (
    <figure>
      {anyUncovered && (
        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-secondary">
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-[2px] bg-series-1" />
            Archive holds maps that year
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-[2px] bg-series-1 opacity-35" />
            No archived maps
          </span>
        </div>
      )}
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Prize money earned in each calendar year"
        onMouseLeave={() => setHover(null)}
      >
        {yTicks.map((v) => (
          <g key={v}>
            <line
              x1={M.left}
              x2={W - M.right}
              y1={y(v)}
              y2={y(v)}
              stroke={v === 0 ? "var(--baseline)" : "var(--hairline)"}
            />
            <text
              x={M.left - 6}
              y={y(v) + 3}
              textAnchor="end"
              fontSize={9.5}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {v === 0 ? "$0" : formatMoney(v)}
            </text>
          </g>
        ))}
        {years.map((yr, i) => {
          const cx = M.left + band * (i + 0.5);
          const h = y(0) - y(yr.amount);
          const x0 = cx - barW / 2;
          const r = Math.min(4, h, barW / 2);
          const lit = covered.has(yr.year);
          const isHover = hover?.year === yr.year;
          const labelled =
            yr.amount > 0 && (yr === peak || yr.year === partialYear);
          return (
            <g key={yr.year}>
              {h > 0 && (
                <path
                  d={`M${x0},${y(0)} v${-(h - r)} a${r},${r} 0 0 1 ${r},${-r} h${barW - 2 * r} a${r},${r} 0 0 1 ${r},${r} v${h - r} Z`}
                  fill="var(--series-1)"
                  opacity={(lit ? 1 : 0.35) * (hover && !isHover ? 0.7 : 1)}
                />
              )}
              {labelled && (
                <text
                  x={cx}
                  y={y(yr.amount) - 5}
                  textAnchor="middle"
                  fontSize={9.5}
                  fill="var(--ink-secondary)"
                  className="font-mono"
                >
                  {formatMoney(yr.amount)}
                </text>
              )}
              {(i % every === 0 || yr.year === partialYear) && (
                <text
                  x={cx}
                  y={H - 8}
                  textAnchor="middle"
                  fontSize={10}
                  fill="var(--ink-muted)"
                  className="font-mono"
                >
                  {yr.year}
                  {yr.year === partialYear && "*"}
                </text>
              )}
              <rect
                x={M.left + band * i}
                y={M.top}
                width={band}
                height={ih}
                fill="transparent"
                onMouseEnter={() => setHover(yr)}
              />
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-ink-muted">
        {hover ? (
          <span className="text-ink-secondary">
            {hover.year}: {formatMoneyExact(hover.amount)}
            {hover.year === partialYear && loadedOn && ` so far, as loaded ${shortDate(loadedOn)}`}
            {covered.has(hover.year)
              ? ""
              : " · no archived maps that year"}
          </span>
        ) : (
          <>
            Calendar years, not seasons. Hover a column for the exact figure.
            {partialYear && loadedOn && (
              <> *{partialYear} is partial: the figure as loaded on {shortDate(loadedOn)}.</>
            )}
          </>
        )}
      </figcaption>
    </figure>
  );
}
