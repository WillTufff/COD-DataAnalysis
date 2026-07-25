"use client";

import { useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

export type PickerMetric = {
  key: string;
  label: string;
  category: string;
  gold: boolean;
};

/**
 * The fix for the 100-item dropdown: a searchable combobox that builds the
 * report's columns as an ordered list of chips. Type to filter the catalog,
 * Enter or click to add; chips reorder (order = column order) and remove.
 *
 * The column set is server-queried, so every edit navigates — it rewrites the
 * ordered `metrics` CSV on the URL, resets the page, and drops `preset` (once
 * the columns are hand-edited the view is no longer "a preset"). Sort is left
 * on the URL for the server to validate: if the sort column was removed, the
 * page falls back to a default rather than the picker having to guess.
 */
export function ColumnPicker({
  catalog,
  categoryLabels,
  selected,
}: {
  catalog: PickerMetric[];
  categoryLabels: Record<string, string>;
  selected: string[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const byKey = useMemo(
    () => new Map(catalog.map((m) => [m.key, m])),
    [catalog],
  );

  function navigate(nextMetrics: string[]) {
    const params = new URLSearchParams(searchParams.toString());
    if (nextMetrics.length > 0) params.set("metrics", nextMetrics.join(","));
    else params.delete("metrics");
    params.delete("page"); // a new column set starts at page 1
    params.delete("preset"); // hand-edited columns are no longer a preset
    params.sort();
    const qs = params.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname);
  }

  function add(key: string) {
    if (selected.includes(key)) return;
    navigate([...selected, key]);
    setQuery("");
  }
  function remove(key: string) {
    navigate(selected.filter((k) => k !== key));
  }
  function move(key: string, dir: -1 | 1) {
    const i = selected.indexOf(key);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= selected.length) return;
    const next = [...selected];
    [next[i], next[j]] = [next[j], next[i]];
    navigate(next);
  }

  // Catalog minus what's already picked, filtered by the query, grouped by
  // category so the list reads the same way the old dropdown did.
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pool = catalog.filter(
      (m) =>
        !selected.includes(m.key) &&
        (q === "" || m.label.toLowerCase().includes(q)),
    );
    const groups = new Map<string, PickerMetric[]>();
    for (const m of pool) {
      const label = categoryLabels[m.category] ?? m.category;
      const list = groups.get(label) ?? [];
      list.push(m);
      groups.set(label, list);
    }
    return [...groups.entries()];
  }, [catalog, selected, query, categoryLabels]);

  const firstMatch = matches[0]?.[1][0];

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-xs text-ink-muted">Columns</span>
        <span className="font-mono text-xs text-ink-muted">
          {selected.length} selected
        </span>
      </div>

      {/* Selected columns as ordered, reorderable chips. */}
      {selected.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {selected.map((key, i) => {
            const m = byKey.get(key);
            return (
              <li
                key={key}
                className="inline-flex items-center gap-1 border border-hairline bg-surface-raised py-0.5 pl-2 pr-1 text-xs"
              >
                <span className="font-medium">
                  {m?.label ?? key}
                  {m?.gold ? " ★" : ""}
                </span>
                <span className="ml-0.5 inline-flex">
                  <button
                    type="button"
                    aria-label={`Move ${m?.label ?? key} left`}
                    disabled={i === 0}
                    onClick={() => move(key, -1)}
                    className="px-0.5 text-ink-muted enabled:hover:text-ink disabled:opacity-30"
                  >
                    ←
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${m?.label ?? key} right`}
                    disabled={i === selected.length - 1}
                    onClick={() => move(key, 1)}
                    className="px-0.5 text-ink-muted enabled:hover:text-ink disabled:opacity-30"
                  >
                    →
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove ${m?.label ?? key}`}
                    onClick={() => remove(key)}
                    className="px-1 text-ink-muted hover:text-accent"
                  >
                    ✕
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {/* Searchable combobox. */}
      <div className="relative max-w-md">
        <input
          type="text"
          value={query}
          placeholder="Add a metric column…"
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => {
            if (blurTimer.current) clearTimeout(blurTimer.current);
            setOpen(true);
          }}
          onBlur={() => {
            // Delay so a click on an option lands before the list closes.
            blurTimer.current = setTimeout(() => setOpen(false), 120);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && firstMatch) {
              e.preventDefault();
              add(firstMatch.key);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          className="w-full border border-hairline bg-surface px-2 py-1.5 text-sm"
        />
        {open && matches.length > 0 && (
          <div className="absolute z-10 mt-1 max-h-72 w-full overflow-y-auto border border-hairline bg-surface shadow-lg">
            {matches.map(([label, list]) => (
              <div key={label}>
                <div className="sticky top-0 bg-surface-raised px-2 py-1 text-[10px] uppercase tracking-wide text-ink-muted">
                  {label}
                </div>
                {list.map((m) => (
                  <button
                    key={m.key}
                    type="button"
                    // onMouseDown fires before the input's blur, so the add
                    // runs before the list would close.
                    onMouseDown={(e) => {
                      e.preventDefault();
                      add(m.key);
                    }}
                    className="block w-full px-3 py-1.5 text-left text-sm hover:bg-surface-raised"
                  >
                    {m.label}
                    {m.gold ? " ★" : ""}
                  </button>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
