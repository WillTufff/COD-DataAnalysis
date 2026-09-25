"use client";

import Link from "next/link";
import { useState } from "react";

export type CareerRow = {
  handle: string;
  slug: string;
  total: number;
  nSeasons: number;
  peakYear: number | null;
  parts: Record<string, number>;
};

const PARTS: { key: string; label: string; hint: string }[] = [
  { key: "PEAK", label: "Peak", hint: "Best single season" },
  { key: "PRIME", label: "Prime", hint: "Best three-year stretch" },
  { key: "RESUME", label: "Résumé", hint: "Event placements" },
  { key: "ACCOLADE", label: "Awards", hint: "Rings, MVPs, All-Stars" },
  { key: "LONGEVITY", label: "Longevity", hint: "Seasons at a high level" },
];

export function CareerBoard({ rows }: { rows: CareerRow[] }) {
  const [active, setActive] = useState(0);
  const cur = rows[active];
  const maxTotal = rows[0]?.total ?? 1;
  const partMax = Math.max(150, ...rows.flatMap((r) => Object.values(r.parts)));

  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1fr_340px]">
      <ol className="space-y-1">
        {rows.map((r, i) => (
          <li key={r.handle}>
            <button
              onPointerEnter={() => setActive(i)}
              onFocus={() => setActive(i)}
              onClick={() => setActive(i)}
              className={`group flex w-full items-center gap-3 px-2 py-1.5 text-left text-sm transition-colors ${
                i === active ? "bg-surface-raised" : "hover:bg-surface"
              }`}
            >
              <span className="w-5 font-mono text-[11px] tabular-nums text-ink-muted">{i + 1}</span>
              <span className="w-28 flex-none truncate font-medium">{r.handle}</span>
              <span className="relative h-2 flex-1 bg-surface">
                <span
                  className={`absolute inset-y-0 left-0 ${i === active ? "bg-accent" : "bg-baseline group-hover:bg-accent-dim"}`}
                  style={{ width: `${(r.total / maxTotal) * 100}%` }}
                />
              </span>
              <span className="w-12 text-right font-mono tabular-nums">{r.total.toFixed(1)}</span>
            </button>
          </li>
        ))}
      </ol>
      {cur && (
        <div className="border border-hairline bg-surface p-5">
          <div className="eyebrow text-[10px] text-ink-muted">#{active + 1} all time</div>
          <Link
            href={`/players/${cur.slug}`}
            className="mt-1 block font-display text-4xl font-bold uppercase leading-none hover:text-accent"
          >
            {cur.handle}
          </Link>
          <p className="mt-2 font-mono text-xs text-ink-muted">
            {cur.nSeasons} seasons{cur.peakYear && <> · peak {cur.peakYear}</>}
          </p>
          <div className="mt-5 space-y-3">
            {PARTS.map((p) => {
              const v = cur.parts[p.key];
              return (
                <div key={p.key}>
                  <div className="flex items-baseline justify-between text-xs">
                    <span className="text-ink-secondary">
                      {p.label} <span className="text-ink-muted">· {p.hint}</span>
                    </span>
                    <span className="font-mono tabular-nums">{v == null ? "—" : Math.round(v)}</span>
                  </div>
                  <div className="relative mt-1 h-1.5 bg-background">
                    <div
                      className="absolute inset-y-0 left-0 bg-accent transition-[width] duration-300"
                      style={{ width: `${((v ?? 0) / partMax) * 100}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
          <Link href={`/players/${cur.slug}`} className="mt-5 inline-block text-sm text-accent hover:text-ink">
            Full career →
          </Link>
        </div>
      )}
    </div>
  );
}
