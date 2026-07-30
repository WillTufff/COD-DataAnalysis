"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

/**
 * The report builder's one way to change state: rewrite the URL. Every control
 * on the page — the cohort tokens, the column header chrome, the small-samples
 * toggle — patches the query string and navigates, so the URL stays the single
 * source of truth and any view is shareable as-is.
 *
 * A `null` value drops its key; an empty string keeps the key with an empty
 * value, which is how "all modes combined" is stated explicitly rather than
 * inferred. Every patch resets paging, since the row set has changed underneath
 * it. Keys are sorted so the same view always produces the same link.
 */
export function useReportUrl(): (patch: Record<string, string | null>) => void {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  return useCallback(
    (patch: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value === null) params.delete(key);
        else params.set(key, value);
      }
      params.delete("page");
      params.sort();
      const qs = params.toString();
      // No scroll reset: these edits happen at the table, and yanking the
      // reader back to the top after every column tweak is disorienting.
      router.push(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );
}
