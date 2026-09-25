import Link from "next/link";
import type { FeedItem, MetricRow } from "@/lib/analytics";

type D = Record<string, unknown>;
const num = (d: D, k: string) => Number(d[k]);
const pct = (v: number) => Math.round(v * 100);
const ord = (p: number) => {
  const n = pct(p);
  const s = n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd" : n % 10 === 3 && n !== 13 ? "rd" : "th";
  return `${n}${s}`;
};
const fmtVal = (v: number) => (v <= 1.5 ? `${pct(v)}%` : Math.round(v).toLocaleString("en-US"));
// The headline was rounded upstream; reuse its figure so the two never disagree.
const sdOf = (f: FeedItem) => f.headline.match(/([\d.]+) SD/)?.[1] ?? Math.abs(num(f.detail, "z")).toFixed(1);
const short = (mode: string) => (mode === "Search & Destroy" ? "SnD" : mode);
const B = ({ children }: { children: React.ReactNode }) => (
  <strong className="font-semibold text-ink tabular-nums">{children}</strong>
);

function Hero({ f, cohort }: { f: FeedItem; cohort: MetricRow[] }) {
  const d = f.detail;
  const value = num(d, "value");
  const mode = String(d.mode);
  const label = String(d.metric_label);
  const ranked = [...cohort].sort((a, b) => b.value - a.value);
  const next = ranked.find((r) => r.handle !== f.subjectName);

  const bins = 22;
  const vals = cohort.map((r) => r.value);
  const lo = Math.min(...vals, value);
  const hi = Math.max(...vals, value);
  const bin = (v: number) => Math.min(bins - 1, Math.floor(((v - lo) / (hi - lo || 1)) * bins));
  const stacks = new Map<number, number>();
  const dots = [...cohort]
    .sort((a, b) => a.value - b.value)
    .map((r) => {
      const b = bin(r.value);
      const k = stacks.get(b) ?? 0;
      stacks.set(b, k + 1);
      return { ...r, b, k };
    });
  const tall = Math.max(...stacks.values(), 1);
  const W = 640;
  const pad = { l: 4, r: 4, t: 34, b: 30 };
  const step = (W - pad.l - pad.r) / bins;
  const rad = step / 2 - 1.5;
  const H = pad.t + pad.b + tall * (rad * 2 + 3);
  const cx = (b: number) => pad.l + step * (b + 0.5);
  const cy = (k: number) => H - pad.b - rad - k * (rad * 2 + 3);
  const mean = vals.reduce((a, b) => a + b, 0) / (vals.length || 1);
  const meanX = pad.l + ((mean - lo) / (hi - lo || 1)) * (W - pad.l - pad.r);
  const me = dots.find((p) => p.handle === f.subjectName);
  const nextTop = next && dots.filter((p) => p.b === bin(next.value)).at(-1);

  return (
    <div>
      <h3 className="lower-third">
        <span className="font-mono text-ink-muted">01</span>&ensp;{f.subjectName}, {short(mode)} {label.toLowerCase()}
      </h3>
      <p className="mt-5 max-w-xl text-2xl leading-snug text-ink-secondary sm:text-[1.7rem]">
        <B>{fmtVal(value)}</B> {label.toLowerCase()} in {String(d.year)} {mode}, {sdOf(f)} standard deviations
        above the field.
        {next && (
          <>
            {" "}
            Next best: {next.handle}, <B>{fmtVal(next.value)}</B>.
          </>
        )}
      </p>
      {dots.length > 0 && (
        <svg viewBox={`0 0 ${W} ${H}`} className="mt-8 w-full" role="img" aria-label={`${label}, every qualified player`}>
          <line x1={pad.l} x2={W - pad.r} y1={H - pad.b + 5} y2={H - pad.b + 5} stroke="var(--hairline)" />
          <line
            x1={meanX}
            x2={meanX}
            y1={pad.t - 10}
            y2={H - pad.b + 5}
            stroke="var(--ink-muted)"
            strokeDasharray="2 3"
          />
          <text x={meanX + 6} y={pad.t - 14} className="fill-ink-muted font-mono text-[11px]">
            avg {fmtVal(mean)}
          </text>
          {dots.map((p) => (
            <circle
              key={p.playerId}
              cx={cx(p.b)}
              cy={cy(p.k)}
              r={rad}
              fill={p === me ? "var(--good)" : "var(--ink-muted)"}
              opacity={p === me ? 1 : 0.3}
            >
              <title>
                {p.handle}: {fmtVal(p.value)}
              </title>
            </circle>
          ))}
          {[me, nextTop].map(
            (p) =>
              p && (
                <text
                  key={p.playerId}
                  x={cx(p.b)}
                  y={cy(p.k) - rad - 7}
                  textAnchor={p === me ? "end" : "middle"}
                  className={`text-[12px] ${p === me ? "fill-ink font-medium" : "fill-ink-muted"}`}
                >
                  {p === me ? f.subjectName : next?.handle}
                </text>
              ),
          )}
          <text x={pad.l} y={H - 6} className="fill-ink-muted font-mono text-[11px]">
            {fmtVal(lo)}
          </text>
          <text x={W - pad.r} y={H - 6} textAnchor="end" className="fill-ink-muted font-mono text-[11px]">
            {fmtVal(hi)}
          </text>
        </svg>
      )}
      <div className="mt-3 flex justify-between text-xs text-ink-muted">
        <span>One dot per qualified player this season.</span>
        {f.subjectSlug && (
          <Link href={`/players/${f.subjectSlug}`} className="hover:text-accent">
            {f.subjectName} →
          </Link>
        )}
      </div>
    </div>
  );
}

