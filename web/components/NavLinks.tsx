"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export function NavLinks({ items }: { items: { href: string; label: string }[] }) {
  const pathname = usePathname();
  return (
    <nav className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
      {items.map((n) => {
        const active =
          n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
        return (
          <Link
            key={n.href}
            href={n.href}
            aria-current={active ? "page" : undefined}
            className={
              active
                ? "text-ink underline decoration-accent decoration-2 underline-offset-8"
                : "text-ink-secondary transition-colors hover:text-ink"
            }
          >
            {n.label}
          </Link>
        );
      })}
    </nav>
  );
}
