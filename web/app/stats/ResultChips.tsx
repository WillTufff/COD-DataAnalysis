"use client";

import { useEffect, useRef, useState } from "react";
import { REPORT_VIEWS, type ReportView, type Threshold } from "@/lib/reports/rows";
import { Check, MENU_ROW } from "./chips";

/** A column a threshold can name: its key, label and unit. */
export type ThresholdColumn = { key: string; label: string; unit: string };

const INPUT =
  "w-full border border-hairline bg-background px-2 py-1 font-mono text-xs tabular-nums text-ink outline-none focus:border-accent";

const SEGMENT = (on: boolean) =>
  `flex-1 border px-2 py-1 text-xs transition-colors motion-reduce:transition-none ${
    on
      ? "border-accent bg-surface-raised text-ink"
      : "border-hairline text-ink-muted hover:text-ink"
  }`;

const APPLY =
  "border border-accent px-2.5 py-1 text-xs text-accent hover:bg-accent hover:text-background disabled:pointer-events-none disabled:opacity-40";

/**
 * A count picker: a few quick picks, then a number box for anything else.
 * `null` in the quick picks is the "none" choice, labelled by `noneLabel`.
 */
export function CountMenu({
  value,
  picks,
  noneLabel,
  suffix,
  inputLabel,
  commit,
}: {
  value: number | null;
  picks: { n: number | null; label: string }[];
  noneLabel?: string;
  suffix?: string;
  inputLabel: string;
  commit: (n: number | null) => void;
}) {
  const [draft, setDraft] = useState(value !== null && value > 0 ? String(value) : "");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
    inputRef.current?.select();
  }, []);
  const parsed = /^\d+$/.test(draft.trim()) ? Number(draft.trim()) : null;

  return (
    <div className="w-52 max-w-full py-1">
      {picks.map((p) => (
        <button
          key={p.label}
          type="button"
          role="menuitemradio"
          aria-checked={value === p.n}
          onClick={() => commit(p.n)}
          className={MENU_ROW}
        >
          <Check on={value === p.n} />
          {p.n === null ? noneLabel : p.label}
        </button>
      ))}
      <form
        className="flex items-center gap-2 border-t border-hairline px-2.5 pb-1.5 pt-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (parsed !== null) commit(parsed);
        }}
      >
        <input
          ref={inputRef}
          inputMode="numeric"
          aria-label={inputLabel}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className={INPUT}
        />
        {suffix && <span className="shrink-0 text-xs text-ink-muted">{suffix}</span>}
        <button type="submit" disabled={parsed === null} className={APPLY}>
          Set
        </button>
      </form>
    </div>
  );
}

/** A number as a threshold box shows it: shares in percent, as typed. */
function toInput(n: number, field: ReportView, unit: string): string {
  if (field === "value" && unit.startsWith("share")) return String(Number((n * 100).toFixed(4)));
  return String(n);
}

/** A threshold box's text back to URL units, or null when it is not a number. */
function fromInput(raw: string, field: ReportView, unit: string): number | null {
  const t = raw.trim();
  if (t === "" || !Number.isFinite(Number(t))) return null;
  const n = Number(t);
  return field === "value" && unit.startsWith("share") ? n / 100 : n;
}

/**
 * The editor behind a threshold chip, and behind "+ filter → Value threshold".
 * A new threshold starts on the first column on screen and the field the table
 * is showing, which is the reading the reader is looking at when they add one.
 */
export function ThresholdMenu({
  columns,
  initial,
  commit,
  remove,
}: {
  columns: ThresholdColumn[];
  initial: Threshold;
  commit: (t: Threshold) => void;
  remove?: () => void;
}) {
  const [metric, setMetric] = useState(initial.metric);
  const [field, setField] = useState<ReportView>(initial.field);
  const [op, setOp] = useState(initial.op);
  const col = columns.find((c) => c.key === metric) ?? columns[0];
  const [draft, setDraft] = useState(
    remove ? toInput(initial.n, initial.field, col?.unit ?? "") : "",
  );
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus({ preventScroll: true });
  }, []);
  if (!col) return null;
  const n = fromInput(draft, field, col.unit);
  const hint =
    field === "pctl"
      ? "0–100"
      : field === "z"
        ? "σ from the mean"
        : col.unit.startsWith("share")
          ? "percent"
          : col.unit;

  return (
    <form
      className="flex w-64 max-w-full flex-col gap-2 p-2.5"
      onSubmit={(e) => {
        e.preventDefault();
        if (n !== null) commit({ metric: col.key, field, op, n });
      }}
    >
      <label className="flex flex-col gap-1 text-[0.66rem] text-ink-muted">
        Column
        <select
          value={col.key}
          onChange={(e) => setMetric(e.target.value)}
          className="border border-hairline bg-background px-1.5 py-1 text-xs text-ink outline-none focus:border-accent"
        >
          {columns.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <div className="flex gap-1" role="radiogroup" aria-label="Reading">
        {REPORT_VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            role="radio"
            aria-checked={field === v.id}
            onClick={() => setField(v.id)}
            className={SEGMENT(field === v.id)}
          >
            {v.id === "z" ? "z" : v.label}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-1">
        <div className="flex gap-1" role="radiogroup" aria-label="Direction">
          {(["gte", "lte"] as const).map((o) => (
            <button
              key={o}
              type="button"
              role="radio"
              aria-checked={op === o}
              aria-label={o === "gte" ? "at least" : "at most"}
              onClick={() => setOp(o)}
              className={`${SEGMENT(op === o)} w-8 flex-none font-mono`}
            >
              {o === "gte" ? "≥" : "≤"}
            </button>
          ))}
        </div>
        <input
          ref={inputRef}
          inputMode="decimal"
          aria-label={`${col.label} threshold`}
          placeholder={hint}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className={INPUT}
        />
      </div>
      <div className="flex items-center justify-between gap-2">
        {remove ? (
          <button
            type="button"
            onClick={remove}
            className="text-xs text-ink-muted underline-offset-2 hover:text-accent hover:underline"
          >
            Remove
          </button>
        ) : (
          <span />
        )}
        <button type="submit" disabled={n === null} className={APPLY}>
          {remove ? "Update" : "Add"}
        </button>
      </div>
    </form>
  );
}
