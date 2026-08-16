"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { TOC } from "./toc";

const MASTHEAD_H = 56; // px, matches h-14 below

// Docs shell: masthead pinned under the site header, tier nav and content
// pane each own their scroll. The site header's height is measured (not
// hardcoded) so this keeps working if that header's size ever changes.
export function MethodologyToc({ children }: { children: React.ReactNode }) {
  const [query, setQuery] = useState("");
  const [headerH, setHeaderH] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const pathname = usePathname();
  const activeId = pathname?.split("/").filter(Boolean).at(-1) ?? null;

  useLayoutEffect(() => {
    const header = document.querySelector("body > header");
    if (!header) return;
    const measure = () => setHeaderH(header.getBoundingClientRect().height);
    const raf = requestAnimationFrame(measure);
    const observer = new ResizeObserver(measure);
    observer.observe(header);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;
      e.preventDefault();
      inputRef.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return TOC;
    return TOC.map((t) => ({
      ...t,
      sections: t.sections.filter((s) => s.title.toLowerCase().includes(q)),
    })).filter((t) => t.sections.length > 0);
  }, [query]);

  const currentTierLabel = TOC.find((t) => t.sections.some((s) => s.id === activeId))?.label;

  return (
    <div>
      <div
        className="sticky z-10 flex h-14 items-center justify-between gap-6 border-b border-hairline bg-background"
        style={{ top: headerH }}
      >
        <div className="font-mono text-xs tracking-wide text-ink-muted">
          cdlhub / methodology
          {currentTierLabel && (
            <>
              <span className="text-accent"> · </span>
              {currentTierLabel}
            </>
          )}
        </div>
        <div className="flex items-center gap-2 text-ink-muted">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.3-4.3" />
          </svg>
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search methodology"
            className="w-56 border-0 border-b border-hairline bg-transparent px-0.5 py-1 text-sm text-ink outline-none placeholder:text-ink-muted focus:border-accent"
          />
          {query === "" && <span className="font-mono text-[0.65rem] text-ink-muted">/</span>}
        </div>
      </div>

      <div
        className="grid grid-cols-[252px_minmax(0,1fr)] items-start gap-0"
        style={{ height: `calc(100vh - ${headerH}px - ${MASTHEAD_H}px)` }}
      >
        <nav className="h-full overflow-y-auto border-r border-hairline py-5 pr-5">
          {filtered.map((t) => (
            <div key={t.label}>
              <div className="flex items-baseline gap-2 pt-4 pb-1.5 font-mono text-[0.68rem] tracking-wide text-ink-muted first:pt-0">
                <span className="text-ink-secondary">{t.tier}</span>
                <span>{t.label}</span>
                <span className="ml-1 flex-1 border-t border-hairline" />
              </div>
              <ul className="list-none">
                {t.sections.map((s) => (
                  <li key={s.id}>
                    <Link
                      href={`/methodology/${s.id}`}
                      className={`flex gap-2 py-1 text-[0.85rem] no-underline ${
                        activeId === s.id ? "text-ink" : "text-ink-muted hover:text-ink-secondary"
                      }`}
                    >
                      <span className={`w-2.5 flex-none ${activeId === s.id ? "text-accent" : ""}`}>
                        {activeId === s.id ? "›" : ""}
                      </span>
                      {s.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <main className="h-full max-w-5xl overflow-y-auto py-5 pl-10">{children}</main>
      </div>
    </div>
  );
}
