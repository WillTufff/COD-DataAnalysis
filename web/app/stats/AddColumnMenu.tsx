"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { type Anchor, anchorBelowOrAbove, useDismiss } from "./popover";

export type MetricOption = {
  key: string;
  label: string;
  category: string; // display name
  gold: boolean;
  /** Whether the metric has rows for the seasons and mode in view. */
  inView: boolean;
  /** The seasons the metric is published for, e.g. "2017–2019". */
  span: string;
};

function OptionRow({
  m,
  onAdd,
}: {
  m: MetricOption;
  onAdd: (key: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onAdd(m.key)}
      className={`flex w-full items-baseline justify-between gap-3 px-2.5 py-1.5 text-left text-xs font-normal hover:bg-surface-raised hover:text-ink ${
        m.inView ? "text-ink-secondary" : "text-ink-muted"
      }`}
    >
      <span>
        {m.label}
        {m.gold && (
          <span
            aria-label="headline metric"
            className="ml-1.5 inline-block h-1 w-1 translate-y-[-0.15em] bg-accent align-middle"
          />
        )}
      </span>
      <span className="shrink-0 font-mono text-[0.6rem] text-ink-muted">
        {m.inView ? m.category : m.span}
      </span>
    </button>
  );
}

/**
 * The dashed `+` cell at the end of the header row, and the menu behind it.
 *
 * It sits where the next column will appear. The menu is a search over the
 * catalog minus the current columns, split in two: metrics with rows for the
 * seasons and mode in view, then the rest, greyed and labelled with the seasons
 * they cover, since adding one of those gives a column of dashes.
 */
export function AddColumnMenu({
  catalog,
  selected,
  onAdd,
}: {
  catalog: MetricOption[];
  selected: string[];
  onAdd: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Closing always clears the search: the menu is a place you visit to pick one
  // column, so it should open the same way every time.
  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);
  useDismiss({
    open,
    close,
    container: ref,
    button: buttonRef,
    fixedMenu: menuRef,
  });

  function openMenu() {
    const button = buttonRef.current;
    if (button) setAnchor(anchorBelowOrAbove(button));
    setOpen(true);
  }

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    return catalog.filter(
      (m) =>
        !selected.includes(m.key) &&
        (q === "" ||
          m.label.toLowerCase().includes(q) ||
          m.category.toLowerCase().includes(q)),
    );
  }, [catalog, selected, query]);
  const inView = matches.filter((m) => m.inView);
  const elsewhere = matches.filter((m) => !m.inView);
  const first = inView[0] ?? elsewhere[0];

  function add(key: string) {
    close();
    onAdd(key);
  }

  return (
    <div ref={ref} className="relative inline-block text-left">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Add a metric column"
        title="Add a metric column"
        onClick={() => (open ? close() : openMenu())}
        className={`h-6 w-6 border border-dashed text-sm leading-none transition-colors motion-reduce:transition-none ${
          open
            ? "border-accent text-accent"
            : "border-hairline text-ink-muted hover:border-accent-dim hover:text-accent"
        }`}
      >
        +
      </button>
      {open && anchor && (
        <div
          ref={menuRef}
          style={anchor}
          className="fixed z-30 flex w-72 max-w-[calc(100vw-1rem)] flex-col border border-hairline bg-surface shadow-lg"
        >
          <input
            autoFocus
            type="text"
            value={query}
            placeholder={`Search ${catalog.length - selected.length} metrics…`}
            aria-label="Search metrics"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && first) {
                e.preventDefault();
                add(first.key);
              }
            }}
            className="w-full border-b border-hairline bg-background px-2.5 py-2 text-xs text-ink outline-none"
          />
          <div className="max-h-72 min-h-0 flex-1 overflow-y-auto py-1">
            {matches.length === 0 && (
              <p className="px-2.5 py-2 text-xs text-ink-muted">
                No metric matches “{query.trim()}”.
              </p>
            )}
            {inView.map((m) => (
              <OptionRow key={m.key} m={m} onAdd={add} />
            ))}
            {elsewhere.length > 0 && (
              <>
                <div className="mt-1 border-t border-hairline px-2.5 pb-1 pt-2 font-display text-[0.6rem] font-semibold uppercase tracking-[0.14em] text-ink-muted">
                  No rows in this view
                </div>
                {elsewhere.map((m) => (
                  <OptionRow key={m.key} m={m} onAdd={add} />
                ))}
              </>
            )}
          </div>
          <p className="border-t border-hairline px-2.5 py-1.5 text-[0.6rem] text-ink-muted">
            <span
              aria-hidden="true"
              className="mr-1.5 inline-block h-1 w-1 translate-y-[-0.15em] bg-accent align-middle"
            />
            headline metric
          </p>
        </div>
      )}
    </div>
  );
}
