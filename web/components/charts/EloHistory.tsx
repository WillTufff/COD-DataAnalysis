"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import type { EraSpan, SeasonChampion, TeamHistory } from "@/lib/analytics";

// Series slots minus the two greens, which read as the accent.
const SLOTS = [
  "var(--series-1)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
];

const DAY = 86400_000;
// A team that sits out this long gets a break in its line, not a connector.
const BREAK_DAYS = 60;
const ERA_GAP = 10;
const ERA_MIN_W = 44;
const M = { top: 54, right: 96, bottom: 12, left: 46 };

type Leader = { era: number; teamId: number; team: string; mean: number };
type EraRun = { teamId: number; team: string; first: number; last: number };

// Consecutive eras held by the same team collapse into one run.
function runsOf(items: { era: number; teamId: number; team: string }[]): EraRun[] {
  const out: EraRun[] = [];
  for (const it of items) {
    const prev = out[out.length - 1];
    if (prev && prev.teamId === it.teamId && prev.last === it.era - 1) prev.last = it.era;
    else out.push({ teamId: it.teamId, team: it.team, first: it.era, last: it.era });
  }
  return out;
}

// Highest mean post-series rating within the era, among teams that played a
// real share of it (at least 40% of the era's busiest team, and 5 series).
function eraLeaders(histories: TeamHistory[], eras: EraSpan[]): Leader[] {
  const out: Leader[] = [];
  eras.forEach((era, i) => {
    const a = Date.parse(era.from);
    const b = Date.parse(era.to);
    const stats = histories
      .map((h) => {
        let n = 0;
        let sum = 0;
        h.t.forEach((t, k) => {
          if (t >= a && t <= b) {
            n++;
            sum += h.r[k];
          }
        });
        return { h, n, mean: n ? sum / n : 0 };
      })
      .filter((s) => s.n > 0);
    const maxN = Math.max(0, ...stats.map((s) => s.n));
    const floor = Math.max(5, 0.4 * maxN);
    const best = stats
      .filter((s) => s.n >= floor)
      .sort((p, q) => q.mean - p.mean)[0];
    if (best) out.push({ era: i, teamId: best.h.teamId, team: best.h.team, mean: best.mean });
  });
  return out;
}