function Row({
  n,
  label,
  href,
  children,
  viz,
}: {
  n: number;
  label: string;
  href: string;
  children: React.ReactNode;
  viz: React.ReactNode;
}) {
  return (
    <li className="border-b border-hairline/60">
      <Link href={href} className="group grid grid-cols-[1.75rem_1fr] gap-x-2 py-5">
        <span className="pt-0.5 font-mono text-[11px] text-ink-muted">{String(n).padStart(2, "0")}</span>
        <div>
          <span className="text-xs font-medium text-ink-muted transition-colors group-hover:text-accent">{label}</span>
          <p className="mt-1 text-[15px] leading-snug text-ink-secondary">{children}</p>
          <div className="mt-3">{viz}</div>
        </div>
      </Link>
    </li>
  );
}

const Bar = ({ v, mark }: { v: number; mark?: string }) => (
  <div className="h-1.5 flex-1 bg-hairline/50">
    <div className={`h-full ${mark ?? "bg-ink-muted/50"}`} style={{ width: `${Math.max(v * 100, 1.5)}%` }} />
  </div>
);

function Split({ f, n }: { f: FeedItem; n: number }) {
  const d = f.detail;
  const kd = num(d, "kd_pctl");
  const q = num(d, "quality_pctl");
  const label = String(d.metric_label);
  return (
    <Row
      n={n}
      label={`${f.subjectName}, ${short(String(d.mode))}`}
      href={f.subjectSlug ? `/players/${f.subjectSlug}` : "/findings"}
      viz={
        <div className="space-y-1.5 text-[11px] text-ink-muted">
          {[
            { l: "K/D", v: kd },
            { l: label, v: q, mark: "bg-critical" },
          ].map((b) => (
            <div key={b.l} className="flex items-center gap-3">
              <span className="w-24 truncate">{b.l}</span>
              <Bar v={b.v} mark={b.mark} />
            </div>
          ))}
        </div>
      }
    >
      <B>{ord(kd)}</B> percentile K/D in {String(d.year)} {String(d.mode)}, <B>{ord(q)}</B> percentile{" "}
      {label.toLowerCase()}.
    </Row>
  );
}

