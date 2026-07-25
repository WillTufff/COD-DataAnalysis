"use client";

import { useSearchParams } from "next/navigation";

/**
 * Hidden inputs that echo the live values of the named query params into a GET
 * form. The tables write `per` and sort onto the URL client-side; without this
 * a filter submit — which rebuilds the query from the form's fields alone —
 * would silently drop them. Reading from `useSearchParams` keeps the inputs in
 * step with whatever the table last wrote.
 */
export function CarryParams({ names }: { names: string[] }) {
  const sp = useSearchParams();
  return (
    <>
      {names.map((n) => {
        const v = sp.get(n);
        return v ? <input key={n} type="hidden" name={n} value={v} /> : null;
      })}
    </>
  );
}