export function EloHistory({
  elo,
  glicko,
  eras,
  champions = [],
  height = 440,
}: {
  elo: TeamHistory[];
  /** Same teams under Glicko-2; enables the system toggle and the ±RD band. */
  glicko?: TeamHistory[];
  eras: EraSpan[];
  champions?: SeasonChampion[];
  height?: number;
}) {
  const [system, setSystem] = useState<"elo" | "glicko">("elo");
  const [picked, setPicked] = useState<number[]>([]);
  const [extras, setExtras] = useState<number[]>([]);
  const [hover, setHover] = useState<{ teamId: number; k: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const canToggle = !!glicko?.length;
  const showBand = canToggle && system === "glicko";
  const source = showBand ? glicko! : elo;
  const byId = useMemo(() => new Map(source.map((h) => [h.teamId, h])), [source]);

  const W = 960;
  const H = height;
  const iw = W - M.left - M.right;
  const ih = H - M.top - M.bottom;

  // Era columns: width follows sqrt(series played), time runs linearly inside
  // each column, and the offseason between columns is a fixed gap.
  const cols = useMemo(() => {
    const raw = eras.map((e) => Math.sqrt(e.seriesCount));
    const free = iw - ERA_GAP * Math.max(eras.length - 1, 0);
    let w = raw.map((v) => Math.max(ERA_MIN_W, (v / raw.reduce((s, u) => s + u, 0)) * free));
    const scale = free / w.reduce((s, u) => s + u, 0);
    w = w.map((v) => v * scale);
    const out: { a: number; b: number; x: number; w: number }[] = [];
    for (let i = 0, x = M.left; i < eras.length; x += w[i] + ERA_GAP, i++)
      out.push({ a: Date.parse(eras[i].from), b: Date.parse(eras[i].to), x, w: w[i] });
    return out;
  }, [eras, iw]);

  const xOf = (t: number) => {
    for (let i = 0; i < cols.length; i++) {
      const c = cols[i];
      if (t < c.a) return i === 0 ? c.x : c.x - ERA_GAP / 2;
      if (t <= c.b) return c.x + ((t - c.a) / (c.b - c.a || 1)) * c.w;
    }
    const last = cols[cols.length - 1];
    return last ? last.x + last.w : M.left;
  };

  // Domain: the grey field's 0.5–99.5% range, widened to hold every picked
  // team (and its band); the few extreme newcomers are clipped.
  const [r0, r1] = useMemo(() => {
    const all = source.flatMap((h) => h.r).sort((a, b) => a - b);
    let lo = all[Math.floor(all.length * 0.005)] ?? 1400;
    let hi = all[Math.ceil(all.length * 0.995) - 1] ?? 1600;
    for (const id of picked) {
      const h = source.find((s) => s.teamId === id);
      h?.r.forEach((r, k) => {
        const rd = showBand ? (h.rd[k] ?? 0) : 0;
        lo = Math.min(lo, r - rd);
        hi = Math.max(hi, r + rd);
      });
    }
    return [Math.floor(Math.min(lo, 1400) / 100) * 100, Math.ceil(Math.max(hi, 1600) / 100) * 100];
  }, [source, showBand, picked]);
  const y = (r: number) => M.top + ih - ((r - r0) / (r1 - r0)) * ih;

  const leaders = useMemo(() => eraLeaders(elo, eras), [elo, eras]);
  const leaderMode = picked.length === 0;
  const leaderRuns = useMemo(() => runsOf(leaders), [leaders]);
  const champs = useMemo(
    () =>
      champions.flatMap((c) => {
        const era = eras.findIndex((e) => e.year === c.year);
        return era === -1 ? [] : [{ ...c, era }];
      }),
    [champions, eras],
  );
  // A thin season, by series count, is flagged in the header as partial.
  const medianSeries = useMemo(() => {
    const n = eras.map((e) => e.seriesCount).sort((a, b) => a - b);
    return n.length ? n[Math.floor(n.length / 2)] : 0;
  }, [eras]);

  // Pill order fixes each team's color slot: era leaders first, then any team
  // added by clicking its line. A slot never changes once assigned.
  const pills = useMemo(() => {
    const seen = new Set<number>();
    const ids: number[] = [];
    for (const l of leaders)
      if (!seen.has(l.teamId)) {
        seen.add(l.teamId);
        ids.push(l.teamId);
      }
    for (const id of extras)
      if (!seen.has(id)) {
        seen.add(id);
        ids.push(id);
      }
    return ids;
  }, [leaders, extras]);
  const slotOf = (id: number) => SLOTS[Math.max(pills.indexOf(id), 0) % SLOTS.length];

  // Path pieces for points k in [from, to), split wherever a team sat out.
  function pathOf(h: TeamHistory, from = 0, to = h.t.length) {
    let d = "";
    const gaps: [number, number][] = [];
    for (let k = from; k < to; k++) {
      const brk = k === from || h.t[k] - h.t[k - 1] > BREAK_DAYS * DAY;
      if (brk && k > from) gaps.push([k - 1, k]);
      d += `${brk ? "M" : "L"}${xOf(h.t[k]).toFixed(1)},${y(h.r[k]).toFixed(1)}`;
    }
    return { d, gaps };
  }

  function bandOf(h: TeamHistory, from = 0, to = h.t.length) {
    const ks: number[] = [];
    for (let k = from; k < to; k++) if (h.rd[k] != null) ks.push(k);
    if (ks.length < 2) return null;
    const top = ks.map((k, i) => `${i ? "L" : "M"}${xOf(h.t[k])},${y(h.r[k] + h.rd[k]!)}`);
    const bot = ks.reverse().map((k) => `L${xOf(h.t[k])},${y(h.r[k] - h.rd[k]!)}`);
    return top.join("") + bot.join("") + "Z";
  }

  const context = useMemo(
    () => source.map((h) => pathOf(h).d).join(""),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [source, cols, r0, r1],
  );

  // Highlighted runs: whole careers for picked teams, or each era leader's
  // stretch inside its own era.
  const runs = leaderMode
    ? leaders.flatMap((l) => {
        const h = byId.get(l.teamId);
        if (!h) return [];
        const c = cols[l.era];
        const from = h.t.findIndex((t) => t >= c.a);
        let to = h.t.findIndex((t) => t > c.b);
        if (to === -1) to = h.t.length;
        return from === -1 || to <= from ? [] : [{ h, from, to, color: "var(--accent)" }];
      })
    : picked.flatMap((id) => {
        const h = byId.get(id);
        return h ? [{ h, from: 0, to: h.t.length, color: slotOf(id) }] : [];
      });

  const endLabels = useMemo(() => {
    if (leaderMode) return new Map<number, number>();
    const ends = picked
      .map((id) => byId.get(id))
      .filter((h): h is TeamHistory => !!h)
      .map((h) => ({ id: h.teamId, y: y(h.r[h.r.length - 1]) }))
      .sort((a, b) => a.y - b.y);
    const out = new Map<number, number>();
    let prev = -Infinity;
    for (const e of ends) {
      const placed = Math.max(e.y, prev + 12);
      out.set(e.id, placed);
      prev = placed;
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leaderMode, picked, byId, r0, r1]);

  // Screen positions for nearest-point hover, across every team.
  const screen = useMemo(
    () =>
      source.map((h) => ({
        id: h.teamId,
        xs: h.t.map((t) => xOf(t)),
        ys: h.r.map((r) => y(r)),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [source, cols, r0, r1],
  );
  const highlighted = new Set(runs.map((r) => r.h.teamId));

  function onMove(e: React.PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    const ctm = svg?.getScreenCTM();
    if (!svg || !ctm) return;
    const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
    let best: { teamId: number; k: number } | null = null;
    let bestD = 14 * 14;
    for (const s of screen) {
      // Highlighted lines win ties against the grey field under them.
      const bias = highlighted.has(s.id) ? 0.35 : 1;
      for (let k = 0; k < s.xs.length; k++) {
        const dx = s.xs[k] - pt.x;
        if (dx > 14 || dx < -14) continue;
        const dy = s.ys[k] - pt.y;
        const d = (dx * dx + dy * dy) * bias;
        if (d < bestD) {
          bestD = d;
          best = { teamId: s.id, k };
        }
      }
    }
    setHover(best);
  }

  function toggle(id: number) {
    setPicked((prev) => (prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]));
    if (!pills.includes(id)) setExtras((prev) => [...prev, id]);
  }

  const rTicks: number[] = [];
  for (let v = r0; v <= r1; v += 100) rTicks.push(v);

  function headerRuns(runs: EraRun[], ty: number, fill: string, what: string) {
    return runs.map((run, i) => {
      const a = cols[run.first];
      const b = cols[run.last];
      const isLast = i === runs.length - 1 && run.last === cols.length - 1;
      // The final run may spill into the right margin rather than truncate.
      const span = b.x + b.w - a.x + (isLast ? M.right - 8 : 0);
      const pad = 0;
      const maxChars = Math.floor((span - pad + ERA_GAP - 4) / 5.6);
      const name =
        run.team.length > maxChars ? run.team.slice(0, Math.max(maxChars - 1, 1)) + "…" : run.team;
      const tx = isLast ? a.x + pad / 2 : a.x + (b.x + b.w - a.x) / 2;
      const anchor = isLast && run.team.length * 5.6 > b.x + b.w - a.x ? "start" : "middle";
      return (
        <g key={`${ty}-${run.first}`}>
          {run.last > run.first && (
            <line x1={a.x + 4} x2={b.x + b.w - 4} y1={ty + 4} y2={ty + 4} stroke={fill} opacity={0.35} />
          )}
          <text
            x={anchor === "start" ? a.x + pad : tx + pad / 2}
            y={ty}
            textAnchor={anchor}
            fontSize={9.5}
            fill={fill}
            stroke="var(--background)"
            strokeWidth={3}
            paintOrder="stroke"
          >
            <title>{`${run.team}: ${what}`}</title>
            {name}
          </text>
        </g>
      );
    });
  }

  // Each champion's point at its title, or its last rated series before it.
  const rings = champs.flatMap((c) => {
    const h = byId.get(c.teamId);
    if (!h) return [];
    let k = -1;
    for (let j = 0; j < h.t.length && h.t[j] <= c.t; j++) k = j;
    return k === -1 ? [] : [{ ...c, h, k }];
  });

  const clipped = source.some((h) => h.r.some((r) => r < r0 || r > r1));

  const hh = hover ? byId.get(hover.teamId) : undefined;
  const hEra = hh ? cols.findIndex((c) => hh.t[hover!.k] <= c.b) : -1;

  return (
    <figure>
      <div className="mb-3 flex flex-wrap items-center gap-1 text-xs">
        {canToggle &&
          (["elo", "glicko"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setSystem(s)}
              aria-pressed={system === s}
              className={`rounded border px-2 py-1 transition-colors ${
                system === s
                  ? "border-hairline bg-surface-raised text-ink"
                  : "border-transparent text-ink-muted hover:text-ink-secondary"
              }`}
            >
              {s === "elo" ? "Elo" : "Glicko-2 ± RD"}
            </button>
          ))}
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        <button
          onClick={() => setPicked([])}
          aria-pressed={leaderMode}
          className={`flex items-center gap-1.5 rounded border px-2 py-1 text-xs transition-colors ${
            leaderMode
              ? "border-hairline bg-surface-raised text-ink"
              : "border-transparent text-ink-muted hover:text-ink-secondary"
          }`}
        >
          <span className="inline-block h-2 w-2 rounded-full bg-accent" />
          Highest-rated
        </button>
        {pills.map((id) => {
          const h = byId.get(id);
          if (!h) return null;
          const on = picked.includes(id);
          return (
            <button
              key={id}
              onClick={() => toggle(id)}
              aria-pressed={on}
              className={`flex items-center gap-1.5 rounded border px-2 py-1 text-xs transition-colors ${
                on
                  ? "border-hairline bg-surface-raised text-ink"
                  : "border-transparent text-ink-muted hover:text-ink-secondary"
              }`}
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: slotOf(id), opacity: on ? 1 : 0.6 }}
              />
              {h.team}
            </button>
          );
        })}
      </div>

      <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="w-full min-w-[720px] cursor-crosshair"
        role="img"
        aria-label="Rating of every team across every Call of Duty title, one column per game, with each game's highest-rated team, its world champion, or selected teams highlighted"
        onPointerMove={onMove}
        onPointerLeave={() => setHover(null)}
        onClick={() => hover && toggle(hover.teamId)}
      >
        {cols.map((c, i) => {
          const era = eras[i];
          return (
            <g key={era.title + era.year}>
              {i % 2 === 1 && (
                <rect x={c.x} y={M.top} width={c.w} height={ih} fill="var(--ink)" opacity={0.035} />
              )}
              <text
                x={c.x + c.w / 2}
                y={14}
                textAnchor="middle"
                fontSize={11}
                fill="var(--ink-secondary)"
                className="font-display"
                style={{ letterSpacing: "0.1em", textTransform: "uppercase" }}
              >
                {era.title}
              </text>
              <text
                x={c.x + c.w / 2}
                y={27}
                textAnchor="middle"
                fontSize={9.5}
                fill="var(--ink-muted)"
                className="font-mono"
              >
                {era.year}
              </text>
              {era.seriesCount < medianSeries / 2 && (
                <text
                  x={c.x + c.w / 2}
                  y={H - M.bottom - 6}
                  textAnchor="middle"
                  fontSize={9}
                  fill="var(--ink-muted)"
                  className="font-mono"
                >
                  <title>{`${era.seriesCount} rated series archived for this game`}</title>
                  partial
                </text>
              )}
            </g>
          );
        })}

        {/* one label per run of consecutive eras held by the same team */}
        {leaderMode && headerRuns(leaderRuns, 41, "var(--accent)", "highest mean rating")}

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

        <defs>
          <clipPath id="elo-history-plot">
            <rect x={M.left} y={M.top} width={W - M.left - M.right + 4} height={ih} />
          </clipPath>
        </defs>
        <g clipPath="url(#elo-history-plot)">
        <path d={context} fill="none" stroke="var(--ink-muted)" strokeWidth={1} opacity={0.16} />

        {hh && !highlighted.has(hh.teamId) && (
          <path
            d={pathOf(hh).d}
            fill="none"
            stroke="var(--ink-secondary)"
            strokeWidth={1.5}
            pointerEvents="none"
          />
        )}

        {/* in the default view, each champion's own season when it was not
            also the highest-rated team */}
        {leaderMode &&
          champs.map((c) => {
            const lead = leaders.find((l) => l.era === c.era);
            const h = byId.get(c.teamId);
            if (!h || lead?.teamId === c.teamId) return null;
            const col = cols[c.era];
            const from = h.t.findIndex((t) => t >= col.a);
            let to = h.t.findIndex((t) => t > col.b);
            if (to === -1) to = h.t.length;
            if (from === -1 || to <= from) return null;
            return (
              <path
                key={`c${c.era}`}
                d={pathOf(h, from, to).d}
                fill="none"
                stroke="var(--ink-secondary)"
                strokeWidth={1.25}
                opacity={0.8}
                pointerEvents="none"
              />
            );
          })}

        {showBand &&
          runs.map((run, i) => {
            const d = bandOf(run.h, run.from, run.to);
            return d ? (
              <path key={`b${i}`} d={d} fill={run.color} opacity={0.14} stroke="none" />
            ) : null;
          })}

        {runs.map((run, i) => {
          const { d, gaps } = pathOf(run.h, run.from, run.to);
          return (
            <g key={`r${i}`} pointerEvents="none">
              <path d={d} fill="none" stroke={run.color} strokeWidth={2} />
              {/* dotted hop across each break */}
              {!leaderMode &&
                gaps.map(([p, q]) => (
                <line
                  key={p}
                  x1={xOf(run.h.t[p])}
                  y1={y(run.h.r[p])}
                  x2={xOf(run.h.t[q])}
                  y2={y(run.h.r[q])}
                  stroke={run.color}
                  strokeWidth={1}
                  strokeDasharray="2 3"
                  opacity={0.6}
                />
                ))}
            </g>
          );
        })}

        </g>

        {rings.map((c) => {
          const cx = xOf(c.h.t[c.k]);
          const cy = y(c.h.r[c.k]);
          return (
            <g key={`ring${c.era}`} pointerEvents="none">
              <circle cx={cx} cy={cy} r={5} fill="var(--background)" stroke="var(--ink)" strokeWidth={1.5} />
              <text
                x={Math.min(cx, W - M.right + 40)}
                y={cy - 9}
                textAnchor="middle"
                fontSize={9}
                fill="var(--ink-secondary)"
                stroke="var(--background)"
                strokeWidth={3}
                paintOrder="stroke"
              >
                {c.team}
              </text>
            </g>
          );
        })}

        {clipped && (
          <text
            x={W - M.right - 4}
            y={H - M.bottom - 6}
            textAnchor="end"
            fontSize={9}
            fill="var(--ink-muted)"
            className="font-mono"
          >
            axis clipped at {r0}–{r1}
          </text>
        )}

        {!leaderMode &&
          picked.map((id) => {
            const h = byId.get(id);
            if (!h) return null;
            const k = h.t.length - 1;
            const lx = xOf(h.t[k]);
            const ly = endLabels.get(id) ?? y(h.r[k]);
            return (
              <text
                key={`l${id}`}
                x={lx + 6}
                y={ly + 3.5}
                fontSize={10.5}
                fill="var(--ink-secondary)"
                stroke="var(--background)"
                strokeWidth={3}
                paintOrder="stroke"
                pointerEvents="none"
              >
                {h.team}
              </text>
            );
          })}

        {hh &&
          (() => {
            const k = hover!.k;
            const px = xOf(hh.t[k]);
            const py = y(hh.r[k]);
            const color = highlighted.has(hh.teamId)
              ? (runs.find((r) => r.h.teamId === hh.teamId)?.color ?? "var(--ink)")
              : "var(--ink-secondary)";
            const bw = 200;
            const bx = px + 12 + bw > W - 4 ? px - 12 - bw : px + 12;
            const by = Math.max(M.top, Math.min(py - 44, H - M.bottom - 52));
            const rd = showBand && hh.rd[k] != null ? ` ± ${hh.rd[k]}` : "";
            const era = hEra >= 0 ? eras[hEra] : null;
            return (
              <g pointerEvents="none">
                <circle cx={px} cy={py} r={4.5} fill={color} stroke="var(--surface)" strokeWidth={2} />
                <rect
                  x={bx}
                  y={by}
                  width={bw}
                  height={52}
                  rx={4}
                  fill="var(--surface-raised)"
                  stroke="var(--hairline)"
                />
                <text x={bx + 10} y={by + 16} fontSize={11} fill="var(--ink)">
                  {hh.team}
                </text>
                <text x={bx + 10} y={by + 31} fontSize={11} fill="var(--ink-secondary)" className="font-mono">
                  {hh.r[k].toFixed(0)}
                  {rd} · {new Date(hh.t[k]).toISOString().slice(0, 10)}
                </text>
                <text x={bx + 10} y={by + 45} fontSize={10} fill="var(--ink-muted)">
                  {era ? `${era.title} ${era.year}` : ""}
                  {rings.some((c) => c.teamId === hh.teamId && c.k === k) ? " · champion" : ""}
                  {highlighted.has(hh.teamId) ? "" : " · click to highlight"}
                </text>
              </g>
            );
          })()}
      </svg>
      </div>

      <figcaption className="mt-1 text-xs text-ink-muted">
        {showBand
          ? "Series-level Glicko-2 after each rated series; the band is the rating deviation. "
          : "Series-level Elo after each rated series. "}
        Grey lines are every team with five or more rated series; each column is
        one game. Green marks the highest mean rating in each game, and a ring
        marks the world champion. Click any line to follow it. Spec in{" "}
        <Link href="/methodology/elo" className="underline">
          methodology
        </Link>
        .
      </figcaption>
    </figure>
  );
}
