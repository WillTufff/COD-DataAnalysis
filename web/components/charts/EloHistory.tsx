"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
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
const M = { top: 54, right: 12, bottom: 12, left: 46 };
// Below this container width the chart draws at 1:1 instead of scaling down.
const NARROW = 720;

const LEAGUES = [
  { key: "cdl", label: "CDL", from: 2020, to: Infinity },
  { key: "cwl", label: "CWL", from: 2016, to: 2019 },
  { key: "mlg", label: "MLG", from: 0, to: 2015 },
  { key: "all", label: "All", from: 0, to: Infinity },
] as const;
type LeagueKey = (typeof LEAGUES)[number]["key"];
const leagueOf = (year: number) =>
  LEAGUES.find((l) => l.key !== "all" && year >= l.from && year <= l.to)!;

// Each history trimmed to the visible eras' time range.
function trim(histories: TeamHistory[], a: number, b: number): TeamHistory[] {
  return histories.flatMap((h) => {
    const ks = h.t.flatMap((t, k) => (t >= a && t <= b ? [k] : []));
    if (ks.length < 2) return [];
    return [{ ...h, t: ks.map((k) => h.t[k]), r: ks.map((k) => h.r[k]), rd: ks.map((k) => h.rd[k]) }];
  });
}

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

// The view's strongest teams by mean rating, among those that played a real
// share of it (a quarter of the busiest team's series, and at least 10).
function topTeams(histories: TeamHistory[], n: number): number[] {
  const maxN = Math.max(0, ...histories.map((h) => h.r.length));
  const floor = Math.max(10, 0.25 * maxN);
  return histories
    .filter((h) => h.r.length >= floor)
    .map((h) => ({ id: h.teamId, mean: h.r.reduce((s, v) => s + v, 0) / h.r.length }))
    .sort((a, b) => b.mean - a.mean)
    .slice(0, n)
    .map((t) => t.id);
}

