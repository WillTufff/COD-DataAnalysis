"use client";

import Link from "next/link";
import { useState } from "react";
import type { SeasonLeader } from "./queries";

type Stat = {
  key: string;
  label: string;
  get: (r: SeasonLeader) => number | null;
  fmt: (v: number) => string;
  note?: string;
};

const STATS: Stat[] = [
  { key: "rating", label: "Rating", get: (r) => r.rating, fmt: (v) => v.toFixed(3), note: "Player rating across all modes; 1.000 is league average." },
  { key: "kd", label: "K/D", get: (r) => r.kd, fmt: (v) => v.toFixed(2) },
  { key: "hp", label: "Hardpoint K/D", get: (r) => r.hpKd, fmt: (v) => v.toFixed(2) },
  { key: "snd", label: "S&D K/D", get: (r) => r.sndKd, fmt: (v) => v.toFixed(2) },
  { key: "dmg", label: "Damage / map", get: (r) => r.dmg, fmt: (v) => Math.round(v).toLocaleString("en-US") },
  { key: "hill", label: "Hill time / HP", get: (r) => r.hill, fmt: (v) => `${Math.floor(v / 60)}:${String(Math.round(v % 60)).padStart(2, "0")}` },
  { key: "fb", label: "First bloods / S&D", get: (r) => r.sndFb, fmt: (v) => v.toFixed(2) },
];

export function SeasonLeaders({ rows }: { rows: SeasonLeader[] }) {
  const [key, setKey] = useState("rating");
  const stat = STATS.find((s) => s.key === key)!;
  const ranked = rows
    .map((r) => ({ r, v: stat.get(r) }))
    .filter((x): x is { r: SeasonLeader; v: number } => x.v !== null)
    .sort((a, b) => b.v - a.v);
  const top = ranked.slice(0, 10);
  const median = ranked[Math.floor(ranked.length / 2)]?.v ?? 0;
  const max = top[0]?.v ?? 1;
  const min = median - (max - median) * 0.6;
  const w = (v: number) => `${((v - min) / (max - min)) * 100}%`;

  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {STATS.map((s) => (
          <button
            key={s.key}
            onClick={() => setKey(s.key)}
            className={`border px-2.5 py-1 text-xs transition-colors ${
              s.key === key
                ? "border-accent-dim bg-surface-raised text-ink"
                : "border-hairline text-ink-muted hover:text-ink-secondary"
            }`}
          >
            {s.label}
          </button>
        ))}
      </div>
      <ol className="relative mt-4 space-y-1.5">
        <div
          className="pointer-events-none absolute inset-y-0 border-l border-dashed border-ink-muted/50"
          style={{ left: `calc(11.5rem + (100% - 15.75rem) * ${(median - min) / (max - min)})` }}
          aria-hidden
        />
        {top.map(({ r, v }, i) => (
          <li key={r.handle} className="group flex items-center gap-3 text-sm">
            <span className="w-4 font-mono text-[11px] tabular-nums text-ink-muted">{i + 1}</span>
            <span className="w-36 flex-none truncate">
              <Link href={`/players/${r.slug}`} className="font-medium hover:text-accent">
                {r.handle}
              </Link>
              <span className="block truncate text-[11px] leading-tight text-ink-muted">{r.team}</span>
            </span>
            <span className="relative h-5 flex-1">
              <span
                className="absolute inset-y-0 left-0 bg-accent-dim transition-[width] duration-500 group-hover:bg-accent"
                style={{ width: w(v) }}
              />
            </span>
            <span className="w-14 text-right font-mono tabular-nums">{stat.fmt(v)}</span>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-ink-muted">
        {stat.note && <>{stat.note} </>}Dashed line is the league median ({stat.fmt(median)}). Players
        with 60+ maps.
      </p>
    </div>
  );
}
