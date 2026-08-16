import type { Metadata } from "next";
import { Barlow, Barlow_Condensed, IBM_Plex_Mono } from "next/font/google";
import Link from "next/link";
import { NavLinks } from "@/components/NavLinks";
import { SiteFooter } from "@/components/SiteFooter";
import "./globals.css";

const body = Barlow({
  variable: "--font-body",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

const mono = IBM_Plex_Mono({
  variable: "--font-mono-data",
  weight: ["400", "500"],
  subsets: ["latin"],
});

const display = Barlow_Condensed({
  variable: "--font-display",
  weight: ["500", "600", "700"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "cdlhub · competitive Call of Duty analytics",
    template: "%s · cdlhub",
  },
  description:
    "Era-adjusted stats, team strength ratings, and evidence-linked analysis for competitive Call of Duty, 2013–2026.",
};

const nav = [
  { href: "/", label: "Overview" },
  { href: "/teams", label: "Teams" },
  { href: "/players", label: "Players" },
  { href: "/stats", label: "Stats" },
  { href: "/rounds", label: "Rounds" },
  { href: "/maps", label: "Maps" },
  { href: "/meta", label: "Loadouts" },
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
      className={`${body.variable} ${mono.variable} ${display.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <header className="sticky top-0 z-30 border-b border-hairline bg-background print:hidden">
          <div className="mx-auto flex max-w-6xl items-baseline gap-8 px-6 py-4">
            <Link
              href="/"
              className="font-display text-2xl font-bold uppercase tracking-tight"
            >
              cdl<span className="text-accent">hub</span>
            </Link>
            <NavLinks items={nav} />
          </div>
        </header>
        <div className="flex-1">{children}</div>
        <SiteFooter />
      </body>
    </html>
  );
}