export function EloHistory({
  elo: allElo,
  glicko: allGlicko,
  eras: allEras,
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
  const [league, setLeague] = useState<LeagueKey>("cdl");
  const [boxW, setBoxW] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setBoxW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const lg = LEAGUES.find((l) => l.key === league)!;
  const eras = useMemo(
    () => allEras.filter((e) => e.year >= lg.from && e.year <= lg.to),
    [allEras, lg],
  );
  const [elo, glicko] = useMemo(() => {
    if (!eras.length) return [allElo, allGlicko];
    const a = Date.parse(eras[0].from);
    const b = Date.parse(eras[eras.length - 1].to);
    return [trim(allElo, a, b), allGlicko && trim(allGlicko, a, b)];
  }, [allElo, allGlicko, eras]);
  const [picked, setPicked] = useState<number[]>([]);
  // Color slot per picked team, held until it is unpicked.
  const [slot, setSlot] = useState<Record<number, number>>({});
  const [extras, setExtras] = useState<number[]>([]);
  const [hover, setHover] = useState<{ teamId: number; k: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const canToggle = !!glicko?.length;
  const showBand = canToggle && system === "glicko";
  const source = showBand ? glicko! : elo;
  const byId = useMemo(() => new Map(source.map((h) => [h.teamId, h])), [source]);

  // A single league fits a phone at 1:1; all thirteen games scroll sideways.
  const narrow = boxW > 0 && boxW < NARROW && league !== "all";
  const W = narrow ? Math.max(boxW, 320) : 960;
  const H = narrow ? 360 : height;
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

  // Each era's stretch of a team's line, as point indices [from, to).
  const eraSlice = (h: TeamHistory, era: number) => {
    const c = cols[era];
    const from = h.t.findIndex((t) => t >= c.a);
    let to = h.t.findIndex((t) => t > c.b);
    if (to === -1) to = h.t.length;
    return from === -1 || to <= from ? null : { from, to };
  };

  // Domain: the grey field's 2–99.5% range, widened to hold every highlighted
  // line (and its band) and every champion; only the weakest teams are cut off.
  const [r0, r1] = useMemo(() => {
    const all = source.flatMap((h) => h.r).sort((a, b) => a - b);
    let lo = all[Math.floor(all.length * 0.02)] ?? 1400;
    let hi = all[Math.ceil(all.length * 0.995) - 1] ?? 1600;
    const hold = (h: TeamHistory | undefined, from: number, to: number) => {
      if (!h) return;
      for (let k = from; k < to; k++) {
        const rd = showBand ? (h.rd[k] ?? 0) : 0;
        lo = Math.min(lo, h.r[k] - rd);
        hi = Math.max(hi, h.r[k] + rd);
      }
    };
    if (leaderMode) {
      for (const l of [...leaders, ...champs]) {
        const h = byId.get(l.teamId);
        const sl = h && eraSlice(h, l.era);
        if (sl) hold(h, sl.from, sl.to);
      }
    }
    for (const c of champs) {
      const h = byId.get(c.teamId);
      if (!h) continue;
      let k = -1;
      for (let j = 0; j < h.t.length && h.t[j] <= c.t; j++) k = j;
      if (k !== -1) hold(h, k, k + 1);
    }
    for (const id of picked) {
      const h = source.find((s) => s.teamId === id);
      h?.r.forEach((r, k) => {
        const rd = showBand ? (h.rd[k] ?? 0) : 0;
        lo = Math.min(lo, r - rd);
        hi = Math.max(hi, r + rd);
      });
    }
    return [Math.floor(Math.min(lo, 1400) / 100) * 100, Math.ceil(Math.max(hi, 1600) / 100) * 100];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, showBand, picked, leaderMode, leaders, champs, byId, cols]);
  const y = (r: number) => M.top + ih - ((r - r0) / (r1 - r0)) * ih;


  // The view's top eight, then any team added by clicking its line.
  const pills = useMemo(() => {
    const ids = topTeams(elo, 8);
    for (const id of extras) if (!ids.includes(id)) ids.push(id);
    return ids;
  }, [elo, extras]);
  const slotOf = (id: number) =>
    slot[id] != null ? SLOTS[slot[id] % SLOTS.length] : "var(--ink-muted)";

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
        const sl = h && eraSlice(h, l.era);
        return sl ? [{ h: h!, ...sl, color: "var(--accent)" }] : [];
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
    const on = picked.includes(id);
    setPicked((prev) => (on ? prev.filter((p) => p !== id) : [...prev, id]));
    setSlot((prev) => {
      const next = { ...prev };
      if (on) delete next[id];
      else {
        const used = new Set(Object.values(prev));
        let i = 0;
        while (used.has(i)) i++;
        next[id] = i;
      }
      return next;
    });
    if (!pills.includes(id)) setExtras((prev) => [...prev, id]);
  }

  const rTicks: number[] = [];
  for (let v = r0; v <= r1; v += 100) rTicks.push(v);

  function headerRuns(runs: EraRun[], ty: number, fill: string, what: string) {
    return runs.map((run, i) => {
      const a = cols[run.first];
      const b = cols[run.last];
      const isLast = i === runs.length - 1 && run.last === cols.length - 1;
      // The final run may spill left over the offseason gap, right-aligned to the edge.
      const span = b.x + b.w - a.x + (isLast ? M.right + ERA_GAP : 0);
      const maxChars = Math.floor((span + ERA_GAP - 4) / 5.6);
      const name =
        run.team.length > maxChars ? run.team.slice(0, Math.max(maxChars - 1, 1)) + "…" : run.team;
      const spill = isLast && name.length * 5.6 > b.x + b.w - a.x;
      return (
        <g key={`${ty}-${run.first}`}>
          {run.last > run.first && (
            <line x1={a.x + 4} x2={b.x + b.w - 4} y1={ty + 4} y2={ty + 4} stroke={fill} opacity={0.35} />
          )}
          <text
            x={spill ? W - 2 : a.x + (b.x + b.w - a.x) / 2}
            y={ty}
            textAnchor={spill ? "end" : "middle"}
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
    if (!h || (!leaderMode && !picked.includes(c.teamId))) return [];
    let k = -1;
    for (let j = 0; j < h.t.length && h.t[j] <= c.t; j++) k = j;
    return k === -1 ? [] : [{ ...c, h, k }];
  });


  const hh = hover ? byId.get(hover.teamId) : undefined;
  const hEra = hh ? cols.findIndex((c) => hh.t[hover!.k] <= c.b) : -1;

  return (
    <figure>
      <div className="mb-3 flex flex-wrap items-center gap-1 text-xs">
        {LEAGUES.map((l) => (
          <button
            key={l.key}
            onClick={() => setLeague(l.key)}
            aria-pressed={league === l.key}
            className={`rounded border px-2 py-1 transition-colors ${
              league === l.key
                ? "border-hairline bg-surface-raised text-ink"
                : "border-transparent text-ink-muted hover:text-ink-secondary"
            }`}
          >
            {l.label}
          </button>
        ))}
        {canToggle && <span className="mx-2 h-4 w-px bg-hairline" />}
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
          onClick={() => {
            setPicked([]);
            setSlot({});
          }}
          aria-pressed={leaderMode}
          className={`flex items-center gap-1.5 rounded border px-2 py-1 text-xs transition-colors ${
            leaderMode
              ? "border-hairline bg-surface-raised text-ink"
              : "border-transparent text-ink-muted hover:text-ink-secondary"
          }`}
        >
          <span className="inline-block h-0.5 w-4 bg-accent" />
          Top Elo in each game
          <span className="ml-2 inline-flex items-center">
            <span className="inline-block h-0.5 w-2 bg-ink opacity-75" />
            <span className="inline-block h-2 w-2 rounded-full border-[1.5px] border-ink bg-background" />
            <span className="inline-block h-0.5 w-2 bg-ink opacity-75" />
          </span>
          World champion
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

      <div ref={boxRef} className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className={`w-full cursor-crosshair ${narrow ? "" : "min-w-[720px]"}`}
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
            {v === 1500 && (
              <text
                x={M.left - 8}
                y={y(v) + 13}
                textAnchor="end"
                fontSize={8.5}
                fill="var(--ink-muted)"
                className="font-mono"
              >
                avg
              </text>
            )}
          </g>
        ))}

        {/* league changes, in the all-games view */}
        {league === "all" &&
          cols.map((c, i) => {
            const l = leagueOf(eras[i].year);
            if (i > 0 && leagueOf(eras[i - 1].year) === l) return null;
            const gx = c.x - ERA_GAP / 2;
            return (
              <g key={`lg${l.key}`} pointerEvents="none">
                {i > 0 && (
                  <line x1={gx} x2={gx} y1={M.top} y2={H - M.bottom} stroke="var(--ink-muted)" strokeDasharray="3 3" opacity={0.6} />
                )}
                <text x={c.x + 4} y={M.top + 12} fontSize={9.5} fill="var(--ink-muted)" className="font-mono">
                  {l.label}
                </text>
              </g>
            );
          })}


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
            const sl = eraSlice(h, c.era);
            if (!sl) return null;
            return (
              <path
                key={`c${c.era}`}
                d={pathOf(h, sl.from, sl.to).d}
                fill="none"
                stroke="var(--ink)"
                strokeWidth={1.25}
                opacity={0.75}
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
            <circle
              key={`ring${c.era}`}
              cx={cx}
              cy={cy}
              r={5}
              fill="var(--background)"
              stroke="var(--ink)"
              strokeWidth={1.5}
              pointerEvents="none"
            />
          );
        })}



        {!leaderMode &&
          picked.map((id) => {
            const h = byId.get(id);
            if (!h) return null;
            const k = h.t.length - 1;
            const lx = xOf(h.t[k]);
            const ly = endLabels.get(id) ?? y(h.r[k]);
            // Lines that run to the right edge get their label on the inside.
            const inside = lx + 6 + h.team.length * 5.8 > W;
            return (
              <text
                key={`l${id}`}
                x={inside ? lx - 8 : lx + 6}
                y={ly + 3.5}
                textAnchor={inside ? "end" : "start"}
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
        Grey lines are teams with five or more rated series; each column is one
        game title, and the top Elo is the highest mean rating across it. Click
        any line to follow it. Details in the{" "}
        <Link href="/methodology/elo" className="underline">
          methodology
        </Link>
        .
      </figcaption>
    </figure>
  );
}
