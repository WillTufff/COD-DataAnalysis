"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ScopePlayer, ScopeSeason, ScopeTeam } from "@/lib/analytics";
import type { ReportEntity } from "@/lib/reports/resolve";
import {
  type ModeCatalog,
  ALL_MODES_LABEL,
  modeLabel,
  pickLabel,
  seasonLabel,
} from "./cohortLabel";
import { useDismiss } from "./popover";
import { useReportUrl } from "./reportUrl";

/** A row in a chip menu: check mark gutter + label, hover fill. */
const MENU_ROW =
  "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-ink-secondary hover:bg-surface-raised hover:text-ink";

function Check({ on }: { on: boolean }) {
  return (
    <span aria-hidden="true" className={on ? "text-accent" : "text-transparent"}>
      ✓
    </span>
  );
}

/**
 * A filter chip: `Label: value`, opening its menu underneath, with its own ✕
 * when the filter can be removed. Open state is an accent border.
 */
function Chip({
  label,
  value,
  open,
  setOpen,
  onClear,
  children,
}: {
  label: string;
  value: string;
  open: boolean;
  setOpen: (open: boolean) => void;
  onClear?: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), [setOpen]);
  useDismiss({ open, close, container: ref, button: buttonRef });

  return (
    <div
      ref={ref}
      className={`relative inline-flex items-stretch border text-xs transition-colors motion-reduce:transition-none ${
        open ? "border-accent bg-surface-raised" : "border-hairline bg-surface hover:border-accent-dim"
      }`}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="px-2 py-1 text-left"
      >
        <span className="text-ink-muted">{label}:</span>{" "}
        <span className="font-medium text-ink">{value}</span>
      </button>
      {onClear && (
        <button
          type="button"
          aria-label={`Remove the ${label.toLowerCase()} filter`}
          title={`Remove the ${label.toLowerCase()} filter`}
          onClick={onClear}
          className="border-l border-hairline px-1.5 text-ink-muted hover:text-accent"
        >
          ✕
        </button>
      )}
      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 max-w-[calc(100vw-2rem)] border border-hairline bg-surface shadow-lg">
          {children}
        </div>
      )}
    </div>
  );
}

/** What a search menu offers: the slug that rides the URL, the name shown. */
type SearchOption = { slug: string; label: string };

