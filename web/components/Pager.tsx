import Link from "next/link";
import {
  PER_OPTIONS,
  type Paging,
  type SearchParams,
  buildQuery,
  pageCount,
} from "@/lib/paging";

/**
 * Rows-per-page and page navigation for the server-rendered tables.
 *
 * Every control is a link rather than a form control: the pager renders below
 * a table, outside whatever filter form the page carries, and a second form
 * cannot nest inside the first. Links keep the whole thing working without
 * client JS and keep each view addressable.
 */
export function Pager({
  basePath,
  searchParams,
  paging,
  total,
  unit = "rows",
}: {
  basePath: string;
  searchParams: SearchParams;
  paging: Paging;
  total: number;
  unit?: string;
}) {
  const pages = pageCount(total, paging.per);
  const first = total === 0 ? 0 : paging.offset + 1;
  const last = Math.min(paging.offset + paging.per, total);

  // Page 1 and the default row count are the implied state, so they come off
  // the URL rather than being written into it.
  const href = (patch: Record<string, string | number | null>) =>
    `${basePath}${buildQuery(searchParams, patch)}`;
  const pageHref = (p: number) => href({ page: p === 1 ? null : p });

  return (
    <nav
      aria-label="Table paging"
      className="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t border-hairline pt-3 font-mono text-xs text-ink-muted"
    >
      <div className="flex items-center gap-2">
        <span>Show</span>
        {PER_OPTIONS.map((n) => (
          <Link
            key={n}
            href={href({ per: n, page: null })}
            aria-current={n === paging.per ? "true" : undefined}
            className={
              n === paging.per
                ? "text-ink underline decoration-accent decoration-2 underline-offset-4"
                : "hover:text-ink"
            }
          >
            {n}
          </Link>
        ))}
        <span>
          {unit} · {first}–{last} of {total.toLocaleString()}
        </span>
      </div>

      {pages > 1 && (
        <div className="flex items-center gap-3">
          {paging.page > 1 ? (
            <Link href={pageHref(paging.page - 1)} className="hover:text-ink">
              ← Prev
            </Link>
          ) : (
            <span aria-hidden="true" className="text-hairline">
              ← Prev
            </span>
          )}
          <span className="tabular-nums">
            Page {paging.page} of {pages}
          </span>
          {paging.page < pages ? (
            <Link href={pageHref(paging.page + 1)} className="hover:text-ink">
              Next →
            </Link>
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
