"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { SearchEntry } from "./queries";

export function Search({ index }: { index: SearchEntry[] }) {
  const [q, setQ] = useState("");
  const hits = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return [];
    return index
      .filter((e) => e.label.toLowerCase().includes(s))
      .sort((a, b) => Number(!a.label.toLowerCase().startsWith(s)) - Number(!b.label.toLowerCase().startsWith(s)) || a.label.length - b.label.length)
      .slice(0, 8);
  }, [q, index]);

  return (
    <div className="relative max-w-md">
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search a player or team"
        className="w-full border border-baseline bg-surface px-4 py-3 text-base placeholder:text-ink-muted focus:border-accent focus:outline-none"
      />
      {hits.length > 0 && (
        <ul className="absolute z-20 mt-1 w-full border border-baseline bg-surface-raised shadow-xl">
          {hits.map((h) => (
            <li key={h.href}>
              <Link href={h.href} className="flex items-baseline justify-between px-4 py-2 text-sm hover:bg-surface">
                <span>{h.label}</span>
                <span className="font-mono text-[10px] text-ink-muted">
                  {h.kind} · {h.sub}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
