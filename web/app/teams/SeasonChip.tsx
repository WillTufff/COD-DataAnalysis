"use client";

import { useState, useTransition } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Check, Chip, MENU_ROW } from "../stats/chips";
import type { SeasonEra } from "@/lib/eras";

/**
 * The season the standings cover. No `season` param means the current season;
 * `season=all` is the whole archive.
 */
export function SeasonChip({
  seasons,
  current,
  picked,
}: {
  seasons: SeasonEra[];
  current: number;
  picked: number | null;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();

  const go = (year: number | null) => {
    setOpen(false);
    const qs = year === current ? "" : `?season=${year ?? "all"}`;
    startTransition(() => router.push(`${pathname}${qs}`, { scroll: false }));
  };
  const season = seasons.find((s) => s.year === picked);
  const value = season ? `${season.year} ${season.title}` : "All time";

  return (
    <div className={pending ? "opacity-60" : undefined}>
      <Chip
        label="Season"
        value={value}
        open={open}
        setOpen={setOpen}
        onClear={picked !== null ? () => go(null) : undefined}
      >
        <div className="max-h-80 w-56 max-w-full overflow-y-auto py-1">
          <button type="button" onClick={() => go(null)} className={MENU_ROW}>
            <Check on={picked === null} />
            All time
          </button>
          <div className="my-1 border-t border-hairline" />
          {[...seasons].reverse().map((s) => (
            <button
              key={s.year}
              type="button"
              onClick={() => go(s.year)}
              className={MENU_ROW}
            >
              <Check on={s.year === picked} />
              <span className="font-mono tabular-nums">{s.year}</span>
              <span className={s.year === picked ? "text-ink" : ""}>{s.title}</span>
              <span className="ml-auto text-xs text-ink-muted">{s.league}</span>
            </button>
          ))}
        </div>
      </Chip>
    </div>
  );
}
