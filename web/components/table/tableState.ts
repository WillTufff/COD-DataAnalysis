"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import {
  DEFAULT_PER,
  type Per,
  pageCount,
  perLimit,
} from "@/lib/paging";

export type SortState = { id: string; dir: "asc" | "desc" } | null;

// How to read and order a sortable column. `dir` is the natural direction the
// column takes on its first click (names read A→Z, metrics best-first).
export type SortSpec<T> = {
  value: (row: T) => number | string | null;
  dir: "asc" | "desc";
};

/**
 * Client-side paging, rows-per-page, and column sort for a table that already
 * holds its whole dataset. Nothing here touches the server: page and sort
 * changes are pure state, and the URL is updated with `history.replaceState`
 * so the view stays shareable without the navigation a `<Link>` would force.
 */
export function useTableState<T>({
  rows,
  defaultPer = DEFAULT_PER,
  initialPer = defaultPer,
  initialPage = 1,
  initialSort = null,
  defaultSort = null,
  sortSpecs,
  syncUrl = true,
}: {
  rows: T[];
  defaultPer?: Per;
  initialPer?: Per;
  initialPage?: number;
  initialSort?: SortState;
  defaultSort?: SortState;
  sortSpecs?: Record<string, SortSpec<T>>;
  syncUrl?: boolean;
}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [per, setPerState] = useState<Per>(initialPer);
  const [page, setPageState] = useState<number>(initialPage);
  const [sort, setSortState] = useState<SortState>(initialSort);

  const writeUrl = useCallback(
    (patch: Record<string, string | null>) => {
      if (!syncUrl || typeof window === "undefined") return;
      const params = new URLSearchParams(searchParams.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null) params.delete(k);
        else params.set(k, v);
      }
      params.sort();
      const qs = params.toString();
      window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname);
    },
    [pathname, searchParams, syncUrl],
  );

  const setPer = useCallback(
    (p: Per) => {
      setPerState(p);
      setPageState(1);
      writeUrl({ per: p === defaultPer ? null : String(p), page: null });
    },
    [defaultPer, writeUrl],
  );

  const setPage = useCallback(
    (p: number) => {
      setPageState(p);
      writeUrl({ page: p <= 1 ? null : String(p) });
    },
    [writeUrl],
  );

  const toggleSort = useCallback(
    (id: string) => {
      const spec = sortSpecs?.[id];
      if (!spec) return;
      const next: SortState =
        sort && sort.id === id
          ? { id, dir: sort.dir === "asc" ? "desc" : "asc" }
          : { id, dir: spec.dir };
      setSortState(next);
      setPageState(1);
      const isDefault =
        defaultSort != null &&
        next.id === defaultSort.id &&
        next.dir === defaultSort.dir;
      writeUrl({
        sort: isDefault ? null : next.id,
        dir: isDefault ? null : next.dir,
        page: null,
      });
    },
    [sort, sortSpecs, defaultSort, writeUrl],
  );

  const sorted = useMemo(() => {
    if (!sort || !sortSpecs?.[sort.id]) return rows;
    const read = sortSpecs[sort.id].value;
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = read(a);
      const bv = read(b);
      // Missing values sink to the bottom in either direction — an unrated
      // player is never "the best" or "the worst", just absent.
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      if (typeof av === "string" || typeof bv === "string") {
        return factor * String(av).localeCompare(String(bv));
      }
      return factor * (Number(av) - Number(bv));
    });
  }, [rows, sort, sortSpecs]);

  const total = sorted.length;
  const limit = perLimit(per, total);
  const pages = pageCount(total, limit);
  const clampedPage = Math.min(page, pages);
  const offset = per === "all" ? 0 : (clampedPage - 1) * limit;
  const visible = per === "all" ? sorted : sorted.slice(offset, offset + limit);

  return {
    per,
    setPer,
    page: clampedPage,
    setPage,
    sort,
    toggleSort,
    visible,
    offset,
    total,
    pageCount: pages,
  };
}
