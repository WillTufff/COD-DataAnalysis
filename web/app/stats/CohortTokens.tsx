"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ScopePlayer, ScopeSeason, ScopeTeam } from "@/lib/analytics";
import type { ReportEntity } from "@/lib/reports/resolve";
import { modeLabel, pickLabel, seasonLabel } from "./cohortLabel";
import { useReportUrl } from "./reportUrl";

/**
 * The framed token: a hairline box on the page's surface with a tiny display-caps
 * label riding above it, opening a menu anchored underneath. Open state is an
 * accent border on a raised fill, so the whole filter area reads as one line of
 * two matching objects rather than a form.
 */
function TokenFrame({
  label,
  value,
  open,
  setOpen,
  children,
}: {
  label: string;
  value: string;
  open: boolean;
  setOpen: (open: boolean) => void;
  children: React.ReactNode;
}) {
  const menuId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: PointerEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, setOpen]);

  return (
    <div ref={ref} className="relative inline-flex flex-col gap-1.5">
      <span className="font-display text-[0.62rem] font-semibold uppercase tracking-[0.16em] text-ink-muted">
        {label}
      </span>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(!open)}
        className={`group inline-flex min-w-40 items-center justify-between gap-5 border px-2.5 py-1.5 text-sm transition-colors motion-reduce:transition-none ${
          open
            ? "border-accent bg-surface-raised"
            : "border-hairline bg-surface hover:border-accent-dim"
        }`}
      >
        <span className="font-medium">{value}</span>
        <span
          aria-hidden="true"
          className={`text-[0.6rem] ${open ? "text-accent" : "text-ink-muted group-hover:text-accent"}`}
        >
          ▼
        </span>
      </button>
      {open && (
        <div
          id={menuId}
          className="absolute left-0 top-full z-20 mt-1 min-w-full border border-hairline bg-surface shadow-lg"
        >
          {children}
        </div>
      )}
    </div>
  );
}

/** A row in a token menu: check mark gutter + label, hover fill. */
const MENU_ROW =
  "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-ink-secondary hover:bg-surface-raised hover:text-ink";

/** What a search menu offers: the slug that rides the URL, the name shown. */
type SearchOption = { slug: string; label: string };

/**
 * The player/team menu: a search over the field, because rosters run to
 * hundreds where seasons run to a handful. Picked entries pin above the search
 * results so the current filter is always visible and un-pickable without
 * retyping a name. Like seasons these are row filters, not cohorts — scoring
 * stays against the whole field — so they multi-select and stay open.
 */
function SearchMenu({
  options,
  picked,
  noun,
  commit,
}: {
  options: SearchOption[];
  picked: string[];
  noun: "players" | "teams";
  commit: (next: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const bySlug = useMemo(
    () => new Map(options.map((o) => [o.slug, o])),
    [options],
  );
  const pickedSet = useMemo(() => new Set(picked), [picked]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pool = options.filter((o) => !pickedSet.has(o.slug));
    if (q === "") return pool;
    return pool.filter((o) => o.label.toLowerCase().includes(q));
  }, [options, pickedSet, query]);

  function toggle(slug: string) {
    commit(
      pickedSet.has(slug) ? picked.filter((s) => s !== slug) : [...picked, slug],
    );
    setQuery("");
    inputRef.current?.focus();
  }

  return (
    <div className="w-64">
      <input
        ref={inputRef}
        type="text"
        value={query}
        placeholder={`Search ${options.length} ${noun}…`}
        aria-label={`Search ${noun}`}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && matches[0]) {
            e.preventDefault();
            toggle(matches[0].slug);
          }
        }}
        className="w-full border-b border-hairline bg-background px-2.5 py-2 text-xs text-ink outline-none"
      />
      <div className="max-h-80 overflow-y-auto py-1">
        <button type="button" onClick={() => commit([])} className={MENU_ROW}>
          <span
            aria-hidden="true"
            className={picked.length === 0 ? "text-accent" : "text-transparent"}
          >
            ✓
          </span>
          All {noun}
        </button>
        {picked.length > 0 && (
          <>
            <div className="my-1 border-t border-hairline" />
            {picked.map((slug) => (
              <button
                key={slug}
                type="button"
                role="menuitemcheckbox"
                aria-checked={true}
                onClick={() => toggle(slug)}
                className={MENU_ROW}
              >
                <span aria-hidden="true" className="text-accent">
                  ✓
                </span>
                <span className="text-ink">
                  {bySlug.get(slug)?.label ?? slug}
                </span>
              </button>
            ))}
          </>
        )}
        <div className="my-1 border-t border-hairline" />
        {matches.length === 0 ? (
          <p className="px-2.5 py-2 text-xs text-ink-muted">
            No {noun.replace(/s$/, "")} matches “{query.trim()}”.
          </p>
        ) : (
          matches.map((o) => (
            <button
              key={o.slug}
              type="button"
              role="menuitemcheckbox"
              aria-checked={false}
              onClick={() => toggle(o.slug)}
              className={MENU_ROW}
            >
              <span aria-hidden="true" className="text-transparent">
                ✓
              </span>
              {o.label}
            </button>
          ))
        )}
      </div>
    </div>
  );
}

