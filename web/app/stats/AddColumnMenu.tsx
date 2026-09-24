"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export type MetricOption = {
  key: string;
  label: string;
  category: string; // display name
  gold: boolean;
};

/** Where the menu sits, in viewport pixels. */
type Anchor = { right: number } & ({ top: number } | { bottom: number });

/** Menu height plus a margin, for deciding whether it fits below the button. */
const MENU_HEIGHT = 340;

/**
 * Pin the menu to the viewport under the button, right edges aligned, or above
 * it when there is no room below. Fixed positioning is what lets the menu escape
 * the table's horizontal scroll box, which would otherwise clip it to the rows.
 */
function anchorBelowOrAbove(button: HTMLElement): Anchor {
  const rect = button.getBoundingClientRect();
  const viewportWidth = document.documentElement.clientWidth;
  const right = Math.max(8, viewportWidth - rect.right);
  const below = window.innerHeight - rect.bottom;
  if (below < MENU_HEIGHT && rect.top > below) {
    return { right, bottom: window.innerHeight - rect.top + 4 };
  }
  return { right, top: rect.bottom + 4 };
}

/**
 * The dashed `+` cell at the end of the header row, and the menu behind it.
 *
 * Its position is the explanation: it sits exactly where the next column will
 * appear, which is what makes it the resting-state signpost that this row is
 * editable — the only always-visible piece of column chrome on the page. The
 * menu is a search over the catalog, minus whatever is already a column, so the
 * list is always "what you could add" rather than "what exists".
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
  const inputRef = useRef<HTMLInputElement>(null);

  // Closing always clears the search: the menu is a place you visit to pick one
  // column, so it should open the same way every time.
  function close() {
    setOpen(false);
    setQuery("");
  }

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    function onPointerDown(e: PointerEvent) {
      if (!ref.current?.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        setQuery("");
        buttonRef.current?.focus();
      }
    }
    // The menu is pinned to the viewport, so any scroll outside it (the page,
    // or the table sliding sideways) would leave it floating off its button.
    function onScroll(e: Event) {
      if (menuRef.current?.contains(e.target as Node)) return;
      setOpen(false);
      setQuery("");
    }
    function onResize() {
      setOpen(false);
      setQuery("");
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);

  function openMenu() {
    const button = buttonRef.current;
    if (button) setAnchor(anchorBelowOrAbove(button));
    setOpen(true);
  }

  const available = useMemo(
    () => catalog.filter((m) => !selected.includes(m.key)),
    [catalog, selected],
  );

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (q === "") return available;
    return available.filter(
      (m) =>
        m.label.toLowerCase().includes(q) ||
        m.category.toLowerCase().includes(q),
    );
  }, [available, query]);

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
          className="fixed z-30 w-68 border border-hairline bg-surface shadow-lg"
        >
          <input
            ref={inputRef}
            type="text"
            value={query}
            placeholder={`Search ${available.length} metrics…`}
            aria-label="Search metrics"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && matches[0]) {
                e.preventDefault();
                add(matches[0].key);
              }
            }}
            className="w-full border-b border-hairline bg-background px-2.5 py-2 text-xs text-ink outline-none"
          />
          <div className="max-h-72 overflow-y-auto py-1">
            {matches.length === 0 ? (
              <p className="px-2.5 py-2 text-xs text-ink-muted">
                No metric matches “{query.trim()}”.
              </p>
            ) : (
              matches.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  onClick={() => add(m.key)}
                  className="flex w-full items-baseline justify-between gap-3 px-2.5 py-1.5 text-left text-xs font-normal text-ink-secondary hover:bg-surface-raised hover:text-ink"
                >
                  <span>
                    {m.label}
                    {m.gold && (
                      <span
                        aria-hidden="true"
                        className="ml-1.5 inline-block h-1 w-1 translate-y-[-0.15em] bg-accent align-middle"
                      />
                    )}
                  </span>
                  <span className="shrink-0 font-mono text-[0.6rem] text-ink-muted">
                    {m.category}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
