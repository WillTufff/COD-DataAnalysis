#!/usr/bin/env bash
# Runs the checks CI runs, locally: ruff, mypy and pytest over both Python
# packages, then eslint, tsc and vitest over the site.
#
#   ./scripts/checks.sh [--skip-python] [--skip-web] [--list-stages]
#
# Every check runs even after one fails, and the names of the failures are
# repeated at the end. Excludes what needs a full ingest or a build (the
# real-data suite, `next build`), which stay in CI. The release gates over the
# newest model runs do run here, and only here — CI has no fitted models to
# check — so they report a skip rather than a pass when the database is absent.
set -uo pipefail

cd "$(dirname "$0")/.."
root="$PWD"

PYTHON_PROJECTS=(analytics pipeline)
PYTHON_CHECKS=(lint format types tests)
WEB_CHECKS=(lint types tests e2e)

# Release gates over the newest run of each model: a title with no declared
# rotation, a rating cohort whose variance collapsed, a style basis that moved
# under its published axis names. They read the local database rather than a
# fixture, so unlike everything else here they can be unable to run — that is
# reported as a skip, never as a pass.
ANALYTICS_CHECKS=(gates)

skip_python=0
skip_web=0
list_stages=0
for arg in "$@"; do
  case "$arg" in
    --skip-python) skip_python=1 ;;
    --skip-web) skip_web=1 ;;
    --list-stages) list_stages=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# The stage names, in order, whether or not they end up running. Callers that
# render progress read this instead of hardcoding the list.
if [ "$list_stages" -eq 1 ]; then
  for project in "${PYTHON_PROJECTS[@]}"; do
    for check in "${PYTHON_CHECKS[@]}"; do echo "$project $check"; done
    if [ "$project" = analytics ]; then
      for check in "${ANALYTICS_CHECKS[@]}"; do echo "$project $check"; done
    fi
  done
  for check in "${WEB_CHECKS[@]}"; do echo "web $check"; done
  exit 0
fi

failed=()
skipped=()

# Each check announces itself as a banner and streams its own output indented,
# so a reader can tell them apart.
run() {
  local label="$1" dir="$2"
  shift 2
  echo "== $label =="
  if (cd "$root/$dir" && "$@" 2>&1 | sed 's/^/  /'); then
    return 0
  fi
  failed+=("$label")
  return 1
}

# Like `run`, but the command distinguishes "failed" from "could not run": exit
# 3 means there was nothing to check against, which is recorded as a skip. A
# gate that cannot run has not passed, and reporting it green is the failure
# mode these gates exist to end.
run_gate() {
  local label="$1" dir="$2"
  shift 2
  local status
  echo "== $label =="
  (cd "$root/$dir" && "$@" 2>&1 | sed 's/^/  /')
  status=$?
  case "$status" in
    0) return 0 ;;
    3) skipped+=("$label (no run to check: fit the models first)") ;;
    *) failed+=("$label") ;;
  esac
  return 1
}

python_check() {
  local project="$1" check="$2"
  case "$check" in
    lint)   run "$project $check" "$project" uv run ruff check . ;;
    format) run "$project $check" "$project" uv run ruff format --check . ;;
    types)  run "$project $check" "$project" uv run mypy ;;
    tests)  run "$project $check" "$project" uv run pytest -q ;;
    gates)  run_gate "$project $check" "$project" uv run python -m cdlhub_analytics.gates ;;
  esac
}

web_check() {
  local npm_bin="$1" check="$2"
  case "$check" in
    lint)  run "web $check" web "$npm_bin" run lint ;;
    types) run "web $check" web "$npm_bin" run typecheck ;;
    tests) run "web $check" web "$npm_bin" test ;;
    # Renders the site against the local database and asserts a rating surface
    # per era. Needs a fitted model, so it reports a skip rather than a pass
    # when there is none — an empty page is the failure it exists to catch.
    e2e)   run_gate "web $check" web "$npm_bin" run e2e ;;
  esac
}

# A GUI-launched run inherits no login shell, so npm is looked for where the
# usual installers put it, newest nvm version last.
find_npm() {
  if command -v npm >/dev/null 2>&1; then command -v npm; return; fi
  for candidate in /opt/homebrew/bin/npm /usr/local/bin/npm; do
    [ -x "$candidate" ] && { echo "$candidate"; return; }
  done
  local versions="$HOME/.nvm/versions/node"
  [ -d "$versions" ] || return 1
  local newest
  newest="$(ls -1 "$versions" 2>/dev/null | sort -V | tail -1)"
  [ -n "$newest" ] && [ -x "$versions/$newest/bin/npm" ] && echo "$versions/$newest/bin/npm"
}

if [ "$skip_python" -eq 0 ]; then
  for project in "${PYTHON_PROJECTS[@]}"; do
    if [ ! -d "$root/$project/.venv" ]; then
      skipped+=("$project (no .venv — run 'uv sync' in $project/)")
      continue
    fi
    for check in "${PYTHON_CHECKS[@]}"; do
      python_check "$project" "$check"
    done
    if [ "$project" = analytics ]; then
      for check in "${ANALYTICS_CHECKS[@]}"; do
        python_check "$project" "$check"
      done
    fi
  done
fi

if [ "$skip_web" -eq 0 ]; then
  npm_bin="$(find_npm)"
  if [ -z "$npm_bin" ]; then
    skipped+=("web (npm not found)")
  elif [ ! -d "$root/web/node_modules" ]; then
    skipped+=("web (no node_modules — run 'npm install' in web/)")
  else
    # npm needs the node beside it, which the PATH here may not carry.
    PATH="$(dirname "$npm_bin"):$PATH"
    export PATH
    for check in "${WEB_CHECKS[@]}"; do
      web_check "$npm_bin" "$check"
    done
  fi
fi

echo "== summary =="
for entry in "${skipped[@]:-}"; do
  [ -n "$entry" ] && echo "  skipped $entry"
done
if [ ${#failed[@]} -gt 0 ]; then
  printf '  failed: %s\n' "$(IFS=', '; echo "${failed[*]}")"
  echo "${#failed[@]} check(s) failed"
  exit 1
fi
echo "  all checks passed"
