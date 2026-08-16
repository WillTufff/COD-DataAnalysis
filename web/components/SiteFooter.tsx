"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// Hidden on /methodology: that page is a fixed docs shell with no page-level
// scroll, and its own "attribution" section already carries this content.
export function SiteFooter() {
  const pathname = usePathname();
  if (pathname?.startsWith("/methodology")) return null;

  return (
    <footer className="mt-16 border-t border-hairline print:hidden">
      <div className="mx-auto max-w-6xl space-y-1 px-6 py-6 text-xs text-ink-muted">
        <p>
          Box scores, placements and awards, 2013&ndash;2016:{" "}
          <a
            className="underline hover:text-ink-secondary"
            href="https://cod-esports.fandom.com"
          >
            Call of Duty Esports Wiki
          </a>
          , CC-BY-SA 3.0. Derived data shared under the same licence.
        </p>
        <p>
          Box scores and kill feeds, 2017&ndash;2019: Call of Duty World League
          archive data ©{" "}
          <a
            className="underline hover:text-ink-secondary"
            href="https://github.com/Activision/cwl-data"
          >
            Activision Publishing (cwl-data)
          </a>
          , BSD-3-Clause.
        </p>
        <p>
          Box scores 2020&ndash;2026: data via{" "}
          <a className="underline hover:text-ink-secondary" href="https://citoapi.com">
            Cito
          </a>
          , which carries Breaking Point match data, used with attribution.
          Published here only as derived analysis, never as a copy of the
          underlying box scores.
        </p>
        <p>
          Tournaments, placements, prize money, rosters, transfers and player
          bios:{" "}
          <a
            className="underline hover:text-ink-secondary"
            href="https://liquipedia.net/callofduty"
          >
            Liquipedia
          </a>
          , CC-BY-SA 3.0, retrieved through their API.
        </p>
        <p>
          All models are educational analysis of historical play; the{" "}
          <Link className="underline hover:text-ink-secondary" href="/methodology">
            methodology
          </Link>{" "}
          page has specs, backtests, and coverage.
        </p>
      </div>
    </footer>
  );
}
