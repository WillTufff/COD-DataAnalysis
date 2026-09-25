"use client";

import { useCallback, useRef, useState } from "react";
import type { HeaderApi } from "@/components/table/DataTable";
import type { ReportColumn } from "@/lib/analytics";
import { type Anchor, anchorBelowOrAbove, useDismiss } from "./popover";

const ROW =
  "flex w-full items-center justify-between gap-3 px-2.5 py-1.5 text-left text-xs text-ink-secondary hover:bg-surface-raised hover:text-ink disabled:pointer-events-none disabled:opacity-40";

/**
 * The ▾ on a metric header and the menu behind it: the column's sample floor
 * and direction, sort either way, move one place, and remove. Every column
 * edit here is also reachable by keyboard, which dragging the header is not.
 */
export function ColumnMenu({
  col,
  index,
  count,
  api,
  onMove,
  onRemove,
}: {
  col: ReportColumn;
  index: number;
  count: number;
  api: HeaderApi;
  onMove: (to: number) => void;
  onRemove: () => void;
}) {
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const open = anchor !== null;
  const close = useCallback(() => setAnchor(null), []);
  useDismiss({
    open,
    close,
    container: ref,
    button: buttonRef,
    fixedMenu: menuRef,
  });

  const best = col.higherIsBetter ? "desc" : "asc";
  const worst = col.higherIsBetter ? "asc" : "desc";
  const sorted = api.sort?.id === col.key ? api.sort.dir : null;

  function act(fn: () => void) {
    close();
    fn();
  }

  return (
    <span ref={ref} data-col-menu className="print:hidden">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${col.label} column options`}
        title="Column options"
        onClick={() =>
          open ? close() : buttonRef.current && setAnchor(anchorBelowOrAbove(buttonRef.current))
        }
        className={`ml-0.5 px-0.5 text-[0.6rem] leading-none transition-colors motion-reduce:transition-none ${
          open ? "text-accent" : "text-baseline hover:text-accent group-hover/th:text-ink-muted"
        }`}
      >
        ▾
      </button>
      {open && (
        <div
          ref={menuRef}
          role="menu"
          style={anchor}
          className="fixed z-30 w-56 border border-hairline bg-surface py-1 text-left font-sans normal-case tracking-normal shadow-lg"
        >
          <p className="px-2.5 pb-1.5 pt-1 text-[0.66rem] leading-snug text-ink-muted">
            <span className="block text-xs text-ink">{col.label}</span>
            {col.higherIsBetter ? "Higher is better" : "Lower is better"} · min{" "}
            {col.minDenom} {col.denomKind}
          </p>
          <div className="my-1 border-t border-hairline" />
          <button
            type="button"
            role="menuitem"
            className={ROW}
            onClick={() => act(() => api.setSort({ id: col.key, dir: best }))}
          >
            Sort best first
            {sorted === best && <span className="text-accent">✓</span>}
          </button>
          <button
            type="button"
            role="menuitem"
            className={ROW}
            onClick={() => act(() => api.setSort({ id: col.key, dir: worst }))}
          >
            Sort worst first
            {sorted === worst && <span className="text-accent">✓</span>}
          </button>
          <div className="my-1 border-t border-hairline" />
          <button
            type="button"
            role="menuitem"
            className={ROW}
            disabled={index === 0}
            onClick={() => act(() => onMove(index - 1))}
          >
            Move left
          </button>
          <button
            type="button"
            role="menuitem"
            className={ROW}
            disabled={index === count - 1}
            onClick={() => act(() => onMove(index + 1))}
          >
            Move right
          </button>
          <div className="my-1 border-t border-hairline" />
          <button
            type="button"
            role="menuitem"
            className={ROW}
            onClick={() => act(onRemove)}
          >
            Remove column
          </button>
        </div>
      )}
    </span>
  );
}