/**
 * The cohort: which seasons, and which mode. Seasons combine — a report over
 * three years is one meaningful cohort — so that token is multi-select and its
 * menu stays open while you build the set. Modes never combine, because putting
 * Hardpoint and Search & Destroy in one column is comparing nothing to nothing,
 * so that token is a single pick that closes on choosing.
 *
 * Players and teams are row filters like seasons — the pick narrows the table,
 * never the scoring cohort — so those tokens multi-select and stay open too. A
 * team pick keeps the player-seasons that team actually fielded.
 *
 * Every change applies immediately by rewriting the URL. There is no Apply
 * button: the URL is the state, and a control that edits it has already applied.
 */
export function CohortTokens({
  entity,
  seasons,
  years,
  modes,
  allModes,
  modeSlug,
  players,
  pickedPlayers,
  teams,
  pickedTeams,
}: {
  entity: ReportEntity;
  seasons: ScopeSeason[];
  years: number[];
  modes: string[];
  allModes: boolean;
  modeSlug?: string;
  players: ScopePlayer[];
  pickedPlayers: string[];
  teams: ScopeTeam[];
  pickedTeams: string[];
}) {
  const push = useReportUrl();
  const [openToken, setOpenToken] = useState<
    "rows" | "season" | "mode" | "players" | "teams" | null
  >(null);

  // Switching the row entity swaps the whole metric catalog, so the columns,
  // preset, sort and player filter cannot survive it. Seasons and the team
  // filter carry over — both mean the same thing on either side — and the
  // server re-validates everything anyway.
  function switchEntity(next: ReportEntity) {
    setOpenToken(null);
    if (next === entity) return;
    push({
      entity: next === "teams" ? "teams" : null,
      metrics: null,
      metric: null,
      preset: null,
      sort: null,
      dir: null,
      players: null,
      mode: null,
    });
  }
  const playerOptions = useMemo(
    () => players.map((p) => ({ slug: p.slug, label: p.handle })),
    [players],
  );
  const teamOptions = useMemo(
    () => teams.map((t) => ({ slug: t.slug, label: t.name })),
    [teams],
  );
  const playerNameBySlug = useMemo(
    () => new Map(playerOptions.map((o) => [o.slug, o.label])),
    [playerOptions],
  );
  const teamNameBySlug = useMemo(
    () => new Map(teamOptions.map((o) => [o.slug, o.label])),
    [teamOptions],
  );

  // An empty `years` is "all seasons"; render it as every box ticked, which is
  // what it means, so toggling one off from that state reads correctly.
  const active = years.length > 0 ? years : seasons.map((s) => s.year);

  function commitYears(next: number[]) {
    const all = next.length === 0 || next.length === seasons.length;
    push({ years: all ? null : next.join(","), year: null });
  }

  function toggleYear(year: number) {
    const next = active.includes(year)
      ? active.filter((y) => y !== year)
      : [...active, year].sort((a, b) => a - b);
    commitYears(next);
  }

  return (
    <div className="flex flex-wrap items-end gap-6 print:hidden">
      <TokenFrame
        label="Rows"
        value={entity === "teams" ? "Teams" : "Players"}
        open={openToken === "rows"}
        setOpen={(o) => setOpenToken(o ? "rows" : null)}
      >
        <div className="w-56 py-1">
          {(["players", "teams"] as const).map((e) => (
            <button
              key={e}
              type="button"
              role="menuitemradio"
              aria-checked={entity === e}
              onClick={() => switchEntity(e)}
              className={MENU_ROW}
            >
              <span
                aria-hidden="true"
                className={entity === e ? "text-accent" : "text-transparent"}
              >
                ✓
              </span>
              {e === "teams" ? "Teams" : "Players"}
            </button>
          ))}
        </div>
      </TokenFrame>

      <TokenFrame
        label="Season"
        value={seasonLabel(seasons, years)}
        open={openToken === "season"}
        setOpen={(o) => setOpenToken(o ? "season" : null)}
      >
        <div className="max-h-80 w-56 overflow-y-auto py-1">
          <button
            type="button"
            onClick={() => commitYears([])}
            className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-ink-secondary hover:bg-surface-raised hover:text-ink"
          >
            <span
              aria-hidden="true"
              className={
                years.length === 0 ? "text-accent" : "text-transparent"
              }
            >
              ✓
            </span>
            All seasons
          </button>
          <div className="my-1 border-t border-hairline" />
          {seasons.map((s) => {
            const on = active.includes(s.year);
            return (
              <button
                key={s.year}
                type="button"
                role="menuitemcheckbox"
                aria-checked={on}
                onClick={() => toggleYear(s.year)}
                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-ink-secondary hover:bg-surface-raised hover:text-ink"
              >
                <span
                  aria-hidden="true"
                  className={on ? "text-accent" : "text-transparent"}
                >
                  ✓
                </span>
                <span className="font-mono tabular-nums">{s.year}</span>
                <span className={on ? "text-ink" : ""}>{s.code}</span>
              </button>
            );
          })}
        </div>
      </TokenFrame>

      {(modes.length > 0 || allModes) && (
        <TokenFrame
          label="Mode"
          value={modeLabel(modeSlug)}
          open={openToken === "mode"}
          setOpen={(o) => setOpenToken(o ? "mode" : null)}
        >
          <div className="w-56 py-1">
            {allModes && (
              <button
                type="button"
                role="menuitemradio"
                aria-checked={modeSlug === undefined}
                onClick={() => {
                  // Empty, not absent: an explicit "combined" has to outrank a
                  // preset's seeded mode.
                  push({ mode: "" });
                  setOpenToken(null);
                }}
                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-ink-secondary hover:bg-surface-raised hover:text-ink"
              >
                <span
                  aria-hidden="true"
                  className={
                    modeSlug === undefined ? "text-accent" : "text-transparent"
                  }
                >
                  ✓
                </span>
                All modes combined
              </button>
            )}
            {modes.map((m) => (
              <button
                key={m}
                type="button"
                role="menuitemradio"
                aria-checked={modeSlug === m}
                onClick={() => {
                  push({ mode: m });
                  setOpenToken(null);
                }}
                className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-ink-secondary hover:bg-surface-raised hover:text-ink"
              >
                <span
                  aria-hidden="true"
                  className={
                    modeSlug === m ? "text-accent" : "text-transparent"
                  }
                >
                  ✓
                </span>
                {modeLabel(m)}
              </button>
            ))}
          </div>
        </TokenFrame>
      )}

      {players.length > 0 && (
        <TokenFrame
          label="Players"
          value={pickLabel(pickedPlayers, playerNameBySlug, "players")}
          open={openToken === "players"}
          setOpen={(o) => setOpenToken(o ? "players" : null)}
        >
          <SearchMenu
            options={playerOptions}
            picked={pickedPlayers}
            noun="players"
            commit={(next) =>
              push({ players: next.length > 0 ? next.join(",") : null })
            }
          />
        </TokenFrame>
      )}

      {teams.length > 0 && (
        <TokenFrame
          label="Teams"
          value={pickLabel(pickedTeams, teamNameBySlug, "teams")}
          open={openToken === "teams"}
          setOpen={(o) => setOpenToken(o ? "teams" : null)}
        >
          <SearchMenu
            options={teamOptions}
            picked={pickedTeams}
            noun="teams"
            commit={(next) =>
              push({ teams: next.length > 0 ? next.join(",") : null })
            }
          />
        </TokenFrame>
      )}
    </div>
  );
}