/**
 * The player/team menu: a search over the field, because rosters run to
 * hundreds where seasons run to a handful. Picked entries pin above the search
 * results so the current filter is always visible and un-pickable without
 * retyping a name.
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
    <div className="w-64 max-w-full">
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
        {picked.map((slug) => (
          <button
            key={slug}
            type="button"
            role="menuitemcheckbox"
            aria-checked={true}
            onClick={() => toggle(slug)}
            className={MENU_ROW}
          >
            <Check on />
            <span className="text-ink">{bySlug.get(slug)?.label ?? slug}</span>
          </button>
        ))}
        {picked.length > 0 && <div className="my-1 border-t border-hairline" />}
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
              <Check on={false} />
              {o.label}
            </button>
          ))
        )}
      </div>
    </div>
  );
}

/** The "+ filter" button and its list of filters not yet on the band. */
function AddFilter({
  options,
  onPick,
}: {
  options: { id: RowFilter; label: string }[];
  onPick: (id: RowFilter) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss({ open, close, container: ref, button: buttonRef });
  if (options.length === 0) return null;
  return (
    <div ref={ref} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={`border border-dashed px-2 py-1 text-xs transition-colors motion-reduce:transition-none ${
          open
            ? "border-accent text-accent"
            : "border-hairline text-ink-muted hover:border-accent-dim hover:text-accent"
        }`}
      >
        + filter
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 w-48 border border-hairline bg-surface py-1 shadow-lg">
          {options.map((o) => (
            <button
              key={o.id}
              type="button"
              onClick={() => {
                setOpen(false);
                onPick(o.id);
              }}
              className={MENU_ROW}
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type RowFilter = "season" | "players" | "teams" | "samples";

/** A band: a caps label, its chips, and a one-line note on what it changes. */
function Band({
  label,
  note,
  tinted,
  children,
}: {
  label: string;
  note: string;
  tinted?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`flex flex-col gap-2 px-3 py-2 sm:flex-row sm:items-center ${tinted ? "bg-surface" : ""}`}
    >
      <span className="w-20 shrink-0 font-display text-[0.62rem] font-semibold uppercase tracking-[0.16em] text-ink-muted">
        {label}
      </span>
      <div className="flex flex-1 flex-wrap items-center gap-2">{children}</div>
      <span className="hidden shrink-0 text-[0.66rem] text-ink-muted lg:inline">
        {note}
      </span>
    </div>
  );
}

/**
 * The filters, in two bands. "Maps from" holds the filters that change which
 * maps feed a number, so every value moves with them; today that is the mode.
 * "Show rows" holds the filters that only hide rows of the finished table:
 * season, player, team and small samples. Each change rewrites the URL.
 */
export function FilterBands({
  entity,
  seasons,
  years,
  modes,
  modeCatalog,
  allModes,
  modeSlug,
  players,
  pickedPlayers,
  teams,
  pickedTeams,
  qualifiedOnly,
}: {
  entity: ReportEntity;
  seasons: ScopeSeason[];
  years: number[];
  modes: string[];
  modeCatalog: ModeCatalog;
  allModes: boolean;
  modeSlug?: string;
  players: ScopePlayer[];
  pickedPlayers: string[];
  teams: ScopeTeam[];
  pickedTeams: string[];
  qualifiedOnly: boolean;
}) {
  const push = useReportUrl();
  const [openChip, setOpenChip] = useState<RowFilter | "mode" | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const opener = (id: RowFilter | "mode") => (o: boolean) =>
    setOpenChip(o ? id : null);

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

  // An empty `years` is every season; render it as every box ticked, which is
  // what it means, so toggling one off from that state reads correctly.
  const active = years.length > 0 ? years : seasons.map((s) => s.year);

  function commitYears(next: number[]) {
    const all = next.length === 0 || next.length === seasons.length;
    push({ years: all ? "all" : next.join(","), year: null });
  }

  function toggleYear(year: number) {
    commitYears(
      active.includes(year)
        ? active.filter((y) => y !== year)
        : [...active, year].sort((a, b) => a - b),
    );
  }

  const seasonOn = years.length > 0;
  const playersOn = entity === "players" && pickedPlayers.length > 0;
  const teamsOn = pickedTeams.length > 0;
  const samplesOn = !qualifiedOnly;
  const rowFilterCount = [seasonOn, playersOn, teamsOn, samplesOn].filter(
    Boolean,
  ).length;
  const shown = (id: RowFilter, on: boolean) => on || openChip === id;

  const addable: { id: RowFilter; label: string }[] = [];
  if (!seasonOn && seasons.length > 1) addable.push({ id: "season", label: "Season" });
  if (entity === "players" && !playersOn && players.length > 0) {
    addable.push({ id: "players", label: "Player" });
  }
  if (!teamsOn && teams.length > 0) addable.push({ id: "teams", label: "Team" });
  if (!samplesOn) addable.push({ id: "samples", label: "Include small samples" });

  const modeClearable = modeSlug !== undefined && allModes;
  const summary = [
    modeLabel(modeCatalog, modeSlug, ALL_MODES_LABEL),
    seasonLabel(seasons, years),
  ].join(" · ");

  return (
    <div className="print:hidden">
      <button
        type="button"
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen(!mobileOpen)}
        className="flex w-full items-center justify-between border border-hairline px-3 py-2 text-left text-xs sm:hidden"
      >
        <span className="truncate text-ink-secondary">{summary}</span>
        <span className="shrink-0 pl-3 text-ink-muted">
          Filters{rowFilterCount > 0 ? ` (${rowFilterCount})` : ""}{" "}
          <span aria-hidden="true">{mobileOpen ? "▴" : "▾"}</span>
        </span>
      </button>
      <div
        className={`divide-y divide-hairline border border-hairline max-sm:border-t-0 ${
          mobileOpen ? "" : "max-sm:hidden"
        }`}
      >
        <Band label="Maps from" note="Changes every number" tinted>
          {modes.length > 0 || allModes ? (
            <Chip
              label="Mode"
              value={modeLabel(modeCatalog, modeSlug, ALL_MODES_LABEL)}
              open={openChip === "mode"}
              setOpen={opener("mode")}
              onClear={modeClearable ? () => push({ mode: "" }) : undefined}
            >
              <div className="w-56 max-w-full py-1">
                {allModes && (
                  <button
                    type="button"
                    role="menuitemradio"
                    aria-checked={modeSlug === undefined}
                    onClick={() => {
                      // Empty, not absent: an explicit "combined" has to
                      // outrank a preset's seeded mode.
                      push({ mode: "" });
                      setOpenChip(null);
                    }}
                    className={MENU_ROW}
                  >
                    <Check on={modeSlug === undefined} />
                    {ALL_MODES_LABEL}
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
                      setOpenChip(null);
                    }}
                    className={MENU_ROW}
                  >
                    <Check on={modeSlug === m} />
                    {modeLabel(modeCatalog, m)}
                  </button>
                ))}
              </div>
            </Chip>
          ) : null}
        </Band>

        <Band label="Show rows" note="Hides rows, numbers stay">
          {shown("season", seasonOn) && (
            <Chip
              label="Season"
              value={seasonLabel(seasons, years)}
              open={openChip === "season"}
              setOpen={opener("season")}
              onClear={seasonOn ? () => push({ years: "all", year: null }) : undefined}
            >
              <div className="max-h-80 w-56 max-w-full overflow-y-auto py-1">
                <button
                  type="button"
                  onClick={() => commitYears([])}
                  className={MENU_ROW}
                >
                  <Check on={!seasonOn} />
                  All seasons
                </button>
                <div className="my-1 border-t border-hairline" />
                {[...seasons].reverse().map((s) => {
                  const on = active.includes(s.year);
                  return (
                    <button
                      key={s.year}
                      type="button"
                      role="menuitemcheckbox"
                      aria-checked={on}
                      onClick={() => toggleYear(s.year)}
                      className={MENU_ROW}
                    >
                      <Check on={on} />
                      <span className="font-mono tabular-nums">{s.year}</span>
                      <span className={on ? "text-ink" : ""}>{s.code}</span>
                    </button>
                  );
                })}
              </div>
            </Chip>
          )}

          {entity === "players" && shown("players", playersOn) && (
            <Chip
              label="Player"
              value={pickLabel(pickedPlayers, playerNameBySlug, "players")}
              open={openChip === "players"}
              setOpen={opener("players")}
              onClear={playersOn ? () => push({ players: null }) : undefined}
            >
              <SearchMenu
                options={playerOptions}
                picked={pickedPlayers}
                noun="players"
                commit={(next) =>
                  push({ players: next.length > 0 ? next.join(",") : null })
                }
              />
            </Chip>
          )}

          {shown("teams", teamsOn) && (
            <Chip
              label="Team"
              value={pickLabel(pickedTeams, teamNameBySlug, "teams")}
              open={openChip === "teams"}
              setOpen={opener("teams")}
              onClear={teamsOn ? () => push({ teams: null }) : undefined}
            >
              <SearchMenu
                options={teamOptions}
                picked={pickedTeams}
                noun="teams"
                commit={(next) =>
                  push({ teams: next.length > 0 ? next.join(",") : null })
                }
              />
            </Chip>
          )}

          {samplesOn && (
            <span className="inline-flex items-stretch border border-hairline bg-surface text-xs">
              <span className="px-2 py-1">
                <span className="text-ink-muted">Small samples:</span>{" "}
                <span className="font-medium text-ink">shown</span>
              </span>
              <button
                type="button"
                aria-label="Hide small samples"
                title="Hide small samples"
                onClick={() => push({ all: null })}
                className="border-l border-hairline px-1.5 text-ink-muted hover:text-accent"
              >
                ✕
              </button>
            </span>
          )}

          <AddFilter
            options={addable}
            onPick={(id) =>
              id === "samples" ? push({ all: "1" }) : setOpenChip(id)
            }
          />

          {rowFilterCount > 0 && (
            <button
              type="button"
              onClick={() =>
                push({
                  years: "all",
                  year: null,
                  players: null,
                  teams: null,
                  all: null,
                })
              }
              className="px-1 text-xs text-ink-muted underline-offset-2 hover:text-accent hover:underline"
            >
              Clear
            </button>
          )}
        </Band>
      </div>
    </div>
  );
}