function Extreme({ f, n }: { f: FeedItem; n: number }) {
  const d = f.detail;
  const z = num(d, "z");
  const at = Math.max(0, Math.min(1, (z + 4) / 8));
  return (
    <Row
      n={n}
      label={`${f.subjectName}, ${short(String(d.mode))}`}
      href={f.subjectSlug ? `/players/${f.subjectSlug}` : "/findings"}
      viz={
        <div>
          <div className="relative h-1.5 bg-hairline/50">
            <div className="absolute -inset-y-1 left-1/2 w-px bg-ink-muted" />
            <div className="absolute -inset-y-1.5 w-1 bg-good" style={{ left: `calc(${at * 100}% - 2px)` }} />
          </div>
          <div className="mt-1.5 flex justify-between font-mono text-[10px] text-ink-muted">
            <span>−4 SD</span>
            <span>avg</span>
            <span>+4 SD</span>
          </div>
        </div>
      }
    >
      <B>{fmtVal(num(d, "value"))}</B> {String(d.metric_label).toLowerCase()} in {String(d.mode)}, the best
      this season and {sdOf(f)} SD above average.
    </Row>
  );
}

function WhatWins({ f, siblings, n }: { f: FeedItem; siblings: FeedItem[]; n: number }) {
  const d = f.detail;
  const rows = siblings
    .map((s) => ({ mode: String(s.detail.mode), v: num(s.detail, "rest_vs_slay") }))
    .sort((a, b) => b.v - a.v);
  const max = Math.max(...rows.map((r) => r.v), 1);
  const others = rows.filter((r) => r.mode !== d.mode);
  return (
    <Row
      n={n}
      label={`What wins maps, ${String(d.year)}`}
      href="/methodology/player-rating"
      viz={
        <div className="space-y-1.5 text-[11px] text-ink-muted">
          {rows.map((r) => (
            <div key={r.mode} className="flex items-center gap-3">
              <span className="w-24 truncate">{r.mode}</span>
              <div className="relative flex flex-1">
                <Bar v={r.v / max} mark={r.mode === d.mode ? "bg-good" : undefined} />
                <div className="absolute -inset-y-0.5 w-px bg-ink-secondary" style={{ left: `${(1 / max) * 100}%` }} />
              </div>
              <span className="w-8 text-right font-mono">{r.v.toFixed(1)}×</span>
            </div>
          ))}
        </div>
      }
    >
      In {String(d.mode)}, objective play was worth <B>{num(d, "rest_vs_slay").toFixed(1)}×</B> as much as
      slaying.
      {others.length > 0 && <> In {others.map((o) => short(o.mode)).join(" and ")}, the gunfight decided maps.</>}
    </Row>
  );
}

function H2H({ f, n }: { f: FeedItem; n: number }) {
  const total = num(f.detail, "n");
  const w = num(f.detail, "wins");
  return (
    <Row
      n={n}
      label={`${f.subjectName} vs ${String(f.detail.opponent)}`}
      href={f.subjectSlug ? `/teams/${f.subjectSlug}` : "/findings"}
      viz={
        <div className="flex gap-[3px]">
          {Array.from({ length: total }, (_, i) => (
            <div key={i} className={`h-3 flex-1 ${i < w ? "bg-ink-muted/50" : "bg-critical"}`} />
          ))}
        </div>
      }
    >
      <B>
        {w}–{total - w}
      </B>{" "}
      in series, all-time.
    </Row>
  );
}

export function FindingCards({ items, cohort }: { items: FeedItem[]; cohort: MetricRow[] }) {
  const pick = (k: string) => items.find((i) => i.kind === k);
  const [hero, second] = items.filter((i) => i.kind === "profile_extreme");
  const split = pick("intangible_outlier");
  const wins = pick("what_wins");
  const h2h = pick("h2h_edge");
  const rows = [
    split && ((n: number) => <Split key="split" f={split} n={n} />),
    second && ((n: number) => <Extreme key="extreme" f={second} n={n} />),
    wins && ((n: number) => (
      <WhatWins key="wins" f={wins} siblings={items.filter((i) => i.kind === "what_wins")} n={n} />
    )),
    h2h && ((n: number) => <H2H key="h2h" f={h2h} n={n} />),
  ].filter((r) => !!r);
  return (
    <div className="grid grid-cols-1 gap-x-14 gap-y-10 lg:grid-cols-[1.4fr_1fr]">
      {hero && <Hero f={hero} cohort={cohort} />}
      <ol className="-mt-5 border-hairline/60 lg:border-l lg:pl-10">
        {rows.map((r, i) => r(i + (hero ? 2 : 1)))}
      </ol>
    </div>
  );
}
