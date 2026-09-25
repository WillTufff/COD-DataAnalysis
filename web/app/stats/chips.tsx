"use client";

import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { useDismiss } from "./popover";

/** A row in a chip menu: check mark gutter + label, hover fill. */
export const MENU_ROW =
  "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-ink-secondary hover:bg-surface-raised hover:text-ink";

export function Check({ on }: { on: boolean }) {
  return (
    <span aria-hidden="true" className={on ? "text-accent" : "text-transparent"}>
      ✓
    </span>
  );
}

/**
 * A filter chip: `Label: value`, opening its menu underneath, with its own ✕
 * when the filter can be removed. Open state is an accent border.
 */
export function Chip({
  label,
  value,
  open,
  setOpen,
  onClear,
  children,
}: {
  label: string;
  value: string;
  open: boolean;
  setOpen: (open: boolean) => void;
  onClear?: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [shift, setShift] = useState(0);
  const close = useCallback(() => setOpen(false), [setOpen]);

  // A menu opened from a chip near the right edge slides left until it fits,
  // so it never widens the page on a narrow screen.
  useLayoutEffect(() => {
    if (!open || !menuRef.current) {
      setShift(0);
      return;
    }
    const rect = menuRef.current.getBoundingClientRect();
    const right = rect.right + window.scrollX + shift;
    const overflow = right - (document.documentElement.clientWidth - 8);
    setShift(overflow > 0 ? overflow : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  useDismiss({ open, close, container: ref, button: buttonRef });

  return (
    <div
      ref={ref}
      className={`relative inline-flex items-stretch border text-xs transition-colors motion-reduce:transition-none ${
        open ? "border-accent bg-surface-raised" : "border-hairline bg-surface hover:border-accent-dim"
      }`}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="px-2 py-1 text-left"
      >
        <span className="text-ink-muted">{label}:</span>{" "}
        <span className="font-medium text-ink">{value}</span>
      </button>
      {onClear && (
        <button
          type="button"
          aria-label={`Remove the ${label.toLowerCase()} filter`}
          title={`Remove the ${label.toLowerCase()} filter`}
          onClick={onClear}
          className="border-l border-hairline px-1.5 text-ink-muted hover:text-accent"
        >
          ✕
        </button>
      )}
      {open && (
        <div
          ref={menuRef}
          style={shift > 0 ? { left: -shift } : undefined}
          className="absolute left-0 top-full z-20 mt-1 max-w-[calc(100vw-1rem)] border border-hairline bg-surface shadow-lg"
        >
          {children}
        </div>
      )}
    </div>
  );
}

