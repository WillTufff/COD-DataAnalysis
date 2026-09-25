"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import type {
  ScopeSeason,
  ScopeStint,
  ScopeTeam,
  ScopeViewPlayer,
} from "@/lib/analytics";
import type { MapOption } from "@/lib/reports/aggregate";
import {
  type ContentFilters,
  type EventTier,
  type Stage,
  type Venue,
  dateRangeLabel,
  hasContent,
  mapsLabel,
  parseDay,
  stageLabel,
  tierLabel,
  venueLabel,
} from "@/lib/reports/content";
import { fuzzyRank } from "@/lib/reports/fuzzy";
import type { ReportEntity } from "@/lib/reports/resolve";
import { type Threshold, parseView, serializeWhere } from "@/lib/reports/rows";
import {
  type ModeCatalog,
  ALL_MODES_LABEL,
  modeLabel,
  pickLabel,
  seasonLabel,
} from "./cohortLabel";
import { Check, Chip, MENU_ROW } from "./chips";
import { formatThreshold } from "./format";
import { useDismiss } from "./popover";
import { CountMenu, type ThresholdColumn, ThresholdMenu } from "./ResultChips";
import { useReportUrl } from "./reportUrl";

/**
 * What a search menu offers: the slug that rides the URL, the name shown, a
 * line of context beside it, and the extra text a search also matches.
 */
type SearchOption = {
  slug: string;
  label: string;
  context?: string;
  also?: string[];
};

/** Consecutive years as ranges: 2019, 2021–23. */
function yearRanges(years: number[]): string {
  const out: string[] = [];
  let lo = years[0];
  let hi = years[0];
  for (const y of [...years.slice(1), NaN]) {
    if (y === hi + 1) {
      hi = y;
      continue;
    }
    out.push(lo === hi ? String(lo) : `${lo}–${String(hi).slice(-2)}`);
    lo = hi = y;
  }
  return out.join(", ");
}

/**
 * Where a player in view played: team names, with their seasons when the view
 * spans more than one. Two teams at most, then a count.
 */
function stintContext(stints: ScopeStint[], multiYear: boolean): string {
  const shown = stints
    .slice(0, 2)
    .map((st) => (multiYear ? `${st.team} ${yearRanges(st.years)}` : st.team));
  const more = stints.length - shown.length;
  return shown.join(" · ") + (more > 0 ? ` +${more}` : "");
}

/**
 * The player/team menu: a forgiving search over the field, because rosters run
 * to hundreds where seasons run to a handful. Picked entries pin above the
 * search results so the current filter is always visible and un-pickable
 * without retyping a name.
 */
