// The match-context ablation: what each feature family moves against what it
// predicts.
//
// The argument is a comparison and a table makes the reader do it. Five of the
// six families move the leaderboard by something and lower out-of-fold error by
// nothing, which is the signature of a family fitting its own noise. Here the
// bar is the movement and the mark to its right is the change in out-of-fold
// error, on its own scale and signed, so "moved a lot, predicted nothing" is a
// shape rather than two columns to subtract by eye.

export type AblationFamilyView = {
  family: string;
  /** Median leaderboard movement, in cohort standard deviations. */
  move: number;
  /** Median change in out-of-fold RMSE. Negative is better prediction. */
  errorDelta: number;
  improved: number;
  measured: number;
  kept: boolean;
};

export function MovementVsError({
  families,
  width = 640,
}: {
  families: AblationFamilyView[];
  width?: number;
}) {
  if (families.length === 0) return null;

  const ROW = 34;
  const M = { left: 116, right: 196, top: 14, bottom: 26 };
  const H = M.top + families.length * ROW + M.bottom;
  const barWidth = width - M.left - M.right;

  const moveHi = Math.max(...families.map((f) => f.move), 1e-6) * 1.15;
  const x = (v: number) => M.left + (v / moveHi) * barWidth;

  // The error mark sits in the right margin on its own symmetric scale: the
  // two quantities are not in the same units and must not share an axis.
  const errAbs = Math.max(...families.map((f) => Math.abs(f.errorDelta)), 1e-9);
  const errMid = width - M.right + 108;
  const errHalf = 52;
  const ex = (v: number) => errMid + (v / errAbs) * errHalf;

  return (
    <figure>
      <svg
        viewBox={`0 0 ${width} ${H}`}
        className="w-full"
        role="img"
        aria-label={`Leaderboard movement and change in out-of-fold error for each of ${families.length} context feature families`}
      >
        {[0, moveHi / 2, moveHi].map((t) => (
          <g key={t}>
            <line
              x1={x(t)}
              x2={x(t)}
              y1={M.top}
              y2={H - M.bottom}
              stroke="var(--hairline)"
            />
            <text
              x={x(t)}
              y={H - 10}
              textAnchor="middle"
              fontSize={9.5}
              fill="var(--ink-muted)"
              className="font-mono"
            >
              {t.toFixed(2)}
            </text>
          </g>
        ))}

        <line
          x1={errMid}
          x2={errMid}
          y1={M.top}
          y2={H - M.bottom}
          stroke="var(--ink-muted)"
          strokeDasharray="3 3"
        />
        <text
          x={errMid}
          y={H - 10}
          textAnchor="middle"
          fontSize={9.5}
          fill="var(--ink-muted)"
        >
          no change
        </text>

        {families.map((f, i) => {
          const y = M.top + i * ROW + ROW / 2;
          const better = f.errorDelta < 0;
          return (
            <g key={f.family}>
              <title>
                {`${f.family}: moves ${f.move.toFixed(4)} cohort sd, out-of-fold RMSE ${
                  f.errorDelta >= 0 ? "+" : ""
                }${f.errorDelta.toFixed(4)}, lower on ${f.improved} of ${
                  f.measured
                } cohorts`}
              </title>
              <text
                x={M.left - 10}
                y={y + 3.5}
                textAnchor="end"
                fontSize={10.5}
                fill={f.kept ? "var(--ink)" : "var(--ink-secondary)"}
                className="font-mono"
              >
                {f.family}
              </text>
              <rect
                x={M.left}
                y={y - 6}
                width={Math.max(x(f.move) - M.left, 1)}
                height={12}
                rx={3}
                fill={f.kept ? "var(--accent)" : "var(--series-1)"}
                opacity={f.kept ? 0.9 : 0.4}
              />
              {/* A square where prediction improved, a circle where it did not:
                  the state is carried by shape as well as by position. */}
              {better ? (
                <rect
                  x={ex(f.errorDelta) - 4}
                  y={y - 4}
                  width={8}
                  height={8}
                  fill="var(--accent)"
                />
              ) : (
                <circle
                  cx={ex(f.errorDelta)}
                  cy={y}
                  r={4}
                  fill="none"
                  stroke="var(--ink-muted)"
                />
              )}
              <text
                x={width - 6}
                y={y + 3.5}
                textAnchor="end"
                fontSize={9.5}
                fill="var(--ink-muted)"
                className="font-mono"
              >
                {f.improved}/{f.measured}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}
