import type { Metadata } from "next";
import { Barlow_Condensed, Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const display = Barlow_Condensed({
  variable: "--font-display",
  weight: ["500", "600", "700"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "cdlhub — competitive Call of Duty analytics",
    template: "%s · cdlhub",
  },
  description:
    "Era-adjusted stats, team strength ratings, and evidence-linked analysis for competitive Call of Duty (CWL 2017–2019 archive).",
};

const nav = [
  { href: "/", label: "Overview" },
  { href: "/players", label: "Players" },
  { href: "/ratings", label: "Ratings" },
  { href: "/findings", label: "Findings" },
  { href: "/methodology", label: "Methodology" },
];

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} ${display.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <header className="border-b border-hairline">
          <div className="mx-auto flex max-w-5xl items-baseline gap-8 px-6 py-4">
            <Link
              href="/"
              className="font-display text-2xl font-bold uppercase tracking-tight"
            >
              cdl<span className="text-accent">hub</span>
            </Link>
            <nav className="flex gap-5 text-sm">
              {nav.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  className="text-ink-secondary transition-colors hover:text-ink"
                >
                  {n.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <div className="flex-1">{children}</div>
        <footer className="mt-16 border-t border-hairline">
          <div className="mx-auto max-w-5xl space-y-1 px-6 py-6 text-xs text-ink-muted">
            <p>
              Box scores: Call of Duty World League archive data ©{" "}
              <a
                className="underline hover:text-ink-secondary"
                href="https://github.com/Activision/cwl-data"
              >
                Activision Publishing (cwl-data)
              </a>
              , BSD-3-Clause. Event and roster context from{" "}
              <a
                className="underline hover:text-ink-secondary"
                href="https://liquipedia.net/callofduty"
              >
                Liquipedia
              </a>{" "}
              (CC-BY-SA 3.0).
            </p>
            <p>
              All models are educational analysis of historical play — see{" "}
              <Link className="underline hover:text-ink-secondary" href="/methodology">
                methodology
              </Link>{" "}
              for specs, backtests, and coverage.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