function SearchMenu({
  options,
  picked,
  pickedNames,
  noun,
  commit,
}: {
  options: SearchOption[];
  picked: string[];
  pickedNames: Map<string, string>;
  noun: string;
  commit: (next: string[]) => void;
}) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, []);

  const bySlug = useMemo(
    () => new Map(options.map((o) => [o.slug, o])),
    [options],
  );
  const pickedSet = useMemo(() => new Set(picked), [picked]);

  const matches = useMemo(() => {
    const pool = options.filter((o) => !pickedSet.has(o.slug));
    return fuzzyRank(pool, query, (o) => [o.label, ...(o.also ?? [])]);
  }, [options, pickedSet, query]);

  function toggle(slug: string) {
    commit(
      pickedSet.has(slug) ? picked.filter((s) => s !== slug) : [...picked, slug],
    );
    setQuery("");
    inputRef.current?.focus({ preventScroll: true });
  }

  return (
    <div className="w-72 max-w-full">
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
            <span className="text-ink">
              {bySlug.get(slug)?.label ?? pickedNames.get(slug) ?? slug}
            </span>
            <OptionContext text={bySlug.get(slug)?.context} />
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
              <OptionContext text={o.context} />
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function OptionContext({ text }: { text?: string }) {
  if (!text) return null;
  return (
    <span className="ml-auto truncate pl-2 text-[0.66rem] text-ink-muted">
      {text}
    </span>
  );
}

/** The "+ filter" button and its list of filters not yet on the band. */
function AddFilter<Id extends string>({
  options,
  onPick,
}: {
  options: { id: Id; label: string }[];
  onPick: (id: Id) => void;
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

type RowFilter = "season" | "players" | "teams" | "minmaps" | "where" | "top";
type MapsFilter = "tier" | "venue" | "stage" | "map" | "dates";

const TIERS: { tier: EventTier; note: string }[] = [
  { tier: "1", note: "Championships, CWL Pro League, CDL" },
  { tier: "2", note: "Lower majors and CWL opens" },
];

/** The event tier menu: one tier at a time, as the title rule reads it. */
function TierMenu({
  tier,
  pick,
}: {
  tier: EventTier | null;
  pick: (next: EventTier) => void;
}) {
  return (
    <div className="w-72 max-w-full py-1">
      {TIERS.map((t) => (
        <button
          key={t.tier}
          type="button"
          role="menuitemradio"
          aria-checked={tier === t.tier}
          onClick={() => pick(t.tier)}
          className={`${MENU_ROW} items-start`}
        >
          <Check on={tier === t.tier} />
          <span className="flex flex-col">
            <span className={tier === t.tier ? "text-ink" : ""}>{tierLabel(t.tier)}</span>
            <span className="text-[0.66rem] leading-snug text-ink-muted">{t.note}</span>
          </span>
        </button>
      ))}
      <p className="border-t border-hairline px-2.5 pb-1 pt-1.5 text-[0.66rem] leading-snug text-ink-muted">
        Untiered events (pre-2017 qualifiers and minors) are in neither.
      </p>
    </div>
  );
}

const VENUES: { venue: Venue; note: string }[] = [
  { venue: "lan", note: "In person" },
  { venue: "online", note: "Remote" },
];

/** LAN or online, as each event's venue is recorded. */
function VenueMenu({
  venue,
  pick,
}: {
  venue: Venue | null;
  pick: (next: Venue) => void;
}) {
  return (
    <div className="w-72 max-w-full py-1">
      {VENUES.map((v) => (
        <button
          key={v.venue}
          type="button"
          role="menuitemradio"
          aria-checked={venue === v.venue}
          onClick={() => pick(v.venue)}
          className={`${MENU_ROW} items-start`}
        >
          <Check on={venue === v.venue} />
          <span className="flex flex-col">
            <span className={venue === v.venue ? "text-ink" : ""}>{venueLabel(v.venue)}</span>
            <span className="text-[0.66rem] leading-snug text-ink-muted">{v.note}</span>
          </span>
        </button>
      ))}
      <p className="border-t border-hairline px-2.5 pb-1 pt-1.5 text-[0.66rem] leading-snug text-ink-muted">
        2017–2019 is all LAN. The 2026 regular season is unrecorded and in
        neither.
      </p>
    </div>
  );
}

const STAGE_NOTES: { stage: Stage; note: string }[] = [
  { stage: "league", note: "CWL Pro League, CDL regular season, 2016 CWL stages" },
  { stage: "event", note: "Groups, brackets and finals" },
  { stage: "group", note: "Event groups and pools" },
  { stage: "bracket", note: "Brackets and finals, league playoffs included" },
  { stage: "final", note: "Each bracket's last series" },
];

/** League play or a part of an event, as each series' stage is recorded. */
function StageMenu({
  stage,
  pick,
}: {
  stage: Stage | null;
  pick: (next: Stage) => void;
}) {
  return (
    <div className="w-72 max-w-full py-1">
      {STAGE_NOTES.map((v) => (
        <button
          key={v.stage}
          type="button"
          role="menuitemradio"
          aria-checked={stage === v.stage}
          onClick={() => pick(v.stage)}
          className={`${MENU_ROW} items-start`}
        >
          <Check on={stage === v.stage} />
          <span className="flex flex-col">
            <span className={stage === v.stage ? "text-ink" : ""}>{stageLabel(v.stage)}</span>
            <span className="text-[0.66rem] leading-snug text-ink-muted">{v.note}</span>
          </span>
        </button>
      ))}
      <p className="border-t border-hairline px-2.5 pb-1 pt-1.5 text-[0.66rem] leading-snug text-ink-muted">
        No league before 2016. 2020 home series count as league play.
      </p>
    </div>
  );
}

const DATE_INPUT =
  "w-full border border-hairline bg-background px-2 py-1 font-mono text-xs tabular-nums text-ink outline-none focus:border-accent";

/** Two days, either left open. The range is inclusive, on the series date. */
function DateMenu({
  from,
  to,
  commit,
}: {
  from: string | null;
  to: string | null;
  commit: (from: string | null, to: string | null) => void;
}) {
  const [draftFrom, setDraftFrom] = useState(from ?? "");
  const [draftTo, setDraftTo] = useState(to ?? "");
  const f = parseDay(draftFrom);
  const t = parseDay(draftTo);
  const valid =
    (draftFrom === "" || f !== null) &&
    (draftTo === "" || t !== null) &&
    (f !== null || t !== null);
  return (
    <form
      className="flex w-64 max-w-full flex-col gap-2 p-2.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) commit(f, t);
      }}
    >
      <label className="flex items-center gap-2 text-xs text-ink-muted">
        <span className="w-10 shrink-0">From</span>
        <input
          type="date"
          value={draftFrom}
          onChange={(e) => setDraftFrom(e.target.value)}
          className={DATE_INPUT}
        />
      </label>
      <label className="flex items-center gap-2 text-xs text-ink-muted">
        <span className="w-10 shrink-0">To</span>
        <input
          type="date"
          value={draftTo}
          onChange={(e) => setDraftTo(e.target.value)}
          className={DATE_INPUT}
        />
      </label>
      <p className="text-[0.66rem] leading-snug text-ink-muted">
        Inclusive. Leave one blank for an open range.
      </p>
      <button
        type="submit"
        disabled={!valid}
        className="self-end border border-accent px-2.5 py-1 text-xs text-accent hover:bg-accent hover:text-background disabled:pointer-events-none disabled:opacity-40"
      >
        Set
      </button>
    </form>
  );
}

/** Row limit quick picks. */
const TOP_PICKS = [10, 25, 50, 100];

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

/** The mode menu. Two or more ticked modes combine into one row. */
function ModeMenu({
  modes,
  picked,
  allModes,
  modeCatalog,
  pick,
}: {
  modes: string[];
  picked: string[];
  allModes: boolean;
  modeCatalog: ModeCatalog;
  pick: (next: string[], close: boolean) => void;
}) {
  return (
    <div className="w-64 max-w-full py-1">
      {allModes && (
        <button
          type="button"
          role="menuitemradio"
          aria-checked={picked.length === 0}
          onClick={() => pick([], true)}
          className={MENU_ROW}
        >
          <Check on={picked.length === 0} />
          {ALL_MODES_LABEL}
        </button>
      )}
      <p className="px-2.5 pb-1 pt-1.5 text-[0.66rem] leading-snug text-ink-muted">
        Tick two or more to combine them.
      </p>
      {modes.map((m) => {
        const on = picked.includes(m);
        return (
          <button
            key={m}
            type="button"
            role="menuitemcheckbox"
            aria-checked={on}
            onClick={() =>
              pick(on ? picked.filter((p) => p !== m) : [...picked, m], false)
            }
            className={MENU_ROW}
          >
            <Check on={on} />
            <span className={on ? "text-ink" : ""}>{modeLabel(modeCatalog, m)}</span>
          </button>
        );
      })}
    </div>
  );
}

/**
 * The filters, in two bands. "Maps from" holds the filters that change which
 * maps feed a number, so every value moves with them: the mode (one, or a mix
 * combined into one row), and the seasons when rows are a combined span.
 * "Show rows" holds the filters that only hide rows of the finished table:
 * season (per-season rows), player, team, min maps, value thresholds and top N.
 * Each change rewrites the URL.
 */
export function FilterBands({
  entity,
  seasons,
  years,
  modes,
  modeCatalog,
  allModes,
  modeSlug,
  modeMix,
  span,
  content,
  mapOptions,
  players,
  pickedPlayers,
  playerNames,
  teams,
  pickedTeams,
  mapsFloor,
  minMaps,
  minMapsSet,
  where,
  top,
  columns,
}: {
  entity: ReportEntity;
  seasons: ScopeSeason[];
  years: number[];
  modes: string[];
  modeCatalog: ModeCatalog;
  allModes: boolean;
  modeSlug?: string;
  /** Modes combined into one row; empty unless two or more are picked. */
  modeMix: string[];
  /** Rows are one combined span, so seasons pick maps rather than rows. */
  span: boolean;
  /** Tier, map and date filters on the maps. */
  content: ContentFilters;
  /** The map names in view, busiest first. */
  mapOptions: MapOption[];
  /** The players with rows in view, with their rosters. */
  players: ScopeViewPlayer[];
  pickedPlayers: string[];
  /** Names for picked players, including any not in view. */
  playerNames: Record<string, string>;
  teams: ScopeTeam[];
  pickedTeams: string[];
  mapsFloor: number;
  minMaps: number;
  /** Whether the URL sets min maps, rather than it being the default. */
  minMapsSet: boolean;
  where: Threshold[];
  top: number | null;
  columns: ThresholdColumn[];
}) {
  const push = useReportUrl();
  const searchParams = useSearchParams();
  const [openChip, setOpenChip] = useState<string | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const opener = (id: string) => (o: boolean) => setOpenChip(o ? id : null);
  // The view is read live: the table switches it in place, without a request.
  const view = parseView({ view: searchParams.get("view") ?? "" });

  const multiYear = years.length !== 1;
  const playerOptions = useMemo(
    () =>
      players.map((p) => ({
        slug: p.slug,
        label: p.handle,
        context: stintContext(p.stints, multiYear),
        also: p.stints.map((st) => st.team),
      })),
    [players, multiYear],
  );
  const teamOptions = useMemo(
    () => teams.map((t) => ({ slug: t.slug, label: t.name })),
    [teams],
  );
  const playerNameBySlug = useMemo(
    () => new Map(Object.entries(playerNames)),
    [playerNames],
  );
  const teamNameBySlug = useMemo(
    () => new Map(teamOptions.map((o) => [o.slug, o.label])),
    [teamOptions],
  );
  const columnByKey = useMemo(
    () => new Map(columns.map((c) => [c.key, c])),
    [columns],
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

  function commitWhere(next: Threshold[]) {
    push({ where: serializeWhere(next) });
    setOpenChip(null);
  }

  const seasonOn = years.length > 0;
  const seasonRowFilter = seasonOn && !span;
  const playersOn = entity === "players" && pickedPlayers.length > 0;
  const teamsOn = pickedTeams.length > 0;
  const topOn = top !== null;
  const rowFilterCount =
    [seasonRowFilter, playersOn, teamsOn, minMapsSet, topOn].filter(Boolean).length +
    where.length;
  const shown = (id: RowFilter | MapsFilter, on: boolean) =>
    on || openChip === id;

  const addable: { id: RowFilter; label: string }[] = [];
  if (!span && !seasonOn && seasons.length > 1) {
    addable.push({ id: "season", label: "Season" });
  }
  if (entity === "players" && !playersOn && players.length > 0) {
    addable.push({ id: "players", label: "Player" });
  }
  if (!teamsOn && teams.length > 0) addable.push({ id: "teams", label: "Team" });
  if (columns.length > 0) addable.push({ id: "where", label: "Stat cutoff" });
  if (!topOn) addable.push({ id: "top", label: "Row limit" });

  const modePicked = modeMix.length > 0 ? modeMix : modeSlug ? [modeSlug] : [];
  const modeText =
    modeMix.length > 0
      ? modeMix.map((m) => modeLabel(modeCatalog, m)).join(" + ")
      : modeLabel(modeCatalog, modeSlug, ALL_MODES_LABEL);
  const modeClearable = modePicked.length > 0 && allModes;
  const mapOptionsForMenu = useMemo(
    () =>
      mapOptions.map((m) => ({
        slug: m.slug,
        label: m.name,
        context: `${m.titles.join(", ")} · ${m.maps}`,
      })),
    [mapOptions],
  );
  const mapNameBySlug = useMemo(
    () => new Map(mapOptions.map((m) => [m.slug, m.name])),
    [mapOptions],
  );
  const tierOn = content.tier !== null;
  const venueOn = content.venue !== null;
  const stageOn = content.stage !== null;
  const mapOn = content.maps.length > 0;
  const datesOn = content.from !== null || content.to !== null;
  const contentOn = hasContent(content);
  const addableMaps: { id: MapsFilter; label: string }[] = [];
  if (!tierOn) addableMaps.push({ id: "tier", label: "Event tier" });
  if (!venueOn) addableMaps.push({ id: "venue", label: "LAN / online" });
  if (!stageOn) addableMaps.push({ id: "stage", label: "League / playoffs" });
  if (!mapOn && mapOptions.length > 0) addableMaps.push({ id: "map", label: "Map" });
  if (!datesOn) addableMaps.push({ id: "dates", label: "Date range" });

  const filterCount =
    rowFilterCount + [tierOn, venueOn, stageOn, mapOn, datesOn].filter(Boolean).length;

  const summary = [
    modeText,
    seasonLabel(seasons, years),
    ...(span ? ["combined span"] : []),
    ...(tierOn ? [tierLabel(content.tier!)] : []),
    ...(venueOn ? [venueLabel(content.venue!)] : []),
    ...(stageOn ? [stageLabel(content.stage!)] : []),
    ...(mapOn ? [mapsLabel(content.maps, mapNameBySlug)] : []),
    ...(datesOn ? [dateRangeLabel(content.from, content.to)] : []),
  ].join(" · ");

  function pickModes(next: string[], close: boolean) {
    // Empty, not absent: an explicit "combined" has to outrank a preset's
    // seeded mode. An empty mix with no all-modes rows keeps the last pick.
    if (next.length === 0 && !allModes) return;
    push({ mode: next.join(",") });
    if (close) setOpenChip(null);
  }

  const seasonChip = (
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
  );

  const minMapsPicks = [
    { n: 0, label: "Any" },
    ...[mapsFloor, 20, 40, 80]
      .filter((n, i, a) => n > 0 && a.indexOf(n) === i)
      .map((n) => ({
        n,
        label: n === mapsFloor ? `${n} (published floor)` : String(n),
      })),
  ];

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
          Filters{filterCount > 0 ? ` (${filterCount})` : ""}{" "}
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
              value={modeText}
              open={openChip === "mode"}
              setOpen={opener("mode")}
              onClear={modeClearable ? () => push({ mode: "" }) : undefined}
            >
              <ModeMenu
                modes={modes}
                picked={modePicked}
                allModes={allModes}
                modeCatalog={modeCatalog}
                pick={pickModes}
              />
            </Chip>
          ) : null}
          {span && seasonChip}

          {shown("tier", tierOn) && (
            <Chip
              label="Event tier"
              value={content.tier ? tierLabel(content.tier) : "any"}
              open={openChip === "tier"}
              setOpen={opener("tier")}
              onClear={tierOn ? () => push({ tier: null }) : undefined}
            >
              <TierMenu
                tier={content.tier}
                pick={(next) => {
                  push({ tier: next });
                  setOpenChip(null);
                }}
              />
            </Chip>
          )}

          {shown("venue", venueOn) && (
            <Chip
              label="Venue"
              value={content.venue ? venueLabel(content.venue) : "any"}
              open={openChip === "venue"}
              setOpen={opener("venue")}
              onClear={venueOn ? () => push({ venue: null }) : undefined}
            >
              <VenueMenu
                venue={content.venue}
                pick={(next) => {
                  push({ venue: next });
                  setOpenChip(null);
                }}
              />
            </Chip>
          )}

          {shown("stage", stageOn) && (
            <Chip
              label="Stage"
              value={content.stage ? stageLabel(content.stage) : "any"}
              open={openChip === "stage"}
              setOpen={opener("stage")}
              onClear={stageOn ? () => push({ stage: null }) : undefined}
            >
              <StageMenu
                stage={content.stage}
                pick={(next) => {
                  push({ stage: next });
                  setOpenChip(null);
                }}
              />
            </Chip>
          )}

          {shown("map", mapOn) && (
            <Chip
              label="Map"
              value={mapsLabel(content.maps, mapNameBySlug)}
              open={openChip === "map"}
              setOpen={opener("map")}
              onClear={mapOn ? () => push({ map: null }) : undefined}
            >
              <SearchMenu
                options={mapOptionsForMenu}
                picked={content.maps}
                pickedNames={mapNameBySlug}
                noun="maps"
                commit={(next) => push({ map: next.length > 0 ? next.join(",") : null })}
              />
            </Chip>
          )}

          {shown("dates", datesOn) && (
            <Chip
              label="Dates"
              value={dateRangeLabel(content.from, content.to)}
              open={openChip === "dates"}
              setOpen={opener("dates")}
              onClear={datesOn ? () => push({ from: null, to: null }) : undefined}
            >
              <DateMenu
                from={content.from}
                to={content.to}
                commit={(from, to) => {
                  push({ from, to });
                  setOpenChip(null);
                }}
              />
            </Chip>
          )}

          <AddFilter options={addableMaps} onPick={(id) => setOpenChip(id)} />

          {contentOn && (
            <button
              type="button"
              onClick={() =>
                push({ tier: null, venue: null, stage: null, map: null, from: null, to: null })
              }
              className="px-1 text-xs text-ink-muted underline-offset-2 hover:text-accent hover:underline"
            >
              Clear
            </button>
          )}
        </Band>

        <Band label="Show rows" note="Hides rows, numbers stay">
          {!span && shown("season", seasonOn) && seasonChip}

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
                pickedNames={playerNameBySlug}
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
                pickedNames={teamNameBySlug}
                noun="teams"
                commit={(next) =>
                  push({ teams: next.length > 0 ? next.join(",") : null })
                }
              />
            </Chip>
          )}

          {mapsFloor > 0 && (
            <Chip
              label="Min maps"
              value={minMaps > 0 ? String(minMaps) : "any"}
              open={openChip === "minmaps"}
              setOpen={opener("minmaps")}
              onClear={minMapsSet ? () => push({ minmaps: null, all: null }) : undefined}
            >
              <CountMenu
                value={minMaps}
                picks={minMapsPicks}
                suffix="maps"
                inputLabel="Minimum maps"
                commit={(n) => {
                  push({ minmaps: String(n ?? 0), all: null });
                  setOpenChip(null);
                }}
              />
            </Chip>
          )}

          {where.map((t, i) => {
            const col = columnByKey.get(t.metric);
            if (!col) return null;
            const id = `where-${i}`;
            return (
              <Chip
                key={`${t.metric}:${t.field}:${t.op}`}
                label={col.label}
                value={`${t.op === "gte" ? "≥" : "≤"} ${formatThreshold(t.n, t.field, col.unit)}`}
                open={openChip === id}
                setOpen={opener(id)}
                onClear={() => commitWhere(where.filter((_, j) => j !== i))}
              >
                <ThresholdMenu
                  columns={columns}
                  initial={t}
                  commit={(next) =>
                    commitWhere(where.map((w, j) => (j === i ? next : w)))
                  }
                  remove={() => commitWhere(where.filter((_, j) => j !== i))}
                />
              </Chip>
            );
          })}

          {openChip === "where" && columns.length > 0 && (
            <Chip
              label="Cutoff"
              value="new"
              open
              setOpen={opener("where")}
            >
              <ThresholdMenu
                columns={columns}
                initial={{ metric: columns[0].key, field: view, op: "gte", n: 0 }}
                commit={(next) => commitWhere([...where, next])}
              />
            </Chip>
          )}

          {shown("top", topOn) && (
            <Chip
              label="Show top"
              value={top !== null ? String(top) : "all"}
              open={openChip === "top"}
              setOpen={opener("top")}
              onClear={topOn ? () => push({ top: null }) : undefined}
            >
              <CountMenu
                value={top}
                picks={[
                  { n: null, label: "all" },
                  ...TOP_PICKS.map((n) => ({ n, label: String(n) })),
                ]}
                noneLabel="All rows"
                suffix="rows"
                inputLabel="Rows to keep"
                commit={(n) => {
                  push({ top: n !== null && n > 0 ? String(n) : null });
                  setOpenChip(null);
                }}
              />
            </Chip>
          )}

          <AddFilter options={addable} onPick={(id) => setOpenChip(id)} />

          {rowFilterCount > 0 && (
            <button
              type="button"
              onClick={() =>
                push({
                  ...(span ? {} : { years: "all", year: null }),
                  players: null,
                  teams: null,
                  minmaps: null,
                  all: null,
                  where: null,
                  top: null,
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
