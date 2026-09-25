"use client";

import { type RefObject, useEffect } from "react";

/** Where a viewport-fixed menu sits, in viewport pixels. */
export type Anchor = { right: number } & ({ top: number } | { bottom: number });

/** Menu height plus a margin, for deciding whether it fits below the button. */
const MENU_HEIGHT = 340;

/**
 * Pin a menu to the viewport under a button, right edges aligned, or above it
 * when there is no room below. Fixed positioning lets the menu escape the
 * table's horizontal scroll box, which would otherwise clip it to the rows.
 */
export function anchorBelowOrAbove(button: HTMLElement): Anchor {
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
 * Close an open menu on a pointer down outside `container`, or on Escape (which
 * hands focus back to `button`). A viewport-fixed menu also closes on any
 * scroll outside `menu` and on resize, since either would leave it floating
 * away from its button.
 */
export function useDismiss({
  open,
  close,
  container,
  button,
  fixedMenu,
}: {
  open: boolean;
  close: () => void;
  container: RefObject<HTMLElement | null>;
  button?: RefObject<HTMLElement | null>;
  fixedMenu?: RefObject<HTMLElement | null>;
}) {
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (!container.current?.contains(e.target as Node)) close();
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        close();
        button?.current?.focus();
      }
    }
    function onScroll(e: Event) {
      if (fixedMenu?.current?.contains(e.target as Node)) return;
      close();
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    if (fixedMenu) {
      document.addEventListener("scroll", onScroll, true);
      window.addEventListener("resize", close);
    }
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
      if (fixedMenu) {
        document.removeEventListener("scroll", onScroll, true);
        window.removeEventListener("resize", close);
      }
    };
  }, [open, close, container, button, fixedMenu]);
}
