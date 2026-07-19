"use client";

import { useMemo, useState } from "react";
import type { EloTimeline, EraSpan, EventMarker } from "@/lib/analytics";

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

// Static labels are majors only; everything else is hover-to-identify, so
// codes stay terse enough to survive the densest stretch (WWII spring 2018).
const CITY_CODES: Record<string, string> = {
  Dallas: "DAL",
  "New Orleans": "NOLA",
  Atlanta: "ATL",
  Birmingham: "BHAM",
  Seattle: "SEA",
  Anaheim: "ANA",
  "Fort Worth": "FTW",
  London: "LON",
};

function shortLabel(name: string): string {
  if (/championship/i.test(name)) return "CHAMPS";
  const city = name.replace(/^CWL\s+/, "").replace(/\s+\d{4}$/, "");
  return CITY_CODES[city] ?? city.toUpperCase().slice(0, 4);
}

export function EloExplorer({
  timelines,
  eras = [],
  events = [],
  height = 340,
}: {
  timelines: EloTimeline[];
  eras?: EraSpan[];
  events?: EventMarker[];
  height?: number;
}) {
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(timelines.slice(0, DEFAULT_SHOWN).map((t) => t.teamId)),
  );
  const [hover, setHover] = useState<{
    team: string;
    t: string;
    rating: number;
    color: string;
  } | null>(null);
  const [eventHover, setEventHover] = useState<EventMarker | null>(null);

  const slotOf = useMemo(() => {
    const m = new Map<number, string>();
    timelines.forEach((tl, i) => m.set(tl.teamId, SLOTS[i % SLOTS.length]));
    return m;
  }, [timelines]);

  const shown = timelines.filter((tl) => selected.has(tl.teamId));

  const W = 760;
  const H = height;
  // With an event lane, the top margin holds era titles (y≈12), major event
  // labels (two staggered rows), and the tick band, above the plot itself.
  const hasLane = events.length > 0;
  const M = { top: hasLane ? 58 : 16, right: 110, bottom: 28, left: 46 };
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;

  const allPts = shown.flatMap((tl) => tl.points);
  const t0 = Math.min(...allPts.map((p) => Date.parse(p.t)));
  const t1 = Math.max(...allPts.map((p) => Date.parse(p.t)));
  const r0 = Math.min(1400, ...allPts.map((p) => p.rating));
  const r1 = Math.max(1600, ...allPts.map((p) => p.rating));
  // Compressed time scale, event-granular: each stretch of rated play gets
  // width proportional to its duration, and every quiet gap — the week after
  // a major, the holiday break, the offseason — collapses to a fixed sliver.
  // A line leaves one event and enters the next with no dead run-up between,
  // so there is nowhere to hover that isn't real play.
  const GAP_PX = 8;
  const DAY = 86400_000;
  const rawSpans = (events.length ? events : eras)
    .map((e) => ({ a: Date.parse(e.from), b: Date.parse(e.to) }))
    .filter((s) => s.b > t0 && s.a < t1)
    .map((s) => ({ a: Math.max(s.a, t0), b: Math.min(s.b, t1) }))
    .sort((s, u) => s.a - u.a);
  // Merge spans that overlap or nearly touch (league weeks, co-run events);
  // only genuine multi-day silences earn a sliver.
  const spans: { a: number; b: number }[] = [];
  for (const s of rawSpans) {
    const prev = spans[spans.length - 1];
    if (prev && s.a <= prev.b + 2 * DAY) prev.b = Math.max(prev.b, s.b);
    else spans.push({ ...s });
  }
  if (spans.length) {
    spans[0].a = Math.min(spans[0].a, t0);
    spans[spans.length - 1].b = Math.max(spans[spans.length - 1].b, t1);
  }
  const onDur = spans.reduce((s, sp) => s + (sp.b - sp.a), 0);
  const onIw = iw - GAP_PX * Math.max(spans.length - 1, 0);
  let segCursor = M.left;
  const segs = spans.map((sp) => {
    const w = ((sp.b - sp.a) / (onDur || 1)) * onIw;
    const seg = { ...sp, x: segCursor, w };
    segCursor += w + GAP_PX;
    return seg;
  });
  const xOfTime = (t: number) => {
    if (!segs.length) return M.left + ((t - t0) / (t1 - t0 || 1)) * iw;
    for (let i = 0; i < segs.length; i++) {
      const s = segs[i];
      if (t <= s.b) {
        if (t >= s.a) return s.x + ((t - s.a) / (s.b - s.a || 1)) * s.w;
        const prev = segs[i - 1];
        if (!prev) return s.x;
        return (
          prev.x + prev.w + ((t - prev.b) / (s.a - prev.b || 1)) * GAP_PX
        );
      }
    }
    const last = segs[segs.length - 1];
    return last.x + last.w;
  };
  const x = (t: string) => xOfTime(Date.parse(t));
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
            const xa = xOfTime(a);
            const xb = xOfTime(b);
            // Shading runs boundary-to-boundary (dotted line to dotted line),
            // not just over the era's own events, so the offseason slivers
            // don't punch unshaded gaps into the band.
            const next = eras[i + 1];
            const xEnd = next
              ? xOfTime(Math.min(Math.max(Date.parse(next.from), t0), t1))
              : M.left + iw;
            return (
              <g key={era.title}>
                {i % 2 === 1 && (
                  <rect
                    x={xa}
                    y={M.top}
                    width={xEnd - xa}
                    height={ih}
                    fill="var(--ink)"
                    opacity={0.035}
                  />
                )}
                {/* boundary rule only between eras — the chart edge is its own line */}
                {i > 0 && (
                  <line
                    x1={xa}
                    x2={xa}
                    y1={hasLane ? 6 : M.top}
                    y2={M.top + ih}
                    stroke="var(--baseline)"
                    strokeDasharray="2 4"
                  />
                )}
                <text
                  x={(xa + xb) / 2}
                  y={hasLane ? 12 : M.top + 12}
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
          {/* event lane: majors get a static short label above their tick;
              league phases render as spans, minors as bare ticks — both
              hover-to-identify */}
          {hasLane &&
            (() => {
              const rowA = 27;
              const rowB = 38;
              let lastA = -Infinity;
              let lastB = -Infinity;
              return events.map((ev) => {
                const a = Math.max(Date.parse(ev.from), t0);
                const b = Math.min(Date.parse(ev.to), t1);
                if (!(b >= a)) return null;
                const xa = xOfTime(a);
                const xb = xOfTime(b);
                const mid = (xa + xb) / 2;
                const isSpan = b - a > 21 * 86400_000;
                // Stagger major labels across two rows; when neither row has
                // horizontal room the label is dropped and the event is
                // hover-only, like the minors.
                let labelY: number | null = null;
                if (ev.major) {
                  if (mid - lastA >= 40) {
                    labelY = rowA;
                    lastA = mid;
                  } else if (mid - lastB >= 40) {
                    labelY = rowB;
                    lastB = mid;
                  }
                }
                return (
                  <g
                    key={ev.name}
                    onMouseEnter={() => setEventHover(ev)}
                    onMouseLeave={() => setEventHover(null)}
                  >
                    {isSpan ? (
                      <line
                        x1={xa}
                        x2={xb}
                        y1={48}
                        y2={48}
                        stroke="var(--baseline)"
                        strokeWidth={2}
                      />
                    ) : (
                      <line
                        x1={mid}
                        x2={mid}
                        y1={44}
                        y2={52}
                        stroke={ev.major ? "var(--ink-muted)" : "var(--baseline)"}
                        strokeWidth={ev.major ? 1.5 : 1}
                      />
                    )}
                    {labelY != null && (
                      <text
                        x={mid}
                        y={labelY}
                        textAnchor="middle"
                        fontSize={8.5}
                        fill="var(--ink-muted)"
                        className="font-mono"
                        style={{ letterSpacing: "0.06em" }}
                      >
                        {shortLabel(ev.name)}
                      </text>
                    )}
                    <rect
                      x={Math.min(xa, mid - 8)}
                      y={20}
                      width={Math.max(xb - xa, 16)}
                      height={36}
                      fill="transparent"
                    />
                  </g>
                );
              });
            })()}
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
                x={xOfTime(t)}
                y={H - 8}
                fontSize={10}
                fill="var(--ink-muted)"
              >
                {yr}
              </text>
            );
          })}

          {shown.map((tl) => {
            const color = slotOf.get(tl.teamId)!;
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
                      setHover({ team: tl.team, t: p.t, rating: p.rating, color })
                    }
                    onMouseLeave={() => setHover(null)}
                  />
                ))}
              </g>
            );
          })}

          {/* marker dot tracking the hovered point on its line */}
          {hover && (
            <circle
              cx={x(hover.t)}
              cy={y(hover.rating)}
              r={4.5}
              fill={hover.color}
              stroke="var(--surface)"
              strokeWidth={2}
              pointerEvents="none"
            />
          )}

          {eventHover &&
            (() => {
              const a = Math.max(Date.parse(eventHover.from), t0);
              const b = Math.min(Date.parse(eventHover.to), t1);
              const mid = xOfTime((a + b) / 2);
              const dates = `${eventHover.from.slice(0, 10)} – ${eventHover.to.slice(0, 10)}`;
              const w =
                Math.max(eventHover.name.length, dates.length) * 6.2 + 20;
              const bx = Math.max(M.left, Math.min(mid - w / 2, W - M.right - w));
              return (
                <g pointerEvents="none">
                  <rect
                    x={bx}
                    y={M.top + 4}
                    width={w}
                    height={38}
                    rx={4}
                    fill="var(--surface-raised)"
                    stroke="var(--hairline)"
                  />
                  <text x={bx + 10} y={M.top + 20} fontSize={11} fill="var(--ink)">
                    {eventHover.name}
                  </text>
                  <text
                    x={bx + 10}
                    y={M.top + 35}
                    fontSize={10}
                    fill="var(--ink-secondary)"
                    className="font-mono"
                  >
                    {dates}
                  </text>
                </g>
              );
            })()}
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
        Series-level Elo (K=32, initial 1500) after each rated series.
        {timelines.length > 1 && (
          <> Teams shown are the top {timelines.length} by final rating.</>
        )}{" "}
        The 1500 line is league average by construction. Spec in{" "}
        <a href="/methodology#elo" className="underline">
          methodology
        </a>
        .
      </figcaption>
    </figure>
  );
}
