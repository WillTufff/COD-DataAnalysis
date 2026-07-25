"use client";

import { PER_OPTIONS, type Per, perLabel } from "@/lib/paging";

/**
 * Rows-per-page and page navigation, rendered above its table. Every control
 * is a button that mutates client state — no navigation, no full-page reload
 * when moving between pages. Visually it carries the same monospace treatment
 * the rest of the tables use.
 */
export function TableControls({
  per,
  setPer,
  page,
  setPage,
  pageCount,
  total,
  offset,
  visibleCount,
  unit = "rows",
}: {
  per: Per;
  setPer: (p: Per) => void;
  page: number;
  setPage: (p: number) => void;
  pageCount: number;
  total: number;
  offset: number;
  visibleCount: number;
  unit?: string;
}) {
  const first = total === 0 ? 0 : offset + 1;
  const last = offset + visibleCount;

  return (
    <nav
      aria-label="Table paging"
      className="mb-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-hairline pb-3 font-mono text-xs text-ink-muted print:hidden"
    >
      <div className="flex items-center gap-2">
        <span>Show</span>
        {PER_OPTIONS.map((n) => (
          <button
            key={String(n)}
            type="button"
            onClick={() => setPer(n)}
            aria-current={n === per ? "true" : undefined}
            className={
              n === per
                ? "text-ink underline decoration-accent decoration-2 underline-offset-4"
                : "hover:text-ink"
            }
          >
            {perLabel(n)}
          </button>
        ))}
        <span>
          {unit} · {first}–{last} of {total.toLocaleString()}
        </span>
      </div>

      {pageCount > 1 && (
        <div className="flex items-center gap-3">
          {page > 1 ? (
            <button
              type="button"
              onClick={() => setPage(page - 1)}
              className="hover:text-ink"
            >
              ← Prev
            </button>
          ) : (
            <span aria-hidden="true" className="text-hairline">
              ← Prev
            </span>
          )}
          <span className="tabular-nums">
            Page {page} of {pageCount}
          </span>
          {page < pageCount ? (
            <button
              type="button"
              onClick={() => setPage(page + 1)}
              className="hover:text-ink"
            >
              Next →
            </button>
          ) : (
            <span aria-hidden="true" className="text-hairline">
              Next →
            </span>
          )}
        </div>
      )}
    </nav>
  );
}
